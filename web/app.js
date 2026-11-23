// The page. Every number on it comes from followup/api.py through the transport.
(function () {
  "use strict";

  const DEFAULT_NOW = "2026-08-17T14:00:00Z";
  const SHOP_OFFSET_H = -4;
  // The brief's walkthrough first, then the parts that go past it.
  const TABS = [
    ["start", "Start here"], ["queue", "Queue"], ["policy", "Policy"], ["review", "Review and send"],
    ["messy", "Messy input"], ["shops", "Two shops"], ["tests", "Tests"],
    ["|", "Beyond the brief"], ["sim", "Live simulation"], ["capacity", "Capacity and offers"], ["guard", "Guard"], ["architecture", "Architecture"], ["design", "Design doc"],
  ];
  const CLOCK_TABS = ["queue", "policy", "review", "messy", "shops", "capacity", "guard"];
  const S = {
    shop: "shop-a", now: DEFAULT_NOW, view: "start", quote: null, draft: null, data: null, detail: null,
    log: [], editing: null, role: "owner", guardText: "", guardResult: null, guardRun: null, messy: null,
    other: null, leak: null, arch: "ING", opening: false, design: null, tests: null, range: null,
  };
  let T = null;
  let refreshTimer = null;

  // ------------------------------------------------------------------ helpers
  const $ = (sel, el) => (el || document).querySelector(sel);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (cents) => "$" + Math.round(cents / 100).toLocaleString("en-US");
  const ms = (iso) => Date.parse(iso);
  const toIso = (t) => new Date(t).toISOString().replace(/\.\d{3}Z$/, "Z");
  function when(iso) {
    if (!iso) return "";
    const d = new Date(ms(iso) + SHOP_OFFSET_H * 3600e3);
    const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getUTCMonth()];
    return mon + " " + d.getUTCDate() + ", " + String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0");
  }
  const hours = (h) => (h < 48 ? Math.round(h) + " h" : (h / 24).toFixed(1) + " d");
  const tier = (n) => (n ? '<span class="tier t' + n + '">' + n + "</span>" : "");
  const WHY = {
    closed: "Quote is closed", cooldown: "Customer in cooldown", backoff: "In backoff", not_due_yet: "Not due yet",
    hold_fresh: "Fresh quote hold, under 24 h", hold_live_conversation: "Live conversation hold", gone_quiet: "Attempt cap reached",
    live_draft: "Draft already in review", snoozed: "Snoozed after rejection", other_quote_chosen: "Other quote selected for this customer",
  };
  const STATUS_BADGE = { draft: "info", approved: "ok", sending: "warn", sent: "ok", failed: "bad", rejected: "", expired: "", skipped: "warn" };

  async function api(name, kwargs, quiet) {
    const res = await T.call(name, kwargs || {});
    if (!res.ok && !quiet) note(res.error.message, "bad");
    return res;
  }
  function note(text, kind) {
    S.log.unshift({ text, kind: kind || "", at: new Date().toLocaleTimeString() });
    S.log = S.log.slice(0, 8);
  }

  async function refresh() {
    const res = await api("overview", { shop_id: S.shop, now: S.now });
    if (!res.ok) { render(); return; }
    S.data = res.data;
    if (S.quote && !S.data.quotes.some((q) => q.id === S.quote)) S.quote = null;
    if (!S.quote) S.quote = S.data.candidates.length ? S.data.candidates[0].quote_id : (S.data.quotes[0] || {}).id || null;
    S.detail = null;
    if (S.quote) {
      const d = await api("quote_detail", { shop_id: S.shop, quote_id: S.quote, now: S.now }, true);
      if (d.ok) S.detail = d.data;
    }
    if (S.draft && !S.data.drafts.some((d) => d.id === S.draft)) S.draft = null;
    render();
  }
  function refreshSoon() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 90); }

  // ------------------------------------------------------------------- render
  function render() {
    $("#tabs").innerHTML = TABS.map(([id, label]) => id === "|" ? '<span class="tab-sep">' + label + "</span>" :
      '<button data-tab="' + id + '" class="' + (S.view === id ? "on" : "") + '">' + label + "</button>").join("");
    $("#now-label").textContent = when(S.now) + " shop time";
    $("#clock").hidden = !CLOCK_TABS.includes(S.view);   // the clock only matters where the engine reads "now"
    const view = VIEWS[S.view] || VIEWS.queue;
    $("#view").innerHTML = S.data || ["start", "sim", "design", "tests", "architecture"].includes(S.view) ? view() : "";
    if (S.view === "design") renderDesign();
    if (S.view === "tests") loadTests();
  }
  function logHtml() {
    if (!S.log.length) return "";
    return "<h3>Action log</h3>" + S.log.map((l) => '<div class="note ' + l.kind + '"><span class="muted small">' + esc(l.at) + "</span> " + esc(l.text) + "</div>").join("");
  }

  const VIEWS = {};

  // -------------------------------------------------------------------- queue
  VIEWS.start = function () {
    const live = S.data ? '<div class="row" style="margin:4px 0 0"><span class="badge">' + S.data.quotes.length + ' quotes in the seed</span><span class="badge info">' +
      S.data.candidates.length + ' to contact now</span><span class="badge">' + S.data.drafts.length + " drafts</span></div>" : "";
    const go = (id, label) => '<button class="small" data-tab="' + id + '">' + label + "</button>";
    const row = (what, where, how) => "<tr><td><b>" + what + "</b></td><td>" + how + "</td><td>" + where + "</td></tr>";
    const step = (n, id, label, text) => '<li><span class="step-n">' + n + "</span><div><div>" + go(id, label) + '</div><div class="muted">' + text + "</div></div></li>";
    return '<div class="start">' +
      '<div class="card"><h2>Follow-up engine</h2>' +
      "<p>Follow-up engine for open home-service quotes. It ranks open quotes by contact priority, drafts a message for each candidate, and sends only after human approval.</p>" +
      '<p class="muted">All values are computed by the Python engine (followup/) on the seed data. On the website the engine runs client-side under Pyodide; no data leaves the page. Default evaluation time: 2026-08-17 14:00 UTC, after the last seed event. The shop clock sets the evaluation time.</p>' + live + "</div>" +

      '<div class="card"><h2>Grading criteria and where each is exercised</h2><table class="start-table"><thead><tr><th>Area</th><th>What to try</th><th></th></tr></thead><tbody>' +
      row("Correctness under messy input", go("messy", "Messy input"), "Replays the event stream shuffled and with duplicates. Quote state, medians and queue are identical.") +
      row("Safety guardrails", go("review", "Review and send"), "A double send produces one outbox row. A quote accepted after approval is refused at send time.") +
      row("Quote and message state", go("policy", "Policy"), "Decision path of one quote through filters and tiers, with the reason.") +
      row("Judgment calls", go("design", "Design doc"), "README: policy rationale, 50-shop scaling, scope cut, next steps. Design doc: rationale per component.") +
      "</tbody></table></div>" +

      '<div class="card"><h2>Suggested order</h2><ol class="tour">' +
      step(1, "queue", "Queue", "Ranked candidates at the evaluation time, with reasons.") +
      step(2, "policy", "Policy", "Decision path for one quote. Thresholds derived from shop medians.") +
      step(3, "review", "Review and send", "Draft lifecycle: run, approve, send, duplicate send, timeout, retry.") +
      step(4, "messy", "Messy input", "Order and duplicate independence of the event log.") +
      step(5, "shops", "Two shops", "Cross-tenant access with colliding ids. Refused.") +
      "</ol></div>" +

      '<div class="grid even"><div class="card"><h2>Beyond the brief</h2><p class="muted">Outside the brief. Mechanisms are implemented; parameter values are demo assumptions.</p><ul class="plain">' +
      "<li>" + go("sim", "Live simulation") + " Engine driven by a synthetic quote stream. Bookings rise; offer size falls with slack.</li>" +
      "<li>" + go("capacity", "Capacity and offers") + " Offer sizing from schedule slack. Premium package as a price anchor.</li>" +
      "<li>" + go("guard", "Guard") + " Deterministic text validation. Rejects any price, date, link or name not set by the policy.</li>" +
      "<li>" + go("architecture", "Architecture") + " Module map and data flow for one event.</li>" +
      "<li>" + go("design", "Design doc") + " Full specification.</li></ul></div>" +
      '<div class="card"><h2>The submission</h2><p class="muted">Code, tests, README and the agent transcript are in the submission archive.</p><ul class="plain">' +
      "<li>" + go("tests", "Tests") + " Test results by risk area, including Postgres row level security.</li></ul>" +
      '<p class="muted small">To run it locally: <code>pip install -r requirements.txt</code>, then <code>python -m followup.cli serve</code>.</p></div></div>' +
      "</div>";
  };

  VIEWS.queue = function () {
    const d = S.data;
    const rows = d.candidates.map((c) =>
      '<tr class="pick ' + (c.quote_id === S.quote ? "sel" : "") + '" data-quote="' + c.quote_id + '"><td class="num">' + c.rank + "</td><td>" + tier(c.tier) +
      "</td><td><b>" + esc(c.customer_name) + '</b><div class="muted small">' + c.quote_id + " · " + esc(c.tech_name) + '</div></td><td class="num">' + money(c.amount_cents) +
      "</td><td>" + esc(c.strategy) + (c.framing ? '<div class="muted small">' + esc(c.framing.kind) + "</div>" : "") +
      (c.offer && c.offer.value_cents ? '<div><span class="badge warn">' + money(c.offer.value_cents) + " off the quote</span></div>" : "") +
      "</td><td>" + esc(c.reason) + "</td></tr>").join("");
    const counts = {};
    d.excluded.forEach((e) => { counts[e.why] = (counts[e.why] || 0) + 1; });
    const excluded = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([why, n]) => "<tr><td class=\"num\">" + n + "</td><td>" + esc(WHY[why] || why) + "</td></tr>").join("");
    const leads = d.excluded.filter((e) => e.manual_lead).map((e) => '<div class="note warn">' + esc(e.customer_name) + " reopened a dismissed quote (" + e.quote_id + ", " + money(e.amount_cents) + "). No draft generated. Flagged for manual follow-up.</div>").join("");
    return '<div class="grid"><div><div class="card"><div class="row"><h2>Candidate queue</h2><span class="spacer"></span>' +
      '<span class="badge">' + d.counts.open + " open quotes</span><span class=\"badge info\">" + d.candidates.length + " to contact</span></div>" +
      '<p class="lead muted">Recomputed from the event log at the shop clock time.</p>' + leads +
      '<div class="scroll"><table><thead><tr><th class="num">#</th><th>Tier</th><th>Customer</th><th class="num">Quote</th><th>Message</th><th>Why now</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="6" class="muted">Nobody is due at this time.</td></tr>') + "</tbody></table></div></div>" +
      '<div class="card" style="margin-top:14px"><h2>Event timeline, all quotes</h2>' +
      '<div class="legend"><span' + I("timeline:view") + '><i style="background:var(--view)"></i>viewed</span><span' + I("timeline:reply") + '><i style="background:var(--reply)"></i>customer replied</span>' +
      '<span' + I("timeline:contact") + '><i style="background:var(--contact)"></i>contacted</span><span' + I("timeline:sent") + '><i style="background:var(--muted)"></i>quote sent</span><span' + I("timeline:hot") + '>shaded band: hot window</span><span' + I("timeline:now") + '>red line: now</span></div>' +
      '<div class="scroll">' + swimlane() + "</div>" + infoBox("timeline") + "</div></div>" +
      '<div><div class="card">' + quotePanel() + '</div><div class="card" style="margin-top:14px"><h2>Not contacted</h2><table><tbody>' + excluded + "</tbody></table></div></div></div>";
  };

  function quotePanel() {
    const q = S.detail;
    if (!q) return '<h2>Quote</h2><p class="muted">Select a row.</p>';
    const c = q.candidate, e = q.excluded, s = q.signals;
    const verdict = c ? '<div class="note ok">' + tier(c.tier) + " " + esc(c.reason) + "</div>" + (c.approver_note ? '<div class="note">' + esc(c.approver_note) + "</div>" : "")
      : e ? '<div class="note warn"><b>Not contacted.</b> ' + esc(WHY[e.why] || e.why) + ": " + esc(e.detail) + "</div>" : "";
    const sig = [
      ["Awaiting our reply", s.awaiting_our_reply], ["Viewed since contact", s.viewed_since_contact + " (" + s.views_since_contact + " views)"],
      ["View burst", s.view_burst], ["Returned after a gap", s.returned_after_gap], ["Never viewed", s.never_viewed],
      ["Unanswered attempts", s.unanswered_attempts], ["Since last contact", hours(s.hours_since_contact)],
      ["Size against shop median", s.amount_ratio + " times"], ["Hold", s.hold || "none"],
    ].map(([k, v]) => "<dt>" + k + "</dt><dd>" + esc(v) + "</dd>").join("");
    const icon = { quote_viewed: "var(--view)", customer_replied: "var(--reply)", message_sent: "var(--contact)", quote_sent: "var(--muted)", quote_accepted: "var(--ok)" };
    const tl = q.timeline.filter((t) => !t.future).map((t) => '<tr><td class="small mono">' + when(t.at) + '</td><td><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' +
      (icon[t.type] || "var(--muted)") + '"></i> ' + esc(t.type.replace(/_/g, " ")) + (t.channel ? ' <span class="muted small">' + esc(t.channel) + "</span>" : "") + "</td></tr>").join("");
    return "<h2>" + esc(q.quote.customer_name) + ' <span class="muted">' + q.quote.id + "</span></h2>" +
      '<div class="row"><span class="badge">' + money(q.quote.amount_cents) + '</span><span class="badge">' + esc(q.quote.tech_name) + '</span><span class="badge ' +
      (q.quote.status === "open" ? "ok" : "") + '">' + q.quote.status + "</span></div>" + verdict +
      '<div class="row" style="margin-top:6px"><button class="small" data-act="goto" data-view="policy">See its path through the policy</button></div>' +
      "<h3>Signals</h3><dl class=\"kv\">" + sig + "</dl><h3>Events</h3><table><tbody>" + tl + "</tbody></table>";
  }

  function swimlane() {
    const d = S.data, rowH = 17, left = 150, right = 90, W = 860, chart = W - left - right;
    const order = {};
    d.candidates.forEach((c) => { order[c.quote_id] = c.rank; });
    const quotes = d.quotes.slice().sort((a, b) => (order[a.id] || 999) - (order[b.id] || 999) || a.id.localeCompare(b.id));
    const now = ms(d.now);
    const t0 = Math.min.apply(null, d.quotes.map((q) => ms(q.created_at)).concat([now])) - 12 * 3600e3;
    const t1 = Math.max(now, d.time_range.last ? ms(d.time_range.last) : now) + 18 * 3600e3;
    const x = (t) => left + ((t - t0) / (t1 - t0)) * chart;
    const H = quotes.length * rowH + 30;
    let out = '<svg viewBox="0 0 ' + W + " " + H + '" width="100%" style="min-width:640px" role="img" aria-label="Timeline of every quote">';
    const hot0 = now - d.thresholds.hot_window_h * 3600e3;
    out += '<rect x="' + x(hot0) + '" y="18" width="' + (x(now) - x(hot0)) + '" height="' + (H - 22) + '" fill="var(--accent-soft)"' + I("timeline:hot") + "/>";
    for (let t = Math.ceil(t0 / 864e5) * 864e5; t < t1; t += 2 * 864e5) {
      const dt = new Date(t);
      out += '<line x1="' + x(t) + '" x2="' + x(t) + '" y1="18" y2="' + H + '" stroke="var(--line)" stroke-width="0.6"/>' +
        '<text class="dim" x="' + x(t) + '" y="12" font-size="9" text-anchor="middle">' + (dt.getUTCMonth() + 1) + "/" + dt.getUTCDate() + "</text>";
    }
    quotes.forEach((q, i) => {
      const y = 26 + i * rowH, sel = q.id === S.quote;
      out += '<g data-quote="' + q.id + '" style="cursor:pointer">' +
        '<rect x="0" y="' + (y - 8) + '" width="' + W + '" height="' + rowH + '" fill="' + (sel ? "var(--accent-soft)" : "transparent") + '"/>' +
        '<text x="4" y="' + (y + 3) + '" font-size="10"' + (q.status !== "open" ? ' class="dim"' : "") + ">" + esc(q.id + " " + q.customer_name) + "</text>" +
        '<line x1="' + x(ms(q.created_at)) + '" x2="' + x(Math.min(now, q.accepted_at ? ms(q.accepted_at) : now)) + '" y1="' + y + '" y2="' + y +
        '" stroke="var(--line)" stroke-width="' + (q.status === "open" ? 2 : 1) + '"' + I("timeline:line") + "/>" +
        '<rect x="' + (x(ms(q.created_at)) - 2) + '" y="' + (y - 4) + '" width="4" height="8" fill="var(--muted)"' + I("timeline:sent") + "/>";
      q.contacts.forEach((t) => { out += '<path d="M' + x(ms(t)) + " " + (y - 5) + " l5 9 h-10 z\" fill=\"var(--contact)\"" + I("timeline:contact") + "/>"; });
      q.views.forEach((t) => { out += '<circle cx="' + x(ms(t)) + '" cy="' + y + '" r="3.4" fill="var(--view)"' + I("timeline:view") + "/>"; });
      q.replies.forEach((t) => { out += '<rect x="' + (x(ms(t)) - 3.5) + '" y="' + (y - 3.5) + '" width="7" height="7" transform="rotate(45 ' + x(ms(t)) + " " + y + ')" fill="var(--reply)"' + I("timeline:reply") + "/>"; });
      const label = q.outcome === "candidate" ? "tier " + q.tier : q.status !== "open" ? q.status : (WHY[q.why] || q.why || "").toLowerCase();
      out += '<text' + I("timeline:label") + ' x="' + (W - right + 6) + '" y="' + (y + 3) + '" font-size="9.5" fill="' + (q.outcome === "candidate" ? "var(--tier" + q.tier + ")" : "var(--muted)") + '"' +
        (q.outcome === "candidate" ? ' font-weight="700"' : "") + ">" + esc(label.slice(0, 18)) + "</text></g>";
    });
    out += '<line x1="' + x(now) + '" x2="' + x(now) + '" y1="14" y2="' + H + '" stroke="var(--bad)" stroke-width="1.5"' + I("timeline:now") + "/>" +
      '<text' + I("timeline:now") + ' x="' + (x(now) + 4) + '" y="24" font-size="9" fill="var(--bad)">now</text></svg>';
    return out;
  }


  // ------------------------------------------------------------- diagram notes
  // Click any part of a diagram to read what it is. Keys are "diagram:part".
  const INFO = {
    "timeline:hot": "Hot window: one median view gap, ending at the evaluation time. A view inside it qualifies the quote for tier 2.",
    "timeline:sent": "quote_sent. Start of the quote's line.",
    "timeline:contact": "message_sent. Attempt count, cooldown and backoff are measured from these.",
    "timeline:view": "quote_viewed. A view after the last contact is the primary intent signal.",
    "timeline:reply": "customer_replied. An unanswered reply puts the quote in tier 1. The response is written by a person.",
    "timeline:now": "Evaluation time, set by the shop clock. Events to the right are ignored.",
    "timeline:line": "Quote lifetime, from quote_sent to the evaluation time or close. Thick: open. Click the row to select the quote.",
    "timeline:label": "Outcome: tier if the quote is a candidate, otherwise the exclusion reason.",
    "flow:B": "Filter. Excludes accepted, dismissed, held and attempt-capped quotes. Holds include opt-outs.",
    "flow:C": "Filter. Excludes customers inside the cooldown or with a draft pending review. At most one open conversation per customer.",
    "flow:T1": "Unanswered customer reply. Highest priority.",
    "flow:T2": "View since the last contact, inside the hot window.",
    "flow:BK": "Backoff. Wait after n unanswered attempts: cooldown plus n median view gaps. If not served, no candidate.",
    "flow:T3": "Amount at or above the big quote threshold, and no contact for the big and quiet wait.",
    "flow:T4": "Never contacted, and older than the shop's median time to first contact.",
    "flow:T5": "No contact for longer than the shop's median time to accept.",
    "flow:R1": "Tier 1. Reply required. Drafted for a person to edit.",
    "flow:R2": "Tier 2. Recent view. Short nudge.",
    "flow:R3": "Tier 3. Large quote, inactive. Escalated to the owner.",
    "flow:R4": "Tier 4. First follow-up.",
    "flow:R5": "Tier 5. Stale. Final touch. Price hold or offer eligible.",
    "flow:X": "Excluded. The reason code is listed under Not contacted on the Queue tab.",
    "flow:M": "Per-customer dedupe. Several open quotes produce one candidate, for the best tier.",
    "flow:A": "Input: every open quote, on each run.",
    "amounts:dot": "One quote. Red: selected.",
    "amounts:median": "Shop median quote amount. Size thresholds are multiples of it.",
    "amounts:big": "Big quote band: amount at or above the multiple times the median. The multiple is set under Parameters.",
    "life:draft": "draft. Passed the text guard. Awaiting review.",
    "life:approved": "approved. Approver and SHA-256 of the body stored. Any edit clears the approval.",
    "life:sending": "sending. Entered by a conditional UPDATE from approved, so concurrent sends cannot both succeed. On provider timeout the draft stays here until reconciled.",
    "life:sent": "sent. Provider accepted. A message_sent event is appended to the log.",
    "life:failed": "failed. Provider rejected. A retry reuses the idempotency key.",
    "life:rejected": "rejected. Declined by the reviewer. Customer snoozed for the cooldown.",
    "life:expired": "expired. Quote closed or signal stale before approval.",
    "life:skipped": "skipped. A send-time check failed: quote closed, cooldown, offer expired or slot filled. Kill switch and quiet hours defer the send instead.",
    "life:edit": "An edit clears the approval and returns the draft to review.",
    "offer:curve": "Discount off the quote as a function of slack (booked hours below the shop's median week). S curve: flat near a median week and near an empty week, steepest in between. Rounded down to $25.",
    "offer:cap": "Per-shop cap on the discount amount. Upper bound at any slack.",
    "offer:now": "Discount at the current slack. Moves with hours booked and nearby openings.",
    "offer:axis": "Slack, from 0 (median week) to 1 (empty week).",
  };
  // The design document's mermaid diagrams after the module map, in order, keyed by node id or label.
  const DOC_INFO = [
    { SHOP: "Tenant. Timezone, config, limits, kill switch. All other tables are keyed by shop_id.",
      CUSTOMER: "Household, unique by phone within a shop. The same phone in another shop is a separate row.",
      QUOTE: "Quote from the quote system. Status and last contact are derived from the event log.",
      EVENT: "Append-only log. Unique on (shop_id, event_id); duplicate webhooks are no-ops.",
      BASELINE: "Shop median per metric, with sample count and source (shop, parent or platform).",
      DRAFT: "Proposed message: reason, strategy, signals, policy version, approved body hash.",
      OFFER: "Costed offer on a draft. Counted against the monthly budget. Optional slot reference.",
      OUTBOX: "One row per draft. Holds the idempotency key passed to the provider.",
      OUTCOME: "Post-send result: reply, acceptance, incentive cost. Input to the learning system." },
    { A: "flow:A", B: "flow:B", C: "flow:C", T1: "flow:T1", T2: "flow:T2", BK: "flow:BK", T3: "flow:T3", T4: "flow:T4", T5: "flow:T5",
      R1: "flow:R1", R2: "flow:R2", R3: "flow:R3", R4: "flow:R4", R5: "flow:R5", X: "flow:X", M: "flow:M" },
    { draft: "life:draft", approved: "life:approved", sending: "life:sending", sent: "life:sent", failed: "life:failed",
      rejected: "life:rejected", expired: "life:expired", skipped: "life:skipped" },
    { Owner: "Approver. Triggers the send.",
      Review: "Review workflow. Persists the approval and body hash.",
      Delivery: "Delivery service. Claims the draft, re-runs send checks, calls the provider once.",
      Guard: "Send-time checks: approval and hash, kill switch, quote open, cooldown, offer and slot validity, daily cap, quiet hours.",
      DB: "Claim and outbox insert in one transaction. A second send finds no approved draft to claim.",
      Sender: "Messaging provider. Deduplicates on the idempotency key." },
    { A: "Signals and baselines at decision time, stored with the draft.",
      B: "Action selector: strategy, framing, offer, channel, hour. Rule-based now; uplift model later.",
      C: "Per-shop constraints: allowed framings, offers, caps, monthly budget.",
      D: "Selected action, stored on the draft.",
      E: "Human approval on every message.",
      F: "Send via the delivery service.",
      G: "Outcome: reply, acceptance, job margin, incentive cost.",
      H: "Decision records with inputs and outcomes. Training data.",
      I: "Estimates the incremental effect of each action over a plain message, net of cost. Designed, not built.",
      J: "Holdout group sent a plain message. Baseline for measuring uplift." },
    { UI: "Page UI. Identical under both transports.",
      T: "Transport selected by page origin.",
      H: "Local: HTTP to a FastAPI app.",
      P: "Website: Python in the browser via Pyodide. No server calls.",
      API: "api.py facade. Same functions and arguments under both transports.",
      ENG: "Engine packages: ingestion, state, policy, guard, review, delivery.",
      DB: "SQLite (file locally, in-memory in the browser). Postgres with RLS in production." },
  ];
  function infoBox(diagram) {
    const text = S.info && S.info.startsWith(diagram + ":") ? INFO[S.info] : null;
    return '<div class="diagram-info' + (text ? " on" : "") + '">' + (text ? esc(text) : "Click a component for its description.") + "</div>";
  }
  const I = (key) => ' data-info="' + key + '"';

  // ------------------------------------------------------------------- policy
  const FLOW = [
    ["B", "Closed, held, or attempts used up?"], ["C", "In cooldown, or a draft in review?"], ["T1", "Awaiting our reply?"],
    ["T2", "Viewed since contact, in the hot window?"], ["BK", "Backoff wait served?"], ["T3", "Big quote, quiet long enough?"],
    ["T4", "Never contacted, past first contact time?"], ["T5", "Past the stale wait?"],
  ];
  const RESULT = { T1: "Tier 1: needs a human reply", T2: "Tier 2: hot view", T3: "Tier 3: big and quiet", T4: "Tier 4: first follow-up", T5: "Tier 5: stale" };
  const STOP_ON = { B: "yes", C: "yes", BK: "no", T5: "no" };

  function flowchart(path) {
    const ans = {};
    (path || []).forEach(([k, v]) => { ans[k] = v; });
    const W = 780, cx = 330, bw = 270, bh = 36, gap = 58, y0 = 14;
    let out = '<svg viewBox="0 0 ' + W + ' 560" width="100%" style="min-width:560px" role="img" aria-label="Policy flowchart">';
    const node = (x, y, w, text, cls, key) => '<g class="node click ' + cls + '"' + I("flow:" + key) + '><rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + bh + '" rx="8"/>' +
      '<text x="' + (x + w / 2) + '" y="' + (y + bh / 2 + 4) + '" font-size="11.5" text-anchor="middle">' + esc(text) + "</text></g>";
    let stopped = false;
    FLOW.forEach(([id, label], i) => {
      const y = y0 + i * gap, hit = id in ans;
      const next = i < FLOW.length - 1;
      const goes = STOP_ON[id] === "yes" ? "no" : id === "BK" ? "yes" : "no";
      if (next) out += '<path class="edge ' + (hit && ans[id] === goes ? "hit" : "") + '" d="M' + cx + " " + (y + bh) + " V" + (y + gap) + '"/>' +
        '<text class="dim" x="' + (cx + 6) + '" y="' + (y + bh + 14) + '" font-size="9.5">' + goes + "</text>";
      if (RESULT[id]) {
        const on = ans[id] === "yes";
        out += '<path class="edge ' + (on ? "hit" : "") + '" d="M' + (cx + bw / 2) + " " + (y + bh / 2) + " H520\"/>" +
          '<text class="dim" x="' + (cx + bw / 2 + 6) + '" y="' + (y + bh / 2 - 4) + '" font-size="9.5">yes</text>' +
          node(520, y, 250, RESULT[id], "end " + (on ? "hit" : ""), "R" + id.slice(1));
      }
      if (STOP_ON[id]) {
        const on = hit && ans[id] === STOP_ON[id];
        if (on) stopped = true;
        out += '<path class="edge ' + (on ? "hit" : "") + '" d="M' + (cx - bw / 2) + " " + (y + bh / 2) + " H150\"/>" +
          '<text class="dim" x="' + (cx - bw / 2 - 22) + '" y="' + (y + bh / 2 - 4) + '" font-size="9.5">' + STOP_ON[id] + "</text>" +
          node(10, y, 140, "Not a candidate", "stop " + (on ? "hit" : ""), "X");
      }
      out += node(cx - bw / 2, y, bw, label, hit ? "hit" : "", id);
    });
    const m = ans.M, yM = y0 + FLOW.length * gap + 10;
    out += node(520, yM, 250, m === "other" ? "Their other quote ranked higher" : "One candidate per customer", (m === "other" ? "stop " : "end ") + (m ? "hit" : ""), "M");
    out += '<path class="edge ' + (m ? "hit" : "") + '" d="M645 ' + (y0 + 4 * gap + bh + gap * 3) + " V" + yM + '"/>';
    return out + "</svg>";
  }

  function amountStrip() {
    const d = S.data, W = 560, H = 70, max = Math.max.apply(null, d.quotes.map((q) => q.amount_cents)) * 1.08;
    const x = (c) => 10 + (c / max) * (W - 20);
    const med = d.baselines.find((b) => b.metric === "amount").value, big = d.thresholds.big_amount_cents;
    let out = '<svg viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" aria-label="Quote amounts with the shop median">' +
      '<rect x="' + x(big) + '" y="8" width="' + Math.max(0, W - 10 - x(big)) + '" height="40" fill="var(--accent-soft)"' + I("amounts:big") + "/>" +
      '<line x1="10" x2="' + (W - 10) + '" y1="28" y2="28" stroke="var(--line)"/>';
    const seen = {};
    d.quotes.forEach((q) => {
      const k = q.amount_cents; seen[k] = (seen[k] || 0) + 1;
      out += '<circle cx="' + x(k) + '" cy="' + (28 - (seen[k] - 1) * 3.4 + 6) + '" r="3" fill="' + (q.id === S.quote ? "var(--bad)" : "var(--accent)") + '" opacity="0.8"' + I("amounts:dot") + "/>";
    });
    out += '<line x1="' + x(med) + '" x2="' + x(med) + '" y1="4" y2="52" stroke="var(--ink)" stroke-dasharray="3 2"' + I("amounts:median") + "/>" +
      '<text' + I("amounts:median") + ' x="' + x(med) + '" y="64" font-size="10" text-anchor="middle">median ' + money(med) + "</text>" +
      '<text' + I("amounts:big") + ' x="' + Math.min(W - 60, x(big) + 4) + '" y="64" font-size="10" fill="var(--accent)">big from ' + money(big) + "</text></svg>";
    return out;
  }

  VIEWS.policy = function () {
    const d = S.data, q = S.detail, th = d.thresholds, cfg = d.config;
    const base = d.baselines.map((b) => "<tr><td>" + b.metric.replace(/_/g, " ") + '</td><td class="num">' + (b.metric === "amount" ? money(b.value) : hours(b.value)) +
      '</td><td class="num">' + b.sample_n + '</td><td><span class="badge ' + (b.source === "shop" ? "ok" : "warn") + '">' + b.source + "</span></td><td class=\"num muted\">" +
      (b.shop_value == null ? "" : b.metric === "amount" ? money(b.shop_value) : hours(b.shop_value)) + "</td></tr>").join("");
    const rows = [
      ["Big quote", money(th.big_amount_cents) + " and up", cfg.big_multiple + " times the median amount"],
      ["Hot window", hours(th.hot_window_h), "one typical gap between views"],
      ["View burst", th.burst_views + " views in " + hours(th.burst_window_h), "inside one typical gap"],
      ["Returned after a gap", hours(th.returned_gap_h), cfg.returned_gap_multiple + " times the view gap"],
      ["Never viewed", hours(th.never_viewed_h), "typical time to a first view"],
      ["First follow-up due", hours(th.first_followup_h), "the shop's own usual first contact"],
      ["Stale", hours(th.stale_h), "typical time to accept"],
      ["Big and quiet", hours(th.big_quiet_h), cfg.big_quiet_fraction + " of the stale wait"],
      ["Backoff", th.backoff_h.map(hours).join(", then "), "cooldown plus one view gap per attempt"],
    ].map((r) => "<tr><td>" + r[0] + '</td><td class="num"><b>' + r[1] + '</b></td><td class="muted small">' + r[2] + "</td></tr>").join("");
    const slider = (key, label, min, max, step) => '<label class="field"><span class="name">' + label + '</span><input type="range" data-config="' + key + '" min="' + min +
      '" max="' + max + '" step="' + step + '" value="' + cfg[key] + '"><output>' + cfg[key] + "</output></label>";
    return '<div class="grid"><div class="card"><div class="row"><h2>Decision path</h2><span class="spacer"></span>' +
      '<select data-pick="quote">' + d.quotes.map((x) => '<option value="' + x.id + '"' + (x.id === S.quote ? " selected" : "") + ">" + x.id + " " + esc(x.customer_name) + "</option>").join("") + "</select></div>" +
      '<p class="lead muted">Filters, then tiers. Highlighted path: the decision for the selected quote at the evaluation time.</p>' +
      (q && q.candidate ? '<div class="note ok">' + tier(q.candidate.tier) + " " + esc(q.candidate.reason) + "</div>" : q && q.excluded ? '<div class="note warn">' + esc(WHY[q.excluded.why] || q.excluded.why) + ": " + esc(q.excluded.detail) + "</div>" : "") +
      '<div class="scroll">' + flowchart(q ? q.path : []) + "</div>" + infoBox("flow") + "</div>" +
      '<div><div class="card"><h2>Thresholds from shop medians</h2><p class="lead muted">Each threshold is a shop median times a shared multiplier. No fixed amounts or durations.</p>' +
      amountStrip() + infoBox("amounts") + '<h3>Baselines</h3><table><thead><tr><th>Metric</th><th class="num">Used</th><th class="num">Samples</th><th>From</th><th class="num">Shop\'s own</th></tr></thead><tbody>' + base +
      '</tbody></table><p class="muted small" style="margin-top:6px">A median needs ' + cfg.min_samples + " samples. Below that, the parent or platform value is used; the shop value is shown for reference.</p>" +
      "<h3>Thresholds</h3><table><tbody>" + rows + "</tbody></table></div>" +
      '<div class="card" style="margin-top:14px"><h2>Parameters</h2><p class="lead muted">Multipliers are shared across shops. Limits are fixed per-shop policy values, not statistics.</p>' +
      slider("big_multiple", "Big quote multiple", 1, 4, 0.25) + slider("min_samples", "Samples needed for a median", 1, 40, 1) +
      slider("cooldown_hours", "Cooldown, hours (a limit)", 0, 168, 12) + slider("attempt_cap", "Attempt cap (a limit)", 1, 5, 1) + "</div></div></div>";
  };

  // ------------------------------------------------------------------- review
  function machine(status) {
    const N = { draft: [20, 120], approved: [150, 120], sending: [280, 120], sent: [410, 120], failed: [280, 24], rejected: [20, 216], expired: [150, 216], skipped: [280, 216] };
    const E = [["draft", "approved", "approve"], ["approved", "sending", "claim"], ["sending", "sent", "accepted"], ["sending", "failed", "rejected"],
      ["draft", "rejected", "reject"], ["draft", "expired", "quote closed"], ["approved", "skipped", "check fails"]];
    let out = '<svg viewBox="0 0 520 270" width="100%" role="img" aria-label="Draft lifecycle">';
    E.forEach(([a, b, label]) => {
      const A = N[a], B = N[b], x1 = A[0] + 45, y1 = A[1] + 17, x2 = B[0] + 45, y2 = B[1] + 17;
      out += '<path class="edge" d="M' + x1 + " " + y1 + " L" + x2 + " " + y2 + '"/><text class="dim" x="' + ((x1 + x2) / 2) + '" y="' + ((y1 + y2) / 2 - 22) + '" font-size="9" text-anchor="middle">' + label + "</text>";
    });
    out += '<path class="edge" d="M175 120 C175 96 85 96 85 120" stroke-dasharray="3 3"/><text class="dim"' + I("life:edit") + ' x="130" y="94" font-size="9" text-anchor="middle">edit clears approval</text>';
    Object.entries(N).forEach(([name, p]) => {
      const cls = name === status ? (["sent"].includes(name) ? "end hit" : ["failed", "skipped", "rejected", "expired"].includes(name) ? "stop hit" : "hit") : "";
      out += '<g class="node click ' + cls + '"' + I("life:" + name) + '><rect x="' + p[0] + '" y="' + p[1] + '" width="90" height="34" rx="8"/><text x="' + (p[0] + 45) + '" y="' + (p[1] + 21) + '" font-size="11.5" text-anchor="middle">' + name + "</text></g>";
    });
    return out + "</svg>";
  }

  VIEWS.review = function () {
    const d = S.data;
    const names = {};
    d.quotes.forEach((q) => { names[q.id] = q.customer_name; });
    const btn = (act, label, id, cls) => '<button class="small ' + (cls || "") + '" data-act="' + act + '" data-id="' + id + '">' + label + "</button>";
    const cards = d.drafts.slice().sort((a, b) => a.tier - b.tier).map((x) => {
      const st = x.status, buttons = [];
      if (st === "draft") buttons.push(btn("approve", "Approve", x.id, "primary"));
      if (st === "approved") buttons.push(btn("send", "Send", x.id, "primary"));
      if (st === "failed") buttons.push(btn("send", "Retry send", x.id, "primary"));
      if (st === "sending") buttons.push(btn("reconcile", "Ask the provider", x.id, "primary"));
      if (["sent", "sending", "draft"].includes(st)) buttons.push(btn("send", st === "draft" ? "Send without approval" : "Send again", x.id));
      if (["draft", "approved"].includes(st)) buttons.push(btn("edit", "Edit", x.id), btn("reject", "Reject", x.id), btn("accept", "Customer accepts the quote", x.quote_id));
      const body = S.editing === x.id
        ? '<textarea id="edit-body">' + esc(x.body) + '</textarea><div class="row" style="margin-top:6px">' + btn("save", "Save", x.id, "primary") + btn("cancel", "Cancel", x.id) + "</div>"
        : '<div class="msg">' + esc(x.body) + "</div>";
      return '<div class="draft ' + (x.id === S.draft ? "sel" : "") + '" data-draft="' + x.id + '"><div class="row">' + tier(x.tier) + "<b>" + esc(names[x.quote_id] || x.quote_id) +
        '</b><span class="muted small">' + x.quote_id + " · " + esc(x.strategy) + (x.framing ? " · " + esc(x.framing) : "") + '</span><span class="spacer"></span>' +
        (x.requires_owner ? '<span class="badge warn">owner only</span>' : "") + '<span class="badge">' + esc(x.written_by) + '</span><span class="badge ' + (STATUS_BADGE[st] || "") + '">' + st + "</span></div>" +
        '<p class="muted small" style="margin:6px 0">' + esc(x.reason) + "</p>" + body +
        (x.approver_note ? '<div class="note">' + esc(x.approver_note) + "</div>" : "") + (x.status_reason ? '<div class="note warn">' + esc(x.status_reason) + "</div>" : "") +
        '<div class="row" style="margin-top:8px">' + buttons.join("") + "</div></div>";
    }).join("");
    const sel = d.drafts.find((x) => x.id === S.draft);
    const out = d.drafts.filter((x) => x.outbox).map((x) => "<tr><td>" + esc(names[x.quote_id]) + '</td><td class="mono small">' + esc(x.outbox.to_phone) + "</td><td>" + esc(x.outbox.status) +
      '</td><td class="num">' + x.outbox.attempts + '</td><td class="mono small">' + esc(x.outbox.idempotency_key) + "</td></tr>").join("");
    return '<div class="grid"><div class="card"><div class="row"><h2>Drafts</h2><span class="spacer"></span><button class="primary" data-act="run">Run the engine</button></div>' +
      '<p class="lead muted">The run is idempotent: at most one live draft per customer. Test cases: duplicate send; quote accepted after approval.</p>' +
      '<div class="row" style="margin-bottom:10px"><label>Approve as <select data-pick="role"><option value="owner"' + (S.role === "owner" ? " selected" : "") + ">owner</option><option value=\"tech\"" + (S.role === "tech" ? " selected" : "") + ">tech</option></select></label>" +
      '<label>Provider answers next send with <select data-pick="script"><option value="accept">accept</option><option value="reject">reject</option><option value="timeout">timeout, not delivered</option><option value="timeout_but_delivered">timeout, but delivered</option></select></label>' +
      '<label><input type="checkbox" data-pick="kill"' + (d.sending.shop ? "" : " checked") + "> Kill switch</label>" +
      '<button class="small" data-act="approve-all">Approve all</button></div>' + (cards || '<p class="muted">No drafts. Run the engine.</p>') + "</div>" +
      '<div><div class="card"><h2>Lifecycle' + (sel ? ' of the selected draft' : "") + "</h2>" + machine(sel ? sel.status : null) + infoBox("life") +
      '<p class="muted small">Approval stores the body hash. Send: conditional UPDATE from approved to sending, send-time checks, provider call.</p>' + logHtml() + "</div>" +
      '<div class="card" style="margin-top:14px"><h2>Outbox</h2><p class="lead muted">One row per draft. The idempotency key is passed to the provider for deduplication.</p><div class="scroll"><table><thead><tr><th>To</th><th>Number</th><th>Status</th><th class="num">Tries</th><th>Key</th></tr></thead><tbody>' +
      (out || '<tr><td colspan="5" class="muted">Nothing sent.</td></tr>') + "</tbody></table></div></div></div></div>";
  };

  // -------------------------------------------------------------------- messy
  VIEWS.messy = function () {
    const m = S.messy, d = S.data;
    const result = m ? '<div class="note ' + (m.same ? "ok" : "bad") + '"><b>' + (m.same ? "Same result." : "Different result.") + "</b> " + m.sent + " events sent in a random order, " +
      m.stored + " stored, " + m.duplicates_ignored + ' duplicates ignored.</div><dl class="kv"><dt>Original</dt><dd class="mono small">' + m.original_hash.slice(0, 32) +
      '</dd><dt>Replayed</dt><dd class="mono small">' + m.replayed_hash.slice(0, 32) + '</dd><dt>Order it arrived in</dt><dd class="mono small">' + m.first_ten_order.join(" ") + " ...</dd></dl>" : "";
    return '<div class="grid even"><div class="card"><h2>Shuffle and duplicate replay</h2>' +
      '<p class="lead muted">Webhooks are delivered late, out of order and at least once. This replays the shop\'s ' + d.counts.events + " events into an empty database in random order with duplicates, then compares quote state, medians and the ranked queue with the original.</p>" +
      '<label class="field"><span class="name">Share of events duplicated</span><input type="range" id="dup" min="0" max="2" step="0.1" value="0.5"><output id="dup-out">0.5</output></label>' +
      '<div class="row"><button class="primary" data-act="messy">Shuffle and replay</button><span class="muted small">A new random order each time.</span></div>' + result + "</div>" +
      '<div class="card"><h2>Mechanism</h2><p>Unique constraint on (shop_id, event_id). Duplicates are ignored on insert.</p>' +
      "<p>Quote state is a projection of the full log, not updated incrementally. It uses only order-independent aggregates: latest timestamp per type, counts, existence.</p>" +
      "<p>Closed is terminal. The seed contains two views after acceptance and one after dismissal. Neither reopens the quote.</p>" +
      '<h3>Late event</h3><p>Inserts a quote_viewed event timestamped 48 h before the evaluation time for the selected quote. Its position depends on its timestamp, not on arrival order.</p>' +
      '<div class="row"><button data-act="late">Add a late view to ' + esc(S.quote || "a quote") + "</button></div>" + logHtml() + "</div></div>";
  };

  // ----------------------------------------------------------------- capacity
  // Same curve as followup/policy/offers.py: logistic in slack, rescaled so 0 maps to 0 and 1 to 1.
  function slackCurve(s, c) {
    const x = Math.max(0, Math.min(1, s)), k = c.slack_curve_steepness, m = c.slack_curve_mid;
    if (!(k > 0)) return x;
    const f = (t) => 1 / (1 + Math.exp(-k * (t - m))), lo = f(0), hi = f(1);
    return Math.max(0, Math.min(1, (f(x) - lo) / (hi - lo)));
  }
  const discountRate = (s, c) => c.percent_base + (c.percent_max - c.percent_base) * slackCurve(s, c);
  // "$750 off (6.0% of $12,500, at cap)": an amount taken off the quote, never a new price.
  function discountLabel(offer, amount, cap) {
    const v = offer.value_cents, pct = amount ? (100 * v / amount).toFixed(1) + "% of " + money(amount) : "";
    return "<b>" + money(v) + " off</b> " + '<span class="muted small">' + pct + (cap && v >= cap ? ", at cap" : "") + "</span>";
  }

  function offerCurve(amount) {
    const c = S.data.config, W = 520, H = 190, L = 46, B = 28, cap = c.percent_cap_cents;
    const val = (s) => Math.floor(Math.min(Math.floor(amount * discountRate(s, c)), cap) / 2500) * 2500;
    const top = Math.max(Math.min(cap, val(1)) * 1.08, val(0) * 1.2), lo = Math.max(0, Math.floor(val(0) * 0.85 / 2500) * 2500);
    const x = (s) => L + s * (W - L - 12), y = (v) => H - B - ((v - lo) / (top - lo)) * (H - B - 12);
    let pts = "";
    for (let i = 0; i <= 100; i++) pts += (i ? " L" : "M") + x(i / 100) + " " + y(val(i / 100));
    const cur = Math.min(1, S.data.capacity.shop_slack + (S.opening ? c.nearby_step || 0.5 : 0));
    return '<svg viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" aria-label="Discount against slack">' +
      (cap <= top ? '<line x1="' + L + '" x2="' + (W - 12) + '" y1="' + y(cap) + '" y2="' + y(cap) + '" stroke="var(--bad)" stroke-dasharray="4 3"' + I("offer:cap") + "/>" +
        '<text' + I("offer:cap") + ' x="' + (W - 14) + '" y="' + (y(cap) - 4) + '" font-size="10" text-anchor="end" fill="var(--bad)">cap: at most ' + money(cap) + " off</text>" : "") +
      '<line x1="' + L + '" x2="' + (W - 12) + '" y1="' + (H - B) + '" y2="' + (H - B) + '" stroke="var(--line)"/><line x1="' + L + '" x2="' + L + '" y1="10" y2="' + (H - B) + '" stroke="var(--line)"/>' +
      '<path d="' + pts + '" fill="none" stroke="var(--accent)" stroke-width="2.2"/><path d="' + pts + '" fill="none" stroke="transparent" stroke-width="12"' + I("offer:curve") + "/>" +
      '<circle cx="' + x(cur) + '" cy="' + y(val(cur)) + '" r="6" fill="var(--bad)"' + I("offer:now") + '/><text' + I("offer:now") + ' x="' + (x(cur) + (cur > 0.7 ? -8 : 8)) + '" y="' + (y(val(cur)) + 16) + '" font-size="11" font-weight="700" text-anchor="' + (cur > 0.7 ? "end" : "start") + '">' + money(val(cur)) + " off now</text>" +
      '<text class="dim"' + I("offer:axis") + ' x="' + L + '" y="' + (H - 8) + '" font-size="10">usual week</text><text class="dim"' + I("offer:axis") + ' x="' + (W - 12) + '" y="' + (H - 8) + '" font-size="10" text-anchor="end">empty week</text>' +
      '<text class="dim" x="6" y="' + (y(lo) + 3) + '" font-size="10">' + money(lo) + "</text>" +
      '<text class="dim" x="6" y="' + (y(val(1)) + 3) + '" font-size="10">' + money(val(1)) + "</text>" + '<text class="dim" x="' + (L + 6) + '" y="11" font-size="9.5">discount off the quote</text>' +
      (cap > top ? '<text class="dim"' + I("offer:cap") + ' x="' + (W - 14) + '" y="14" font-size="10" text-anchor="end">cap ' + money(cap) + " off, not reached</text>" : "") + "</svg>";
  }

  VIEWS.capacity = function () {
    const d = S.data, cap = d.capacity, q = d.quotes.find((x) => x.id === S.quote) || d.quotes[0];
    const booked = cap.connected ? cap.booked_hours : 160;
    const offers = d.candidates.filter((c) => c.framing || c.offer).map((c) => '<tr class="pick ' + (c.quote_id === S.quote ? "sel" : "") + '" data-quote="' + c.quote_id + '"><td>' + esc(c.customer_name) +
      '</td><td class="num">' + money(c.amount_cents) + "</td><td>" + esc(c.framing ? c.framing.kind.replace(/_/g, " ") : "") + '</td><td class="num">' +
      (c.offer && c.offer.value_cents ? discountLabel(c.offer, c.amount_cents, d.config.percent_cap_cents)
        : '<span class="muted">none</span> <span class="muted small">' + (c.framing && c.framing.kind === "premium_option" ? money(c.framing.numbers_cents.package) + " package shown beside it" : "price held") + "</span>") +
      "</td><td>" + (c.offer && c.offer.slot_label ? esc(c.offer.slot_label) : "") + "</td></tr>").join("");
    const costed = d.candidates.reduce((n, c) => n + (c.offer ? c.offer.value_cents : 0), 0);
    return '<div class="grid even"><div class="card"><h2>Schedule and slack</h2><p class="lead muted">The discount scales with schedule slack, on an S curve, and with nearby openings, since unused capacity lowers the cost of a discount. High slack also moves the offer one touch earlier. Bounded by the per-shop cap and monthly budget.</p>' +
      '<label class="field"><span class="name">Hours booked this week</span><input type="range" id="booked" min="0" max="200" step="10" value="' + booked + '"><output>' + booked + "</output></label>" +
      '<p class="muted small">Median week: 160 of 200 h. Slack is measured against the median, not against full capacity.</p>' +
      '<div class="row"><span class="badge ' + (cap.connected ? "info" : "") + '">' + (cap.connected ? "slack " + cap.shop_slack : "no schedule connected") + "</span>" +
      (cap.fully_booked ? '<span class="badge bad">fully booked, no costed offers</span>' : "") + (cap.error ? '<span class="badge bad">schedule service down, running as normal</span>' : "") + "</div>" +
      '<h3>Nearby opening</h3><label><input type="checkbox" id="opening"' + (S.opening ? " checked" : "") + "> A tech has an opening Thursday afternoon near " + esc(q.customer_name) + "</label>" +
      '<div class="row" style="margin-top:8px"><button class="small" data-act="fill-slot"' + (S.opening ? "" : " disabled") + '>Someone else books that slot</button><button class="small" data-act="cap-down">Schedule service goes down</button><button class="small" data-act="cap-clear">Disconnect the schedule</button></div>' +
      "<h3>Discount on a " + money(q.amount_cents) + " quote, by slack</h3>" + offerCurve(q.amount_cents) + infoBox("offer") + logHtml() + "</div>" +
      '<div class="card"><h2>Current offers</h2><p class="lead muted">Zero-cost framings first. A costed offer requires the final touch, high slack or a nearby opening, and owner approval.</p>' +
      '<div class="scroll"><table><thead><tr><th>Customer</th><th class="num">Quote</th><th>Framing</th><th class="num">Discount off the quote</th><th>Slot</th></tr></thead><tbody>' + (offers || '<tr><td colspan="5" class="muted">No closing messages due.</td></tr>') + "</tbody></table></div>" +
      "<h3>Monthly offer budget</h3><p>" + money(costed) + " of discounts in this queue, " + money(d.offers.spent_cents) + " already committed in drafts, of " + money(d.offers.budget_cents) + " per month.</p>" +
      "<h3>Premium option</h3><p>The anchor is a sellable package at the stated price. The engine selects the add-on that puts the package between 1.2 and 1.5 times the quote. If none fits, no anchor is shown.</p>" +
      "<table><tbody>" + d.offers.addons.map((a) => "<tr><td>" + esc(a.label) + '</td><td class="num">' + money(a.price_cents) + "</td></tr>").join("") + "</tbody></table></div></div>";
  };

  // -------------------------------------------------------------------- guard
  VIEWS.guard = function () {
    const d = S.data, q = d.quotes.find((x) => x.id === S.quote) || d.quotes[0];
    const draft = d.drafts.find((x) => x.quote_id === q.id && ["draft", "approved"].includes(x.status));
    const cases = (S.cases || []).map((c) => '<button class="small" data-act="case" data-id="' + c.id + '">' + esc(c.label) + "</button>").join(" ");
    const r = S.guardResult;
    const verdict = !r ? "" : r.ok ? '<div class="note ok"><b>Pass.</b> All amounts, dates and names match the decision.</div>'
      : '<div class="note bad"><b>Rejected.</b> Template output used instead.</div><table><tbody>' + r.violations.map((v) => '<tr><td><span class="badge bad">' + esc(v.rule) + "</span></td><td>" + esc(v.detail) + "</td></tr>").join("") + "</tbody></table>";
    const allowed = r ? "<h3>Permitted values for this draft</h3><dl class=\"kv\"><dt>Dollar figures</dt><dd>" + r.allowed.allowed_money_cents.map(money).join(", ") + "</dd><dt>Dates</dt><dd>" +
      (r.allowed.allowed_dates.map((x) => x[0] + "/" + x[1]).join(", ") || "none") + "</dd><dt>Discount language</dt><dd>" + (r.allowed.has_costed_offer ? "allowed, an offer is attached" : "not allowed, no offer attached") + "</dd></dl>" : "";
    const run = S.guardRun;
    const runHtml = !run ? "" : '<div class="note ok"><b>' + run.created + " drafts created, " + run.byTemplate + " written by the template.</b> No writer output was used.</div>" +
      "<table><tbody>" + run.log.slice(0, 5).map((l) => '<tr><td class="small">' + esc(l.quote_id) + "</td><td class=\"small\">" + esc((l.body || l.error || "").slice(0, 70)) + '</td><td><span class="badge bad">' +
      esc(l.violations ? l.violations[0].rule : "error") + "</span></td></tr>").join("") + "</tbody></table>";
    return '<div class="grid even"><div class="card"><h2>Text guard test</h2>' +
      '<p class="lead muted">Deterministic validation against the facts the policy attached to the draft for ' + esc(q.customer_name) + " (" + money(q.amount_cents) + (draft ? ", draft in review" : "") + ").</p>" +
      '<div class="row" style="margin-bottom:8px">' + cases + '</div><textarea id="guard-text" placeholder="Type a message, or pick one above">' + esc(S.guardText) + "</textarea>" +
      '<div class="row" style="margin-top:8px"><button class="primary" data-act="guard-check">Check this text</button><select data-pick="quote">' +
      d.quotes.map((x) => '<option value="' + x.id + '"' + (x.id === q.id ? " selected" : "") + ">" + x.id + " " + esc(x.customer_name) + "</option>").join("") + "</select></div>" + verdict + allowed + "</div>" +
      '<div class="card"><h2>Writer substitution test</h2><p class="lead muted">Resets the demo, replaces the writer with one that returns the text on the left for every candidate, and runs the engine.</p>' +
      '<div class="row"><button data-act="guard-run">Run the engine with that writer</button><button data-act="guard-broken">Run with a writer that times out</button></div>' + runHtml +
      "<h3>Writer constraints</h3><ul style=\"padding-left:18px;margin:0\"><li>Input: first name, amount, reason, offer. No phone number, other customers or other shops.</li>" +
      "<li>Output is text only. Recipient, channel, price and send time come from the database.</li><li>No approval rights. Approval is human-only and bound to the body hash.</li>" +
      "<li>Customer replies are passed as quoted data. Reply drafts always require human review.</li></ul>" + logHtml() + "</div></div>";
  };

  // -------------------------------------------------------------------- shops
  VIEWS.shops = function () {
    const a = S.data, b = S.other;
    if (!b) { loadOther(); return '<div class="card muted">Loading the second shop.</div>'; }
    const col = (d) => "<h2>" + esc(d.shop_name) + '</h2><table><tbody>' +
      [["Median quote", money(d.baselines.find((x) => x.metric === "amount").value)], ["Big quote from", money(d.thresholds.big_amount_cents)],
        ["Open quotes", d.counts.open], ["To contact", d.candidates.length], ["Drafts", d.drafts.length], ["Schedule slack", d.capacity.connected ? d.capacity.shop_slack : "not connected"]]
        .map((r) => "<tr><td>" + r[0] + '</td><td class="num"><b>' + r[1] + "</b></td></tr>").join("") + "</tbody></table>" +
      '<p class="muted small" style="margin-top:8px">A $6,000 quote here is ' + (600000 >= d.thresholds.big_amount_cents ? "<b>big</b>" : "not big") + ".</p>";
    const l = S.leak;
    const leak = !l ? "" : '<table><thead><tr><th>Request as ' + esc(a.shop_name) + "</th><th>Answer</th></tr></thead><tbody>" + l.map((x) => "<tr><td>" + esc(x.what) + '</td><td class="mono small">' + esc(x.answer) + "</td></tr>").join("") +
      '</tbody></table><div class="note ok">A cross-tenant id returns the same error as a nonexistent id. The other shop\'s draft is unchanged.</div>';
    return '<div class="grid even"><div class="card">' + col(a) + '</div><div class="card">' + col(b) + "</div></div>" +
      '<div class="card" style="margin-top:14px"><h2>Tenant isolation with colliding ids</h2><p class="lead muted">Both shops load the same seed, so quote ids, event ids and phone numbers collide. All tables are keyed by shop_id, unscoped queries are rejected, and Postgres RLS enforces the same constraint in the database.</p>' +
      '<div class="row"><button class="primary" data-act="leak">Try to act on the other shop\'s draft</button></div>' + leak + "</div>";
  };
  async function loadOther() {
    const id = S.shop === "shop-a" ? "shop-b" : "shop-a";
    const res = await api("overview", { shop_id: id, now: S.now }, true);
    if (res.ok) { S.other = res.data; if (S.view === "shops") render(); }
  }

  // ------------------------------------------------------------- architecture
  // Every box on the map. kind: platform (exists today), module (this engine), store, shared.
  // parts: [name, where in the code, what it does]. Reads and writes name other boxes.
  const ARCH = {
    CRM: { name: "Quote system", kind: "platform", owns: "Quotes, customers, quote page. Existing system; integrated, not rebuilt.",
      parts: [["Quote webhooks", "", "quote_sent, quote_viewed, quote_accepted and quote_dismissed, each with an event id."], ["Quote records", "", "Amount, customer, tech and status."]],
      writes: ["ING"] },
    GW: { name: "Messaging gateway", kind: "platform", owns: "SMS and email delivery. Existing system.",
      parts: [["Send API", "", "Takes a message with an idempotency key and dedupes on it."], ["Delivery and reply webhooks", "", "Delivered, failed, and customer replies, sent back into ingestion."]],
      reads: ["DEL"], writes: ["ING"] },
    UI: { name: "Owner and tech app", kind: "platform", owns: "Draft review, edit, approval and rejection. Existing system; the demo page substitutes for it.",
      parts: [["Review screen", "", "Shows each draft with its reason, tier and offer."], ["Roles", "", "Owner or tech. Only an owner can approve a costed offer."]],
      reads: ["REV"], writes: ["REV"] },
    CFG: { name: "Tenancy and config", kind: "platform", owns: "Who each shop is, its parent company, its limits, tone and offer catalogue.",
      parts: [["Shop config", "followup/platform/config.py", "Cooldown, attempt cap, quiet hours, daily cap, offer budget and cap, add-on catalogue."], ["Parent floors", "", "A parent company can set limits no shop can go below."]],
      writes: ["POL"] },
    SCH: { name: "Scheduling system", kind: "platform", owns: "Jobs and open slots for each tech.",
      parts: [["Jobs", "", "Booked hours by tech and day."], ["Open slots", "", "Free time beside existing jobs."]],
      writes: ["CAP"] },
    ING: { name: "Ingestion engine", kind: "module", stage: "Intake", code: "followup/ingestion", tests: ["test_ingest_messy.py"],
      owns: "Sole writer of the event log. Invoked per webhook.",
      parts: [["Webhook intake", "ingestion/engine.py", "Validates each event. shop_id comes from the authenticated caller, never the payload."], ["Normaliser", "ingestion/engine.py", "Phone numbers to E.164, timestamps to UTC."],
        ["Dedupe", "platform/eventlog.py", "Insert keyed on (shop_id, event_id). Duplicates are no-ops."], ["Quote loader", "ingestion/engine.py", "Loads quotes and customers from the quote system."]],
      reads: ["CRM", "GW"], writes: ["EV"] },
    PROJ: { name: "State, signals and baselines", short: "State and signals", kind: "module", stage: "Understand", code: "followup/state", tests: ["test_baselines.py", "test_ingest_messy.py"],
      owns: "Quote state and shop medians. Pure functions of the full log; independent of order and duplicates.",
      parts: [["Projector", "state/projector.py", "Projects quote state from the log with order-independent aggregates: latest timestamp, count, existence."],
        ["Signals", "state/signals.py", "Awaiting our reply, viewed since contact, view burst, returned after a gap, never viewed."],
        ["Baselines", "state/baselines.py", "Shop medians, 90-day window, minimum 15 samples, fallback to parent then platform."]],
      reads: ["EV"], writes: ["QS"] },
    CAP: { name: "Capacity reader", kind: "module", stage: "Understand", code: "followup/capacity", tests: ["test_capacity_offers.py"],
      owns: "Stateless. Reduces the schedule to slack and openings. Addresses do not cross this boundary.",
      parts: [["Slack", "capacity/base.py", "Booked hours against the shop's median week, normalised to 0 to 1."], ["Nearby openings", "capacity/base.py", "Free slot adjacent to a job near the customer."],
        ["Slot check", "capacity/base.py", "Send-time check that an offer's slot is still free."], ["Providers", "capacity/null.py, fixture.py", "NullCapacity by default. Fixture schedule for tests and the demo."]],
      reads: ["SCH"], writes: ["POL"] },
    POL: { name: "Policy engine", kind: "module", stage: "Decide", code: "followup/policy", tests: ["test_policy.py", "test_capacity_offers.py"],
      owns: "Stateless. Pure function of state, config, capacity and an explicit now. Runs on schedule, on hot events, or on demand.",
      parts: [["Thresholds", "policy/thresholds.py", "Derives thresholds from medians: big quote, hot window, stale, backoff."], ["Filters", "policy/engine.py", "Closed, held, out of attempts, in cooldown, draft in review."],
        ["Tiers", "policy/engine.py", "Five ranked tiers. One candidate per customer."], ["Strategy and framing", "policy/offers.py, framing.py", "Strategy: nudge, help, close. Framing: price hold, premium option, was/now."],
        ["Offer chooser", "policy/offers.py", "Lowest-cost applicable offer, scaled by slack, bounded by cap and budget."]],
      reads: ["QS", "CFG", "CAP", "LRN"], writes: ["AG", "DL"] },
    AG: { name: "Writer agent", kind: "module", stage: "Write and check", code: "followup/agents", tests: ["test_agent_containment.py"],
      owns: "Stateless. Decision in, text out. No database access, recipient selection or send capability.",
      parts: [["Writer input", "agents/base.py", "First name, amount, reason, offer. No phone number or other customers."], ["Template writer", "agents/template.py", "Default implementation. Fallback on model failure."],
        ["Model writer", "agents/model.py, prompt.py", "Claude, when an API key is set. Customer replies passed as quoted data."]],
      reads: ["POL"], writes: ["GRD"] },
    GRD: { name: "Guard", kind: "module", stage: "Write and check", code: "followup/guard", tests: ["test_agent_containment.py", "test_send_guardrails.py"],
      owns: "Stateless. The only component that can block a message. Deterministic code, no model.",
      parts: [["Text rules", "guard/text_rules.py", "Rejects any price, date, link or name not set by the policy, and discount terms without an attached offer."],
        ["Send rules", "guard/send_rules.py", "Approval and hash, kill switch, quote open, cooldown, offer and slot validity."], ["Limits", "guard/limits.py", "Quiet hours and daily cap, in shop local time."]],
      reads: ["AG", "DEL", "CAP"], writes: ["WF"] },
    REV: { name: "Review workflow", kind: "module", stage: "Act", code: "followup/review", tests: ["test_send_guardrails.py"],
      owns: "Drafts. At most one live draft per customer.",
      parts: [["Drafts", "review/workflow.py", "Persists guard-approved drafts with reason and signals."], ["Approval", "review/workflow.py", "Human only. Stores the body hash. Costed offers require the owner role."],
        ["Edit and reject", "review/workflow.py", "Edit clears approval. Reject snoozes the customer for the cooldown."], ["Expiry", "review/workflow.py", "Expires drafts whose quote has closed."]],
      reads: ["WF", "UI"], writes: ["WF", "UI", "DL"] },
    DEL: { name: "Delivery service", kind: "module", stage: "Act", code: "followup/delivery", tests: ["test_send_guardrails.py"],
      owns: "The outbox. At-most-once send per approved draft.",
      parts: [["Claim", "delivery/service.py", "Conditional UPDATE from approved to sending. Concurrent sends cannot both succeed."], ["Outbox", "delivery/service.py", "One row per draft, with the idempotency key."],
        ["Sender", "delivery/sender.py", "Provider interface. Outbox-only sender here; gateway adapter in production."], ["Reconcile", "delivery/service.py", "Resolves a timed-out send by querying the provider."]],
      reads: ["WF"], writes: ["GRD", "WF", "GW", "EV"] },
    LRN: { name: "Learning system", kind: "module", stage: "Learn", code: "followup/learning", tests: [], designed: true,
      owns: "Decision log implemented. Model designed, not implemented.",
      parts: [["Decision log", "learning/decision_log.py", "Implemented. Signals, baselines and action per draft."], ["Uplift model", "designed", "Estimates the incremental effect of each framing or offer over a plain message, net of cost."],
        ["Holdout", "designed", "Control group sent a plain message."]],
      reads: ["DL", "EV"], writes: ["POL"] },
    EV: { name: "Event log", kind: "store", tables: "events", owns: "Append-only. Unique on (shop_id, event_id). Source of truth; all derived state can be rebuilt from it.",
      parts: [["events", "db/schema.sql", "Type, quote, time, channel and the raw payload."]], reads: ["ING", "DEL"], writes: ["PROJ", "LRN"] },
    QS: { name: "Quote state and baselines", kind: "store", tables: "quotes, customers, baselines", owns: "Derived from the log. Recomputed, never patched.",
      parts: [["quotes, customers", "db/schema.sql", "Each quote's status and last contact."], ["baselines", "db/schema.sql", "Each median, with its sample count and where it came from."]], reads: ["PROJ"], writes: ["POL"] },
    DL: { name: "Decision log", kind: "store", tables: "decisions", owns: "Per-draft decision record, for audit and model training.",
      parts: [["decisions", "db/schema.sql", "Signals, baselines, action and policy version for each draft."]], reads: ["POL", "REV"], writes: ["LRN"] },
    WF: { name: "Drafts and outbox", kind: "store", tables: "drafts, offers, outbox", owns: "One live draft per customer. One outbox row per draft.",
      parts: [["drafts", "db/schema.sql", "Text, status, reason, approval hash and who decided."], ["offers", "db/schema.sql", "The costed offer, its expiry and slot."], ["outbox", "db/schema.sql", "Idempotency key, attempts and the provider's message id."]],
      reads: ["GRD", "REV", "DEL"], writes: ["REV", "DEL"] },
    PLAT: { name: "Shared platform code", kind: "shared", code: "followup/platform", tests: ["test_tenant_isolation.py", "test_rls_postgres.py"],
      owns: "Cross-cutting code. Enforces tenant isolation.",
      parts: [["Shop session", "platform/db.py", "All queries scoped to one shop_id. Unscoped queries raise."], ["Row level security", "db/rls_postgres.sql", "Postgres enforces the same scoping at the database level."],
        ["Config", "platform/config.py", "Shop config and limits."], ["Event log append", "platform/eventlog.py", "Single write path for events."], ["Redaction", "platform/redact.py", "Masks phone numbers in logs."], ["Shop time", "platform/timeutil.py", "Times in UTC, quiet hours in the shop's zone."]] },
  };
  const MODS = {};                       // name and summary, for the design document's diagram
  Object.entries(ARCH).forEach(([id, a]) => { MODS[id] = [a.name, a.owns]; });

  // Layout: eight columns. The engine runs left to right in stages.
  const AX = (i) => 20 + i * 142, AW = 130;
  const COL = { CRM: 0, SCH: 2, CFG: 3, UI: 6, GW: 7, ING: 0, PROJ: 1, CAP: 2, POL: 3, AG: 4, GRD: 5, REV: 6, DEL: 7, LRN: 4 };
  const STAGES = [["Intake", 0, 0], ["Understand", 1, 2], ["Decide", 3, 3], ["Write and check", 4, 5], ["Act", 6, 7]];
  const STORE_POS = { EV: [AX(0), AW], QS: [AX(1), AX(3) + AW - AX(1)], DL: [AX(4), AW], WF: [AX(5), AX(7) + AW - AX(5)] };
  const RA = 162, RB = 290, MID = 226, ST = 472, SB = 520;
  const c = (i) => AX(i) + 65;
  // [from, to, points, label, label x, label y, dashed]
  const WIRES = [
    ["CRM", "ING", [[c(0), 72], [c(0), RA]], "quotes", c(0) + 4, 90],
    ["GW", "ING", [[AX(7) + 25, 72], [AX(7) + 25, 98], [AX(0) + 100, 98], [AX(0) + 100, RA]], "delivery receipts and replies", AX(5) - 10, 94],
    ["SCH", "CAP", [[c(2), 72], [c(2), RA]], "jobs, open slots", c(2) + 4, 90],
    ["CFG", "POL", [[c(3), 72], [c(3), RA]], "limits, catalogue", c(3) + 4, 90],
    ["UI", "REV", [[c(6), 72], [c(6), RA]], "approve, edit", c(6) + 4, 90, false, true],
    ["DEL", "GW", [[c(7), RA], [c(7), 72]], "send", c(7) + 4, 90],
    ["ING", "EV", [[c(0), RB], [c(0), ST]]],
    ["EV", "PROJ", [[AX(0) + 110, ST], [AX(0) + 110, 434], [AX(1) + 30, 434], [AX(1) + 30, RB]]],
    ["PROJ", "QS", [[AX(1) + 100, RB], [AX(1) + 100, ST]]],
    ["CAP", "POL", [[AX(2) + AW, MID], [AX(3), MID]]],
    ["QS", "POL", [[AX(3) + 30, ST], [AX(3) + 30, RB]]],
    ["POL", "AG", [[AX(3) + AW, MID], [AX(4), MID]]],
    ["AG", "GRD", [[AX(4) + AW, MID], [AX(5), MID]]],
    ["GRD", "WF", [[c(5), RB], [c(5), ST]]],
    ["WF", "REV", [[c(6), ST], [c(6), RB]]],
    ["REV", "DEL", [[AX(6) + AW, MID], [AX(7), MID]]],
    ["DEL", "GRD", [[AX(7) + 15, RB], [AX(7) + 15, 306], [AX(5) + 105, 306], [AX(5) + 105, RB]], "send check", AX(6) + 26, 320, true],
    ["DEL", "WF", [[AX(7) + 70, RB], [AX(7) + 70, ST]]],
    ["DEL", "EV", [[AX(7) + 115, RB], [AX(7) + 115, 540], [AX(0) + 40, 540], [AX(0) + 40, SB]], "message_sent comes back as an event", AX(3), 553],
    ["POL", "DL", [[AX(3) + 85, RB], [AX(3) + 85, 434], [AX(4) + 30, 434], [AX(4) + 30, ST]]],
    ["REV", "DL", [[AX(6) + 20, RB], [AX(6) + 20, 434], [AX(4) + 110, 434], [AX(4) + 110, ST]]],
    ["DL", "LRN", [[c(4), ST], [c(4), 412]]],
    ["LRN", "POL", [[AX(4), 355], [AX(3) + 115, 355], [AX(3) + 115, RB]], "", 0, 0, true],
  ];
  const REPLAY = ["CRM", "ING", "EV", "PROJ", "QS", "POL", "AG", "GRD", "WF", "REV", "DEL", "GW"];
  const REPLAY_STEPS = [
    "Quote system posts a quote_viewed webhook.", "Ingestion validates it, sets shop_id from the caller, inserts once.", "Event appended. Its position is set by timestamp, not arrival order.",
    "Projector rebuilds quote state. Signal set: viewed since last contact.", "Quote state and shop medians persisted.",
    "Policy: view inside the hot window, tier 2 candidate.", "Writer renders the decision as text.", "Guard validates every amount, date and name against the decision.",
    "Draft stored with status draft.", "Owner approves. Body hash stored.", "Delivery claims the draft, re-runs send checks, sends once.",
    "Gateway sends. Delivery appends message_sent to the event log.",
  ];

  function archBox(id, x, y, w, h) {
    const a = ARCH[id], lit = S.replay || [], on = S.arch === id || lit[lit.length - 1] === id;
    const near = !on && S.arch && WIRES.some((wr) => (wr[0] === S.arch && wr[1] === id) || (wr[1] === S.arch && wr[0] === id));
    let out = '<g class="node click arch-' + a.kind + (on ? " hit" : "") + (near ? " near" : "") + (a.designed ? " designed" : "") + '" data-arch="' + id + '">' +
      '<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="8"/>';
    const title = a.short || a.name;
    if (a.kind === "module") {
      out += '<text x="' + (x + 9) + '" y="' + (y + 18) + '" font-size="11.5" font-weight="700">' + esc(title) + "</text>" +
        '<line x1="' + (x + 9) + '" x2="' + (x + w - 9) + '" y1="' + (y + 26) + '" y2="' + (y + 26) + '" stroke="var(--line)"/>';
      a.parts.forEach((p, i) => {
        out += '<text class="' + (p[1] === "designed" ? "dim" : "") + '" x="' + (x + 9) + '" y="' + (y + 42 + i * 15) + '" font-size="9.5">' +
          (p[1] === "designed" ? "○ " : "• ") + esc(p[0]) + "</text>";
      });
    } else if (a.kind === "store") {
      out += '<text x="' + (x + w / 2) + '" y="' + (y + 20) + '" font-size="11.5" font-weight="700" text-anchor="middle">' + esc(a.name) + "</text>" +
        '<text class="dim" x="' + (x + w / 2) + '" y="' + (y + 36) + '" font-size="9.5" text-anchor="middle">' + esc(a.tables) + "</text>";
    } else {
      out += '<text x="' + (x + w / 2) + '" y="' + (y + h / 2 + 4) + '" font-size="11" text-anchor="middle">' + esc(title) + "</text>";
    }
    return out + "</g>";
  }

  function archMap() {
    const lit = S.replay || [];
    const litWire = (a, b) => REPLAY.some((x, i) => x === a && REPLAY[i + 1] === b && i + 1 < lit.length) || (a === "DEL" && b === "EV" && lit.length >= REPLAY.length);
    let svg = '<svg viewBox="0 0 1164 660" width="100%" style="min-width:900px" role="img" aria-label="Systems and subsystems of the follow-up engine">' +
      '<defs><marker id="ah" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="var(--muted)"/></marker>' +
      '<marker id="ah-hit" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="var(--accent)"/></marker></defs>' +
      '<rect class="band" x="8" y="8" width="1148" height="76" rx="10"/><text class="dim" x="18" y="24" font-size="10" font-weight="700">EXISTING PLATFORM</text>' +
      '<rect class="band" x="8" y="112" width="1148" height="312" rx="10"/><text class="dim" x="18" y="128" font-size="10" font-weight="700">FOLLOW-UP ENGINE</text>' +
      '<rect class="band" x="8" y="444" width="1148" height="116" rx="10"/><text class="dim" x="18" y="460" font-size="10" font-weight="700">STORAGE, ONE DATABASE, EVERY TABLE KEYED BY SHOP</text>' +
      '<rect class="band" x="8" y="576" width="1148" height="76" rx="10"/><text class="dim" x="18" y="592" font-size="10" font-weight="700">SHARED CODE, USED BY EVERY MODULE</text>';
    STAGES.forEach(([label, a, b]) => {
      svg += '<rect class="stage" x="' + (AX(a) - 6) + '" y="138" width="' + (AX(b) + AW + 6 - (AX(a) - 6)) + '" height="160" rx="9"/>' +
        '<text class="dim" x="' + AX(a) + '" y="153" font-size="9.5" font-weight="700">' + label.toUpperCase() + "</text>";
    });
    svg += '<rect class="stage" x="' + (AX(4) - 6) + '" y="310" width="' + (AW + 12) + '" height="108" rx="9"/><text class="dim" x="' + AX(4) + '" y="323" font-size="9.5" font-weight="700">LEARN</text>';
    WIRES.forEach(([a, b, pts, label, lx, ly, dashed, both]) => {
      const hit = S.arch === a || S.arch === b || litWire(a, b);
      svg += '<path class="edge' + (hit ? " hit" : "") + '" d="M' + pts.map((p) => p.join(" ")).join(" L") + '"' + (dashed ? ' stroke-dasharray="4 3"' : "") +
        ' marker-end="url(#' + (hit ? "ah-hit" : "ah") + ')"' + (both ? ' marker-start="url(#' + (hit ? "ah-hit" : "ah") + ')"' : "") + ' stroke-linejoin="round"/>';
      if (label) svg += '<text class="' + (hit ? "" : "dim") + '" x="' + lx + '" y="' + ly + '" font-size="9">' + esc(label) + "</text>";
    });
    ["CRM", "SCH", "CFG", "UI", "GW"].forEach((id) => { svg += archBox(id, AX(COL[id]), 30, AW, 42); });
    ["ING", "PROJ", "CAP", "POL", "AG", "GRD", "REV", "DEL"].forEach((id) => { svg += archBox(id, AX(COL[id]), RA, AW, RB - RA); });
    svg += archBox("LRN", AX(4), 328, AW, 84);
    Object.entries(STORE_POS).forEach(([id, [x, w]]) => { svg += archBox(id, x, ST, w, SB - ST); });
    const chips = ARCH.PLAT.parts.map((p) => p[0]), cw = (1132 - (chips.length - 1) * 10) / chips.length;
    svg += '<g class="node click arch-shared' + (S.arch === "PLAT" ? " hit" : "") + '" data-arch="PLAT">';
    chips.forEach((name, i) => {
      const x = 16 + i * (cw + 10);
      svg += '<rect x="' + x + '" y="602" width="' + cw + '" height="36" rx="8"/><text x="' + (x + cw / 2) + '" y="624" font-size="10.5" text-anchor="middle">' + esc(name) + "</text>";
    });
    return svg + "</g></svg>";
  }

  // A popover beside the clicked box, so the explanation appears where the reader is looking.
  function showArchPop(g, id, inDoc) {
    if (!g) return;
    const a = ARCH[id], wrap = g.closest(".arch-wrap"), pop = wrap.querySelector(".arch-pop");
    const kind = { platform: "Existing platform", module: "Engine module", store: "Storage", shared: "Shared code" }[a.kind];
    pop.innerHTML = '<div class="row"><b>' + esc(a.name) + '</b><span class="badge">' + kind + "</span>" + (a.stage ? '<span class="badge">' + esc(a.stage) + "</span>" : "") +
      (a.designed ? '<span class="badge warn">partly built</span>' : "") + '<span class="spacer"></span><button class="small" data-popclose title="Close">Close</button></div>' +
      "<p>" + esc(a.owns) + '</p><ul class="plain">' + a.parts.map((p) => "<li><b>" + esc(p[0]) + ".</b> " + esc(p[2]) + (p[1] === "designed" ? ' <span class="badge warn">designed</span>' : "") + "</li>").join("") + "</ul>" +
      (a.code ? '<p class="muted small">Code: <span class="mono">' + esc(a.code) + "</span>" + (inDoc ? "" : ". Files and tests: panel below the map.") + "</p>" : "");
    pop.hidden = false;
    const wr = wrap.getBoundingClientRect(), gr = g.getBoundingClientRect();
    const w = Math.min(360, wr.width - 16);
    pop.style.width = w + "px";
    const h = pop.offsetHeight, bx = gr.left - wr.left, by = gr.top - wr.top;
    let left = bx + gr.width / 2 - w / 2, top;
    if (by + gr.height + 6 + h <= wr.height - 4) top = by + gr.height + 6;          // below the box
    else if (by - h - 6 >= 4) top = by - h - 6;                                      // above it
    else {                                                                           // beside it
      top = Math.max(4, Math.min(by, wr.height - h - 4));
      left = bx + gr.width + 8 + w <= wr.width - 8 ? bx + gr.width + 8 : bx - w - 8;
    }
    pop.style.left = Math.max(8, Math.min(left, wr.width - w - 8)) + "px";
    pop.style.top = top + "px";
  }

  VIEWS.architecture = function () {
    const a = ARCH[S.arch] || ARCH.ING;
    const name = (id) => '<a href="#" data-arch="' + id + '">' + esc(ARCH[id].name) + "</a>";
    const kind = { platform: ["Existing platform", ""], module: ["Engine module", "info"], store: ["Storage", ""], shared: ["Shared code", ""] }[a.kind];
    const parts = a.parts.map((p) => "<tr><td><b>" + esc(p[0]) + '</b></td><td class="mono small">' + (p[1] === "designed" ? '<span class="badge warn">designed</span>' : esc(p[1])) +
      "</td><td>" + esc(p[2]) + "</td></tr>").join("");
    const io = (label, ids) => ids && ids.length ? "<dt>" + label + "</dt><dd>" + ids.map(name).join(", ") + "</dd>" : "";
    const lit = S.replay || [];
    const steps = REPLAY_STEPS.map((t, i) => '<li class="' + (lit.length === i + 1 ? "on" : lit.length > i ? "done" : "") + '">' + t + "</li>").join("");
    return '<div class="card"><div class="row"><h2>Systems and subsystems</h2><span class="spacer"></span><button class="primary" data-act="replay">Replay one event</button></div>' +
      '<p class="lead muted">Five stages, left to right. Modules communicate through storage, not direct calls. Click a box for its components.</p>' +
      '<div class="scroll"><div class="arch-wrap">' + archMap() + '<div class="arch-pop" hidden></div></div></div></div>' +
      '<div class="grid" style="margin-top:14px"><div class="card"><div class="row"><h2>' + esc(a.name) + '</h2><span class="badge ' + kind[1] + '">' + kind[0] + "</span>" +
      (a.stage ? '<span class="badge">' + esc(a.stage) + "</span>" : "") + (a.designed ? '<span class="badge warn">partly built</span>' : "") + "</div>" +
      '<p class="lead">' + esc(a.owns) + "</p>" +
      '<div class="scroll"><table><thead><tr><th>Part</th><th>Where</th><th>What it does</th></tr></thead><tbody>' + parts + "</tbody></table></div>" +
      '<dl class="kv" style="margin-top:10px">' + io("Takes from", a.reads) + io("Gives to", a.writes) +
      (a.code ? '<dt>Code</dt><dd class="mono small">' + esc(a.code) + "</dd>" : "") + (a.tests && a.tests.length ? '<dt>Tests</dt><dd class="mono small">' + a.tests.map((t) => "tests/" + t).join(", ") + "</dd>" : "") + "</dl></div>" +
      '<div class="card"><h2>Trace: one quote_viewed event</h2><p class="lead muted">Replay one event highlights each step on the map.</p><ol class="replay-steps">' + steps + "</ol>" +
      "<h3>Properties</h3><ul class=\"plain\"><li>Modules fail independently. Writer failure falls back to the template; schedule outage holds offers at base.</li>" +
      "<li>Sends are recorded as events. Last contact has a single source.</li><li>All storage is keyed by shop_id, enforced by Postgres RLS.</li>" +
      "<li>Modules are in-process packages here. The same contracts hold as separate services.</li></ul></div></div>";
  };

  // ------------------------------------------------------------------- design
  const JUMP = { "4": "architecture", "7": "messy", "8": "queue", "9": "policy", "10": "guard", "11": "guard", "12": "review", "14": "shops", "15": "tests" };
  VIEWS.design = () => '<div class="card doc" id="doc"><p class="muted">Loading the design document.</p></div>';
  async function renderDesign() {
    const el = $("#doc");
    try {
      if (!S.design) {
        const b = window.FOLLOWUP_BUNDLE;
        S.design = b ? b.design : await (await fetch("/design.md")).text();
      }
      if (!window.marked) await window.FollowupTransport.loadScript("https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js");
      if (!window.mermaid) await window.FollowupTransport.loadScript("https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js");
      if (S.view !== "design") return;
      el.innerHTML = window.marked.parse(S.design);
      el.querySelectorAll("pre code.language-mermaid").forEach((code, i) => {
        const div = document.createElement("div");
        if (i === 0) {                 // the module map: show the same drawing as the Architecture tab
          div.className = "doc-arch";
          div.innerHTML = '<div class="scroll"><div class="arch-wrap">' + archMap() + '<div class="arch-pop" hidden></div></div></div><div class="diagram-info">Click a box for its components. <a href="#" data-tab="architecture">Open it in the Architecture tab</a> for the event trace.</div>';
        } else { div.className = "mermaid"; div.textContent = code.textContent; }
        code.parentElement.replaceWith(div);
      });
      el.querySelectorAll("h2").forEach((h) => {
        const n = (h.textContent.match(/^(\d+)\./) || [])[1];
        if (JUMP[n]) {
          const p = document.createElement("p");
          p.className = "jump";
          p.innerHTML = '<a href="#" data-tab="' + JUMP[n] + '">Open the live view for this section</a>';
          h.after(p);
        }
      });
      const dark = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() === "#15161a";
      window.mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "neutral", securityLevel: "strict" });
      await window.mermaid.run({ nodes: el.querySelectorAll(".mermaid") });
      wireDocDiagrams(el);
    } catch (err) {
      el.innerHTML = '<div class="note bad">The document could not be rendered: ' + esc(err.message) + "</div><pre>" + esc(S.design || "") + "</pre>";
    }
  }

  // Each design document diagram gets a note line. Clicking a node fills it.
  function docKey(g) {
    const id = g.id || "";
    const m = id.match(/^(?:flowchart|state)-(.+?)-\d+$/) || id.match(/^entity-(.+?)-/);
    return m ? m[1] : (g.textContent || "").trim();
  }
  function wireDocDiagrams(el) {
    const byName = {};
    Object.values(MODS).forEach((m) => { byName[m[0]] = m[1]; });
    byName["Decision log and models"] = MODS.DL[1] + " It also holds the trained models.";
    el.querySelectorAll(".mermaid").forEach((div, i) => {
      const table = DOC_INFO[i];
      const box = document.createElement("div");
      box.className = "diagram-info";
      box.textContent = "Click a component for its description.";
      div.after(box);
      const lookup = (g) => {
        const key = docKey(g), label = (g.textContent || "").trim();
        if (!table) return byName[label] ? [label, byName[label]] : null;
        const v = table[key] || table[label];
        if (!v) return null;
        return [g.matches("g[id^='entity-']") ? key : label, INFO[v] || v];
      };
      div.querySelectorAll("g.node, g[id^='entity-'], text.actor").forEach((g) => {
        if (!lookup(g)) return;
        g.style.cursor = "pointer";
        g.addEventListener("click", () => {
          const [label, text] = lookup(g);
          box.className = "diagram-info on";
          box.textContent = "";
          const b = document.createElement("b");
          b.textContent = (label.length > 60 ? label.slice(0, 57) + "..." : label) + ". ";
          if (text.toLowerCase().startsWith(label.toLowerCase())) box.append(document.createTextNode(text));
          else box.append(b, document.createTextNode(text));
        });
      });
    });
  }

  // -------------------------------------------------------------------- tests
  VIEWS.tests = function () {
    const t = S.tests;
    if (!t) return '<div class="card muted">Loading the last test run.</div>';
    if (t.missing) return '<div class="card"><h2>Tests</h2><p class="muted">No test report is bundled with this page. Run <code>python tools/build_site.py</code>, or <code>pytest</code> in the project.</p></div>';
    const groups = t.files.map((f) => '<details class="card" style="margin-bottom:10px"><summary><b>' + esc(f.title) + '</b> <span class="badge ' + (f.failed ? "bad" : "ok") + '">' + f.passed + " passed" +
      (f.failed ? ", " + f.failed + " failed" : "") + (f.skipped ? ", " + f.skipped + " skipped" : "") + '</span> <span class="muted small mono">' + esc(f.file) + "</span></summary><table><tbody>" +
      f.tests.map((x) => '<tr><td><span class="badge ' + (x.outcome === "passed" ? "ok" : x.outcome === "skipped" ? "" : "bad") + '">' + x.outcome + "</span></td><td>" + esc(x.name) + "</td></tr>").join("") + "</tbody></table></details>").join("");
    return '<div class="card" style="margin-bottom:14px"><div class="row"><h2>Last test run</h2><span class="spacer"></span><span class="badge ok">' + t.passed + ' passed</span><span class="badge ' + (t.failed ? "bad" : "") + '">' + t.failed +
      ' failed</span><span class="badge">' + t.skipped + ' skipped</span></div><p class="lead muted">Recorded when this page was built. The page is not built if a test fails.' + (t.skipped ? " The skipped tests need a Postgres database." : "") + '</p></div>' + groups;
  };
  async function loadTests() {
    if (S.tests) return;
    const b = window.FOLLOWUP_BUNDLE;
    try { S.tests = b ? b.tests : await (await fetch("/web/test-report.json")).json(); }
    catch (e) { S.tests = { missing: true }; }
    if (!S.tests || !S.tests.files) S.tests = { missing: true };
    if (S.view === "tests") render();
  }

  // ------------------------------------------------------------------ actions
  const fill = (text, q) => text.replace(/\{first\}/g, q.customer_name.split(" ")[0]).replace(/\{tech\}/g, q.tech_name.split(" ")[0]).replace(/\{amount\}/g, money(q.amount_cents)).replace(/\{other\}/g, "Mrs Okafor");
  const curQuote = () => S.data.quotes.find((x) => x.id === S.quote) || S.data.quotes[0];

  const ACT = {
    goto: (el) => { S.view = el.dataset.view; },
    run: async () => { const r = await api("run", { shop_id: S.shop, now: S.now }); if (r.ok) note("Engine ran: " + r.data.created.length + " drafts created" + (r.data.expired.length ? ", " + r.data.expired.length + " expired" : "") + ".", r.data.created.length ? "ok" : ""); },
    approve: async (el) => { const r = await api("approve", { shop_id: S.shop, draft_id: el.dataset.id, actor: "user:" + S.role, role: S.role, now: S.now }); if (r.ok) note("Approved by the " + S.role + ". Body hash stored.", "ok"); S.draft = el.dataset.id; },
    "approve-all": async () => { let n = 0; for (const d of S.data.drafts.filter((x) => x.status === "draft")) { const r = await api("approve", { shop_id: S.shop, draft_id: d.id, actor: "user:" + S.role, role: S.role, now: S.now }, true); if (r.ok) n++; } note(n + " drafts approved as " + S.role + ".", "ok"); },
    send: async (el) => {
      S.draft = el.dataset.id;
      const r = await api("send", { shop_id: S.shop, draft_id: el.dataset.id, now: S.now });
      if (!r.ok) return;
      const x = r.data, kind = x.result === "sent" ? "ok" : x.result === "noop" ? "" : "warn";
      note({ sent: "Sent once.", noop: "Nothing sent: " + x.reason + ".", failed: "Provider rejected it. Draft is failed and can be retried with the same key.", unknown: "Provider did not answer. Draft stays in sending. It will not be retried until the provider is asked." }[x.result] || ("Not sent (" + x.rule + "): " + x.reason + "."), kind);
    },
    reconcile: async (el) => { const r = await api("reconcile", { shop_id: S.shop, draft_id: el.dataset.id, now: S.now }); if (r.ok) note("Asked the provider: " + r.data.reason + ".", r.data.result === "sent" ? "ok" : "warn"); },
    reject: async (el) => { const r = await api("reject", { shop_id: S.shop, draft_id: el.dataset.id, actor: "user:" + S.role, now: S.now }); if (r.ok) note("Rejected. Customer snoozed for the cooldown.", ""); },
    accept: async (el) => { const r = await api("accept_quote", { shop_id: S.shop, quote_id: el.dataset.id, now: S.now }); if (r.ok) note("The customer accepted " + el.dataset.id + ". The draft is unchanged; sending it should be refused.", "warn"); },
    edit: (el) => { S.editing = el.dataset.id; },
    cancel: () => { S.editing = null; },
    save: async (el) => { const r = await api("edit", { shop_id: S.shop, draft_id: el.dataset.id, body: $("#edit-body").value, actor: "user:" + S.role, now: S.now }); S.editing = null; if (r.ok) note("Edited. Approval was cleared" + (r.data.warnings.length ? ". Guard warnings: " + r.data.warnings.map((w) => w.detail).join("; ") : "") + ".", r.data.warnings.length ? "warn" : ""); },
    messy: async () => { const r = await api("messy_check", { shop_id: S.shop, shuffle_seed: Math.floor(Math.random() * 1e6), duplicate_fraction: parseFloat($("#dup").value), now: S.now }); if (r.ok) S.messy = r.data; },
    late: async () => { const at = toIso(ms(S.now) - 48 * 3600e3); const r = await api("ingest", { shop_id: S.shop, events: [{ event_id: "late-" + Date.now(), type: "quote_viewed", quote_id: S.quote, timestamp: at }] }); if (r.ok) note("quote_viewed dated " + when(at) + " ingested for " + S.quote + ". Stored: " + r.data.stored + ". See the Queue timeline.", "ok"); },
    "fill-slot": async () => { await api("fill_slot", { shop_id: S.shop, slot_id: "S-demo" }); S.opening = false; note("Slot filled. Approved drafts referencing it are refused at send.", "warn"); },
    "cap-down": async () => { await setCapacity({ fail: true }); note("Schedule service unavailable. Offers fall back to base.", "warn"); },
    "cap-clear": async () => { S.opening = false; await api("set_capacity", { shop_id: S.shop }); note("Schedule disconnected.", ""); },
    case: (el) => { const c = S.cases.find((x) => x.id === el.dataset.id); S.guardText = fill(c.text, curQuote()); S.guardRaw = c.text; S.guardResult = null; return checkGuard(); },
    "guard-check": () => { S.guardText = $("#guard-text").value; S.guardRaw = null; return checkGuard(); },
    "guard-run": () => guardRun("hostile"),
    "guard-broken": () => guardRun("broken"),
    leak: async () => {
      const mine = S.shop, other = mine === "shop-a" ? "shop-b" : "shop-a";
      await api("run", { shop_id: other, now: S.now }, true);
      const o = await api("overview", { shop_id: other, now: S.now }, true);
      const target = o.data.drafts[0];
      const rows = [];
      for (const [what, name, id] of [["Approve the other shop's draft", "approve", target.id], ["Send the other shop's draft", "send", target.id], ["Approve an id that does not exist", "approve", "D-0000000000"]]) {
        const r = await api(name, { shop_id: mine, draft_id: id, now: S.now }, true);
        rows.push({ what, answer: r.ok ? "accepted" : r.error.code + ": " + r.error.message });
      }
      const after = await api("overview", { shop_id: other, now: S.now }, true);
      rows.push({ what: "The other shop's draft afterwards", answer: "status " + after.data.drafts.find((x) => x.id === target.id).status + ", unchanged" });
      S.leak = rows; S.other = after.data;
    },
    "sim-start": () => simStart(),
    "sim-pause": () => simStop(),
    replay: () => { let i = 0; S.replay = []; clearInterval(S.replayTimer); S.replayTimer = setInterval(() => { S.replay = REPLAY.slice(0, ++i); S.arch = REPLAY[i - 1]; if (S.view === "architecture") render(); if (i >= REPLAY.length) { clearInterval(S.replayTimer); setTimeout(() => { S.replay = null; if (S.view === "architecture") render(); }, 1400); } }, 520); },
  };

  async function checkGuard() {
    const d = S.data, q = curQuote(), draft = d.drafts.find((x) => x.quote_id === q.id && ["draft", "approved"].includes(x.status));
    const r = await api("guard_check", draft ? { shop_id: S.shop, text: S.guardText, draft_id: draft.id } : { shop_id: S.shop, text: S.guardText, quote_id: q.id, now: S.now });
    if (r.ok) S.guardResult = r.data;
  }
  async function guardRun(mode) {
    const text = S.guardRaw || ($("#guard-text") ? $("#guard-text").value : S.guardText);
    await T.reset();
    await api("set_writer", mode === "hostile" ? { mode: "hostile", text } : { mode: "broken" });
    const r = await api("run", { shop_id: S.shop, now: S.now });
    const o = await api("overview", { shop_id: S.shop, now: S.now }, true);
    await api("set_writer", { mode: "template" });
    if (r.ok && o.ok) S.guardRun = { created: r.data.created.length, byTemplate: o.data.drafts.filter((x) => x.written_by === "template").length, log: o.data.guard_log };
    S.other = null; S.leak = null;
  }
  async function setCapacity(extra) {
    const q = curQuote(), booked = $("#booked") ? parseFloat($("#booked").value) : 160;
    const openings = S.opening ? [{ slot_id: "S-demo", tech_name: q.tech_name, label: "Thursday afternoon", ends_at: toIso(ms(S.now) + 72 * 3600e3), near_customer_ids: [q.customer_id] }] : [];
    await api("set_capacity", Object.assign({ shop_id: S.shop, booked_hours: booked, median_booked_hours: 160, available_hours: 200, openings }, extra || {}));
  }

  // ------------------------------------------------------------------ live simulation
  // Drives the real engine on an empty shop. Seed quotes arrive one at a time in random order,
  // re-dated to the simulated clock. The engine drafts, the owner approves, messages go out, and
  // customers answer. Accepted jobs fill the techs' weeks, and the engine reads the new booked
  // total, so offers shrink as the schedule fills. Only the customer behaviour is invented here.
  const LIVE = "shop-live";
  const TECH_HOURS = 80;               // two weeks per tech
  const SIM_START = "2026-08-17T12:00:00Z";
  const SIM = { running: false, busy: false, timer: null, speed: 1, clock: SIM_START, started: false, pool: [], seq: 0, techs: {},
    pending: [], scheduled: new Set(), feed: [], stats: { arrived: 0, sent: 0, accepted: 0, declined: 0, won: 0, discount: 0, offers: 0 },
    data: null, open: 0, error: null };
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const jobHours = (cents) => Math.max(3, Math.min(32, Math.round(cents / 100 / 400)));
  const shopHour = (t) => new Date(t + SHOP_OFFSET_H * 3600e3).getUTCHours();
  const phoneOf = (customerId) => "+" + customerId.slice(2);

  function simFeed(text, kind) {
    SIM.feed.unshift({ at: when(SIM.clock), text, kind: kind || "" });
    SIM.feed = SIM.feed.slice(0, 40);
  }

  async function simSetup() {
    const seed = await api("overview", { shop_id: "shop-a", now: DEFAULT_NOW }, true);
    if (!seed.ok) { SIM.error = "The engine is not reachable."; return false; }
    SIM.pool = seed.data.quotes.map((q) => ({ name: q.customer_name, phone: phoneOf(q.customer_id), tech: q.tech_name, cents: q.amount_cents, id: q.id }))
      .sort(() => Math.random() - 0.5);
    const techs = [...new Set(SIM.pool.map((q) => q.tech))].sort();
    SIM.techs = {};
    techs.forEach((t) => { SIM.techs[t] = { booked: 14 + Math.round(Math.random() * 18), cap: TECH_HOURS, flash: 0 }; });
    SIM.started = true;
    simFeed("Start: " + techs.length + " techs, about one third booked over the next two weeks.", "");
    await simCapacity();
    return true;
  }

  async function simCapacity() {
    const t = Object.values(SIM.techs);
    const booked = t.reduce((n, x) => n + x.booked, 0), avail = t.reduce((n, x) => n + x.cap, 0);
    await api("set_capacity", { shop_id: LIVE, booked_hours: booked, median_booked_hours: Math.round(avail * 0.8), available_hours: avail, openings: [] }, true);
  }

  async function simArrive() {
    SIM.seq += 1;
    const fresh = SIM.seq <= SIM.pool.length;
    const base = SIM.pool[(SIM.seq - 1) % SIM.pool.length];
    const id = fresh ? base.id : base.id + "-" + Math.ceil(SIM.seq / SIM.pool.length);
    const cents = fresh ? base.cents : Math.round(base.cents * (0.75 + Math.random() * 0.5) / 5000) * 5000;
    const phone = fresh ? base.phone : base.phone.slice(0, -4) + String(1000 + ((SIM.seq * 7919) % 9000));
    const q = { id, customer_name: base.name, customer_phone: phone, tech_name: base.tech, amount: cents / 100, status: "open", created_at: SIM.clock };
    const r = await api("add_quotes", { shop_id: LIVE, quotes: [q] }, true);
    if (!r.ok) return;
    await api("ingest", { shop_id: LIVE, events: [{ event_id: "sim-q-" + id, type: "quote_sent", quote_id: id, timestamp: SIM.clock }] }, true);
    SIM.stats.arrived += 1;
    simFeed(base.tech + " quoted " + base.name + " " + money(cents) + (fresh ? "" : " (a repeat job)") + ".", "");
  }

  async function simView(q) {
    await api("ingest", { shop_id: LIVE, events: [{ event_id: "sim-v-" + q.id + "-" + ms(SIM.clock), type: "quote_viewed", quote_id: q.id, timestamp: SIM.clock }] }, true);
  }

  async function simResolve(p) {
    const q = SIM.data && SIM.data.quotes.find((x) => x.id === p.quote_id);
    if (!q || q.status !== "open") return;
    if (p.outcome === "accept") {
      await api("accept_quote", { shop_id: LIVE, quote_id: q.id, now: SIM.clock }, true);
      // The tech who quoted does the job if it fits in their two weeks. Otherwise dispatch gives it
      // to whoever has the most free time.
      const h = jobHours(q.amount_cents);
      let who = SIM.techs[q.tech_name] ? q.tech_name : Object.keys(SIM.techs)[0];
      if (SIM.techs[who].booked + h > SIM.techs[who].cap) who = Object.keys(SIM.techs).sort((a, b) => SIM.techs[a].booked - SIM.techs[b].booked)[0];
      SIM.techs[who].booked += h; SIM.techs[who].flash = 3;
      SIM.stats.accepted += 1; SIM.stats.won += q.amount_cents - (p.offer || 0); SIM.stats.discount += p.offer || 0;
      simFeed(q.customer_name + " accepted " + money(q.amount_cents) + (p.offer ? " with " + money(p.offer) + " off" : p.framing ? " after a " + p.framing.replace(/_/g, " ") : "") +
        ". " + (who === q.tech_name ? who + " +" + h + " h." : q.tech_name + " is full, so dispatch gives it to " + who + ", +" + h + " h."), "ok");
    } else if (p.outcome === "decline") {
      await api("add_quotes", { shop_id: LIVE, quotes: [{ id: q.id, customer_name: q.customer_name, customer_phone: phoneOf(q.customer_id), tech_name: q.tech_name,
        amount: q.amount_cents / 100, status: "dismissed", created_at: q.created_at }] }, true);
      SIM.stats.declined += 1;
      simFeed(q.customer_name + " declined the " + money(q.amount_cents) + " quote.", "bad");
    } else if (p.outcome === "view") {
      await simView(q);
    } else if (p.outcome === "reply") {
      await api("ingest", { shop_id: LIVE, events: [{ event_id: "sim-r-" + q.id + "-" + ms(SIM.clock), type: "customer_replied", quote_id: q.id, timestamp: SIM.clock, channel: "sms" }] }, true);
      simFeed(q.customer_name + " replied. Tier 1 on the next run.", "");
    }
  }

  function simSchedule(d) {
    if (SIM.scheduled.has(d.id)) return;
    SIM.scheduled.add(d.id);
    SIM.stats.sent += 1;
    const offer = d.offer && d.offer.value_cents ? d.offer.value_cents : 0, framing = d.framing ? d.framing.kind : null;
    if (offer) SIM.stats.offers += 1;
    const pa = 0.16 + (offer ? 0.24 : 0) + (framing ? 0.08 : 0) + (d.strategy === "reply" ? 0.2 : 0), pd = 0.12, r = Math.random();
    const outcome = r < pa ? "accept" : r < pa + pd ? "decline" : r < pa + pd + 0.25 ? "view" : r < pa + pd + 0.32 ? "reply" : "silent";
    SIM.pending.push({ at: ms(SIM.clock) + (3 + Math.random() * 30) * 3600e3, quote_id: d.quote_id, outcome, offer, framing });
    const q = SIM.data && SIM.data.quotes.find((x) => x.id === d.quote_id);
    simFeed("Sent to " + (q ? q.customer_name : d.quote_id) + ": " + d.strategy + (offer ? ", " + money(offer) + " off" : framing ? ", " + framing.replace(/_/g, " ") : "") + ".", "info");
  }

  async function simTick() {
    if (SIM.busy) return;
    SIM.busy = true;
    try {
      if (!SIM.started && !(await simSetup())) { simStop(); return; }
      let t = ms(SIM.clock) + 2 * 3600e3;
      if (shopHour(t) >= 20 || shopHour(t) < 8) {           // overnight: skip to 8:00 shop time
        while (shopHour(t) !== 8) t += 3600e3;
        // The two-week window rolls forward a day: a day's work is done and leaves the schedule.
        for (const k in SIM.techs) SIM.techs[k].booked = Math.max(0, SIM.techs[k].booked - 3);
      }
      SIM.clock = toIso(t);
      for (const k in SIM.techs) SIM.techs[k].flash = Math.max(0, SIM.techs[k].flash - 1);

      if (SIM.seq === 0) { for (let i = 0; i < 6; i++) await simArrive(); }
      else if (Math.random() < 0.8) await simArrive();

      const due = SIM.pending.filter((p) => p.at <= t);
      SIM.pending = SIM.pending.filter((p) => p.at > t);
      for (const p of due) await simResolve(p);

      const open = SIM.data ? SIM.data.quotes.filter((q) => q.status === "open") : [];
      if (open.length && Math.random() < 0.6) await simView(pick(open));
      if (open.length && Math.random() < 0.04) {
        const q = pick(open);
        SIM.pending.push({ at: t, quote_id: q.id, outcome: "accept", offer: 0, framing: null });
      }

      await simCapacity();
      await api("run", { shop_id: LIVE, now: SIM.clock }, true);
      let o = await api("overview", { shop_id: LIVE, now: SIM.clock }, true);
      if (!o.ok) return;
      SIM.data = o.data;
      for (const d of o.data.drafts.filter((x) => x.status === "draft")) await api("approve", { shop_id: LIVE, draft_id: d.id, role: "owner", actor: "user:owner", now: SIM.clock }, true);
      for (const d of o.data.drafts.filter((x) => x.status === "draft" || x.status === "approved")) await api("send", { shop_id: LIVE, draft_id: d.id, now: SIM.clock }, true);
      o = await api("overview", { shop_id: LIVE, now: SIM.clock }, true);
      if (o.ok) {
        SIM.data = o.data;
        o.data.drafts.filter((x) => x.status === "sent").forEach(simSchedule);
      }
    } catch (err) {
      SIM.error = String(err);
      simStop();
    } finally {
      SIM.busy = false;
      if (S.view === "sim") render();
    }
  }

  function simStart() {
    if (!T.live) { SIM.error = "The simulation needs the live engine. This page fell back to a recorded run."; return; }
    SIM.running = true;
    clearInterval(SIM.timer);
    SIM.timer = setInterval(simTick, 1400 / SIM.speed);
    simTick();
  }
  function simStop() { SIM.running = false; clearInterval(SIM.timer); }

  VIEWS.sim = function () {
    const techs = Object.entries(SIM.techs);
    const booked = techs.reduce((n, [, x]) => n + x.booked, 0), avail = techs.reduce((n, [, x]) => n + x.cap, 0) || 1;
    const cap = SIM.data ? SIM.data.capacity : null, cfg = (SIM.data || S.data || {}).config || {};
    const slack = cap && cap.connected ? cap.shop_slack : null;
    const rate = slack == null || !cfg.percent_base ? null : discountRate(slack, cfg) * 100;
    const bars = techs.map(([name, x]) => {
      const pct = Math.min(100, (x.booked / x.cap) * 100), over = x.booked >= x.cap;
      return '<div class="sim-tech"><div class="sim-tech-name">' + esc(name) + '</div><div class="sim-bar"><div class="sim-fill' + (over ? " full" : pct >= 80 ? " busy" : "") + (x.flash ? " flash" : "") +
        '" style="width:' + pct.toFixed(1) + '%"></div><div class="sim-median" title="The shop usually books 80%"></div></div><div class="sim-hours">' + x.booked + " / " + x.cap + " h</div></div>";
    }).join("");
    const st = SIM.stats;
    const open = SIM.data ? SIM.data.quotes.filter((q) => q.status === "open").length : 0;
    const kpi = (n, l) => '<div class="sim-kpi"><div class="sim-kpi-n">' + n + '</div><div class="muted small">' + l + "</div></div>";
    const feed = SIM.feed.map((f) => '<div class="sim-line ' + f.kind + '"><span class="mono small muted">' + esc(f.at) + "</span> " + esc(f.text) + "</div>").join("");
    return '<div class="grid"><div class="card">' +
      '<div class="row"><h2>Live simulation</h2><span class="spacer"></span>' +
      (SIM.running ? '<button data-act="sim-pause">Pause</button>' : '<button class="primary" data-act="sim-start">' + (SIM.started ? "Resume" : "Start") + "</button>") +
      '<select data-pick="sim-speed"><option value="1"' + (SIM.speed === 1 ? " selected" : "") + '>Normal speed</option><option value="2"' + (SIM.speed === 2 ? " selected" : "") + '>Fast</option></select></div>' +
      '<p class="lead muted">Engine running on an empty shop. Seed quotes arrive one at a time in random order; drafts are auto-approved and sent. ' +
      "Simulated customers accept, decline, reply or go silent; an offer raises acceptance probability. Accepted jobs are booked to the quoting tech over the next two weeks, and the engine reads the new total, so offer size falls as bookings rise. One day of work is removed each night.</p>" +
      (SIM.error ? '<div class="note bad">' + esc(SIM.error) + "</div>" : "") +
      '<div class="row" style="margin-bottom:10px"><span class="badge">' + (SIM.started ? when(SIM.clock) + " shop time" : "not started") + "</span>" +
      (slack != null ? '<span class="badge info">slack ' + slack.toFixed(2) + "</span>" : "") + (cap && cap.fully_booked ? '<span class="badge bad">fully booked, no costed offers</span>' : "") + "</div>" +
      "<h3>Techs, hours booked over the next two weeks</h3>" + (bars || '<p class="muted">Press Start.</p>') +
      '<div class="sim-total"><div class="sim-bar"><div class="sim-fill' + (booked >= avail ? " full" : booked / avail >= 0.8 ? " busy" : "") + '" style="width:' + Math.min(100, (booked / avail) * 100).toFixed(1) +
      '%"></div><div class="sim-median"></div></div><div class="muted small">Shop: ' + booked + " of " + avail + " h. The line is the usual 80%. Below it the week counts as light.</div></div>" +
      "<h3>Current offer value</h3><p>" + (rate == null ? "Starts once the shop opens." : cap.fully_booked ? "None. Shop fully booked; costed offers disabled."
        : "Discount rate: <b>" + rate.toFixed(1) + "% off the quote</b>. Range " + (cfg.percent_base * 100).toFixed(0) + "% (median week) to " + (cfg.percent_max * 100).toFixed(0) +
          "% (empty week) on an S curve in slack, at most " + money(cfg.percent_cap_cents) + " off per quote. Final touch or high slack only; owner approval required.") + "</p>" +
      '<div class="sim-kpis">' + kpi(st.arrived, "quotes in") + kpi(open, "open now") + kpi(st.sent, "messages sent") + kpi(st.offers, "with an offer") + kpi(st.accepted, "accepted") +
      kpi(st.declined, "declined") + kpi(money(st.won), "won, after discounts") + kpi(money(st.discount), "given in discounts") + "</div>" +
      '<p class="muted small">Simulated: customer behaviour, job hours ($400 per hour, 3 to 32 h), starting load. Engine: candidate selection, offers, send decisions. Pick "Live simulation" in the shop menu to see its queue and drafts in the other tabs.</p>' +
      '</div><div class="card"><h2>What happened</h2><div class="sim-feed">' + (feed || '<p class="muted">Nothing yet.</p>') + "</div></div></div>";
  };

  // ------------------------------------------------------------------- events
  document.addEventListener("click", async (ev) => {
    const tab = ev.target.closest("[data-tab]");
    if (tab) { ev.preventDefault(); S.view = tab.dataset.tab; window.scrollTo(0, 0); if (S.view === "shops") S.other = null; render(); return; }
    const act = ev.target.closest("[data-act]");
    if (act) {
      ev.stopPropagation();
      const fn = ACT[act.dataset.act];
      if (!fn) return;
      const local = ["goto", "edit", "cancel", "replay", "sim-start", "sim-pause"].includes(act.dataset.act);
      await fn(act);
      if (local) render(); else await refresh();
      return;
    }
    const info = ev.target.closest("[data-info]");
    if (info) {
      S.info = info.dataset.info;
      if (!ev.target.closest("[data-arch], [data-quote], [data-draft]")) { render(); return; }
    }
    const arch = ev.target.closest("[data-arch]");
    if (ev.target.closest("[data-popclose]")) { ev.target.closest(".arch-pop").hidden = true; return; }
    if (arch) {
      ev.preventDefault();
      const id = arch.dataset.arch, inDoc = !!arch.closest(".doc-arch");
      if (!inDoc) { S.arch = id; render(); }
      const g = inDoc ? arch : document.querySelector('.arch-wrap svg [data-arch="' + id + '"]');
      if (g && g.closest(".arch-wrap")) showArchPop(g, id, inDoc);
      return;
    }
    const draft = ev.target.closest("[data-draft]");
    if (draft && !ev.target.closest("textarea, button, select")) { S.draft = draft.dataset.draft; render(); return; }
    const quote = ev.target.closest("[data-quote]");
    if (quote) { S.quote = quote.dataset.quote; S.guardResult = null; await refresh(); }
  });
  document.addEventListener("change", async (ev) => {
    const t = ev.target;
    if (t.id === "shop") { S.shop = t.value; S.quote = S.draft = S.other = S.leak = S.guardResult = S.messy = null; S.opening = false; await refresh(); return; }
    if (t.dataset.pick === "quote") { S.quote = t.value; S.guardResult = null; await refresh(); return; }
    if (t.dataset.pick === "role") { S.role = t.value; return; }
    if (t.dataset.pick === "sim-speed") { SIM.speed = parseInt(t.value, 10); if (SIM.running) simStart(); return; }
    if (t.dataset.pick === "script") { await api("script_sender", { steps: t.value === "accept" ? [] : [t.value] }); note("The provider will answer the next send with: " + t.options[t.selectedIndex].text + ".", ""); render(); return; }
    if (t.dataset.pick === "kill") { await api("set_sending", { shop_id: S.shop, enabled: !t.checked }); note(t.checked ? "Kill switch on. Nothing will send, including approved drafts." : "Kill switch off.", t.checked ? "warn" : ""); await refresh(); return; }
    if (t.dataset.config) { const c = {}; c[t.dataset.config] = parseFloat(t.value); await api("update_config", { shop_id: S.shop, changes: c }); await refresh(); return; }
    if (t.id === "booked") { await setCapacity(); await refresh(); return; }
    if (t.id === "opening") { S.opening = t.checked; await setCapacity(); await refresh(); }
  });
  document.addEventListener("input", (ev) => {
    const t = ev.target;
    if (t.id === "now-slider") { S.now = toIso(S.range[0] + parseInt(t.value, 10) * 3600e3); $("#now-label").textContent = when(S.now) + " shop time"; S.other = null; refreshSoon(); }
    if (t.type === "range" && t.nextElementSibling && t.nextElementSibling.tagName === "OUTPUT") t.nextElementSibling.textContent = t.value;
    if (t.id === "guard-text") S.guardText = t.value;
  });
  $("#reset").addEventListener("click", async () => { simStop(); Object.assign(SIM, { started: false, clock: SIM_START, seq: 0, techs: {}, pending: [], scheduled: new Set(), feed: [], data: null, error: null, stats: { arrived: 0, sent: 0, accepted: 0, declined: 0, won: 0, discount: 0, offers: 0 } }); await T.reset(); Object.assign(S, { draft: null, log: [], messy: null, guardResult: null, guardRun: null, other: null, leak: null, opening: false, editing: null }); note("Seed reloaded.", ""); await refresh(); });
  $("#theme").addEventListener("click", () => {
    const root = document.documentElement, dark = getComputedStyle(root).getPropertyValue("--bg").trim() === "#15161a";
    root.dataset.theme = dark ? "light" : "dark";
    if (S.view === "design") render();
  });

  // --------------------------------------------------------------------- boot
  (async function boot() {
    T = await window.FollowupTransport.connect((msg) => { $("#loading").textContent = msg; });
    const badge = $("#transport-badge");
    badge.textContent = T.label; badge.className = "badge " + (T.live ? "info" : "warn");
    const shops = await api("shops", {});
    $("#shop").innerHTML = (shops.ok ? shops.data : [{ shop_id: "shop-a", name: "Robby Home Services" }]).map((s) => '<option value="' + s.shop_id + '">' + esc(s.name) + "</option>").join("");
    const cases = await api("hostile_cases", {}, true);
    S.cases = cases.ok ? cases.data : [];
    const first = await api("overview", { shop_id: S.shop, now: S.now });
    const start = first.ok && first.data.time_range.first ? ms(first.data.time_range.first) : ms(S.now) - 16 * 864e5;
    S.range = [Math.floor(start / 3600e3) * 3600e3 - 24 * 3600e3, ms(S.now) + 21 * 864e5];
    const slider = $("#now-slider");
    slider.max = String(Math.round((S.range[1] - S.range[0]) / 3600e3));
    slider.value = String(Math.round((ms(S.now) - S.range[0]) / 3600e3));
    if (!T.live) { slider.disabled = true; note("The live engine could not load in this browser. Showing a recorded run. Actions are switched off.", "warn"); }
    $("#loading").remove();
    await refresh();
  })();
})();
