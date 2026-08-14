"""Ranní digest do Slacku (volitelné).

Pošle přehled událostí za posledních 24 h (po víkendu 72 h) do Slack kanálu
přes incoming webhook. Přeskočí se, pokud není nastaven SLACK_WEBHOOK_URL.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from . import config
from .pipeline import load_state

SITE_URL = os.environ.get("RADAR_SITE_URL", "https://novinky.vceliste.cz")


def main() -> int:
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        print("SLACK_WEBHOOK_URL není nastaven – digest se přeskakuje.")
        return 0

    state = load_state()
    now = datetime.now(timezone.utc)
    hours = 72 if now.weekday() == 0 else 24
    cutoff = (now - timedelta(hours=hours)).isoformat()
    events = [e for e in state["events"] if e["updated"] >= cutoff and e.get("title")]
    if not events:
        print("Žádné nové události – nic neposílám.")
        return 0

    order = {"zásadní": 0, "důležité": 1, "stojí za pozornost": 2, "kontext": 3}
    events.sort(key=lambda e: (order.get(e.get("importance_label"), 3), -e.get("importance", 0)))
    top = events[:10]

    lines = [f"*📡 Marketing Radar – přehled za posledních {hours} h*\n"]
    for e in top:
        src = e["articles"][0]["url"] if e.get("articles") else SITE_URL
        badge = {"zásadní": "🔴", "důležité": "🟠"}.get(e.get("importance_label"), "•")
        lines.append(f"{badge} *{e['title']}*\n{e.get('takeaway') or e.get('what', '')} <{src}|zdroj>")
    if len(events) > len(top):
        lines.append(f"\n… a dalších {len(events) - len(top)} událostí.")
    lines.append(f"\nVše na <{SITE_URL}|Marketing Radaru>.")

    resp = requests.post(webhook, json={"text": "\n\n".join(lines)}, timeout=15)
    resp.raise_for_status()
    print(f"Digest odeslán ({len(top)} událostí).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
