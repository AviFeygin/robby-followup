// One page, three ways to reach the engine. All of them answer call(name, kwargs)
// with the same { ok, data | error } shape that followup/api.py returns.
(function () {
  "use strict";

  const PYODIDE_VERSION = "0.26.4";
  const PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v" + PYODIDE_VERSION + "/full/";

  // Localhost: the page talks to FastAPI, which calls api.py.
  class HttpTransport {
    constructor() { this.label = "local server"; this.live = true; }
    async call(name, kwargs) {
      const res = await fetch("/api/" + name, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(kwargs || {}),
      });
      return res.json();
    }
    async reset() { await fetch("/api/reset", { method: "POST" }); }
  }

  // Website: the same Python packages run in the browser, with SQLite in memory.
  class PyodideTransport {
    constructor(py) { this.py = py; this.label = "engine in your browser"; this.live = true; }
    static async create(bundle, onStatus) {
      onStatus("Loading Python for the browser.");
      await loadScript(PYODIDE_BASE + "pyodide.js");
      const py = await window.loadPyodide({ indexURL: PYODIDE_BASE });
      onStatus("Loading SQLite and SQLAlchemy.");
      await py.loadPackage(["sqlite3", "sqlalchemy"]);
      onStatus("Starting the engine.");
      for (const [path, text] of Object.entries(bundle.files)) {
        const full = "/app/" + path;
        const dir = full.slice(0, full.lastIndexOf("/"));
        py.FS.mkdirTree(dir);
        py.FS.writeFile(full, text);
      }
      py.runPython([
        "import sys, json",
        "sys.path.insert(0, '/app')",
        "from followup.bootstrap import demo_engine",
        "from followup.api import call as _call",
        "ENGINE = demo_engine()",
        "def api(name, kwargs_json):",
        "    return json.dumps(_call(ENGINE, name, json.loads(kwargs_json)))",
        "def reset():",
        "    global ENGINE",
        "    ENGINE = demo_engine()",
      ].join("\n"));
      return new PyodideTransport(py);
    }
    async call(name, kwargs) {
      const api = this.py.globals.get("api");
      try { return JSON.parse(api(name, JSON.stringify(kwargs || {}))); }
      finally { api.destroy(); }
    }
    async reset() { this.py.runPython("reset()"); }
  }

  // Fallback when Python cannot load: a recorded run from build time, read only.
  class RecordedTransport {
    constructor(recorded) { this.recorded = recorded; this.label = "recorded run"; this.live = false; }
    async call(name, kwargs) {
      const key = name + ":" + ((kwargs && kwargs.shop_id) || "");
      if (this.recorded[key]) return { ok: true, data: this.recorded[key] };
      if (this.recorded[name]) return { ok: true, data: this.recorded[name] };
      return { ok: false, error: { code: "recorded", message: "This is a recorded run. The live engine did not load, so this action is not available." } };
    }
    async reset() {}
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = () => reject(new Error("could not load " + src));
      document.head.appendChild(s);
    });
  }

  async function connect(onStatus) {
    const bundle = window.FOLLOWUP_BUNDLE;
    if (!bundle) return new HttpTransport();
    try {
      return await PyodideTransport.create(bundle, onStatus);
    } catch (err) {
      console.error(err);
      return new RecordedTransport(bundle.recorded || {});
    }
  }

  window.FollowupTransport = { connect, loadScript };
})();
