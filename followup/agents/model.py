"""Model writer. Optional: used only when the `anthropic` package and credentials are present.

It sits behind the same Writer interface as the template writer. It has no tools. It
sees only the allow-listed fields in WriterInput, and only its text is read. Any
failure here (timeout, refusal, empty answer) raises, and the caller falls back to
the template. The guard checks whatever it returns.
"""
from __future__ import annotations

from .base import WriterInput
from .prompt import build_prompt

DEFAULT_MODEL = "claude-opus-5-5"


class ModelWriterError(RuntimeError):
    pass


class ModelWriter:
    def __init__(self, model: str = DEFAULT_MODEL, timeout_seconds: float = 20.0, client=None):
        if client is None:
            import anthropic  # imported here so the engine runs without the package

            client = anthropic.Anthropic()   # credentials come from the environment
        self.client = client
        self.model = model
        self.name = model
        self.timeout_seconds = timeout_seconds

    def write(self, inp: WriterInput) -> str:
        system, user = build_prompt(inp)
        response = self.client.with_options(timeout=self.timeout_seconds, max_retries=1).beta.messages.create(
            model=self.model,
            max_tokens=4000,                      # room for the model's thinking plus one short message
            system=system,
            output_config={"effort": "low"},      # one SMS is a small task
            # If the model declines, let the API retry on its default fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise ModelWriterError("model declined to write this message")
        if response.stop_reason == "max_tokens":
            raise ModelWriterError("model ran out of room before finishing")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise ModelWriterError("model returned no text")
        return text


def model_writer_from_env():
    """Return a ModelWriter if FOLLOWUP_WRITER=model is set and the SDK is importable, else None."""
    import os

    if os.environ.get("FOLLOWUP_WRITER", "").lower() != "model":
        return None
    try:
        return ModelWriter(os.environ.get("FOLLOWUP_MODEL", DEFAULT_MODEL))
    except Exception:
        return None
