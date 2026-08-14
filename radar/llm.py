"""Tenký wrapper nad Claude API + mock režim pro testování zdarma."""
from __future__ import annotations

import json
import logging
import os
import re

from . import config

log = logging.getLogger("radar.llm")

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def call_json(model: str, system: str, user: str, max_tokens: int = 4000):
    """Zavolá model a vrátí naparsovaný JSON z odpovědi."""
    if config.MOCK:
        raise RuntimeError("call_json volán v MOCK režimu – použij mock větev v pipeline")
    client = _get_client()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text")
    return parse_json(text)


def parse_json(text: str):
    """Vytáhne JSON z odpovědi (i když je obalený ```json ... ```)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=0)
    return json.loads(text[start:])
