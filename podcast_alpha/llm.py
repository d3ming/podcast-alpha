import json
import os

import requests

PROVIDERS = [
    ("XAI_API_KEY", "xai", "https://api.x.ai/v1/chat/completions", "grok-4-fast"),
    ("OPENAI_API_KEY", "openai", "https://api.openai.com/v1/chat/completions", "gpt-4.1-mini"),
    ("ANTHROPIC_API_KEY", "anthropic", "https://api.anthropic.com/v1/messages", "claude-haiku-4-5"),
]


def available_provider() -> tuple[str, str, str, str] | None:
    """Checks env var names only; values are never printed."""
    for env, name, url, model in PROVIDERS:
        if os.environ.get(env):
            return env, name, url, os.environ.get("PODCAST_ALPHA_MODEL", model)
    return None


def complete_json(system: str, user: str) -> dict | list | None:
    p = available_provider()
    if not p:
        return None
    env, name, url, model = p
    key = os.environ[env]
    if name == "anthropic":
        r = requests.post(url, timeout=180, headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                          json={"model": model, "max_tokens": 4000, "system": system,
                                "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        text = r.json()["content"][0]["text"]
    else:
        r = requests.post(url, timeout=180, headers={"Authorization": f"Bearer {key}"},
                          json={"model": model, "response_format": {"type": "json_object"},
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
    return json.loads(text[start:])
