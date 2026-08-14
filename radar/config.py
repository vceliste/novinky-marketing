"""Konfigurace Marketing Radaru."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "state.json"
SOURCES_FILE = ROOT / "sources.yaml"
SITE_DIR = ROOT / "site"
OUTPUT_DIR = ROOT / "_site"

# Prohlížečová identifikace – řada webů (Cloudflare apod.) blokuje neznámé roboty
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Modely – lze přepsat env proměnnými
FAST_MODEL = os.environ.get("RADAR_FAST_MODEL", "claude-haiku-4-5")
SMART_MODEL = os.environ.get("RADAR_SMART_MODEL", "claude-sonnet-4-5")

# Mock režim: bez volání API (testování zdarma)
MOCK = os.environ.get("RADAR_MOCK", "") == "1"

# Prahové hodnoty
MIN_SCORE = int(os.environ.get("RADAR_MIN_SCORE", "45"))   # 0–100, pod tím = šum
MAX_NEW_PER_RUN = 60          # max nových článků zpracovaných v jednom běhu
MAX_ARTICLE_AGE_DAYS = 3      # starší články z feedů ignorujeme
EVENT_MATCH_WINDOW_DAYS = 7   # do jak starých událostí lze přiřadit nový článek
ARCHIVE_DAYS = 35             # jak dlouho držíme události pro web
SEEN_RETENTION_DAYS = 60      # jak dlouho si pamatujeme viděné URL

TZ = "Europe/Prague"

IMPORTANCE_LABELS = [
    (80, "zásadní"),
    (60, "důležité"),
    (40, "stojí za pozornost"),
    (0, "kontext"),
]


def importance_label(score: int) -> str:
    for threshold, label in IMPORTANCE_LABELS:
        if score >= threshold:
            return label
    return "kontext"
