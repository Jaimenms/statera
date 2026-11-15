"""The model: an Ollama server on this computer (https://ollama.com), and nothing else.

One method, `chat(messages, schema)`: the conversation so far in, the model's reply out, constrained by
the server to the JSON schema (Ollama's structured outputs). The agent needs no more than that, so a test
replaces the model with any object that has this method.

The address must be a loopback one (localhost, 127.0.0.1, ::1). The copilot shows the model the catalog and
values of the data; with the model on this computer, neither leaves it.
"""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit

DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_URL = "http://localhost:11434"
DEFAULT_CONTEXT = 8192  # tokens; Ollama's own default is smaller and cuts a long prompt without saying so
ENV_MODEL = "STATERA_COPILOT_MODEL"
ENV_URL = "STATERA_COPILOT_URL"


class CopilotError(RuntimeError):
    """The copilot cannot answer: the local model is not there, or its address is not local."""


def is_local(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def settings(config: dict | None = None, model: str | None = None) -> dict:
    """Model, address and context window: the argument, the environment, `[copilot]` in statera.toml, the default."""
    config = config or {}
    return {
        "model": model or os.environ.get(ENV_MODEL) or config.get("model") or DEFAULT_MODEL,
        "url": os.environ.get(ENV_URL) or config.get("url") or DEFAULT_URL,
        "context": int(config.get("context") or DEFAULT_CONTEXT),
    }


class Ollama:
    def __init__(self, model: str = DEFAULT_MODEL, url: str = DEFAULT_URL, context: int = DEFAULT_CONTEXT):
        if not is_local(url):
            raise CopilotError(
                f"the copilot only talks to a model on this computer, and {url} is not a local address "
                f"(use {DEFAULT_URL}): the catalog and the data it reads must not leave the computer"
            )
        self.model, self.url, self.context = model, url.rstrip("/"), context

    def chat(self, messages: list[dict], schema: dict) -> str:
        """The model's reply to the conversation, as JSON text that follows `schema`."""
        import httpx  # the extra `copilot`: imported where it is used, so `statera doctor` can read the settings

        payload = {
            "model": self.model,
            "messages": messages,
            "format": schema,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": self.context},
        }
        try:
            response = httpx.post(f"{self.url}/api/chat", json=payload, timeout=600.0)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise CopilotError(self.problem() or f"the local model failed: {error}") from error
        return response.json()["message"]["content"]

    def installed(self) -> list[str] | None:
        """The models the local Ollama has; None when it is not running."""
        import httpx

        try:
            response = httpx.get(f"{self.url}/api/tags", timeout=3.0)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        return [m["name"] for m in response.json().get("models", [])]

    def problem(self) -> str | None:
        """Why the model cannot answer, and what to do about it; None when it can."""
        models = self.installed()
        if models is None:
            return (
                f"Ollama is not running at {self.url}. Install it (https://ollama.com/download) and start it "
                "(`ollama serve`, or the Ollama app); the model runs on this computer."
            )
        if self.model not in models and f"{self.model}:latest" not in models:
            have = f" (this computer has: {', '.join(models)})" if models else ""
            return f"the model {self.model} is not on this computer{have}. Download it once: ollama pull {self.model}"
        return None
