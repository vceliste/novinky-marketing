"""Náhledové obrázky událostí: og:image z původního článku.

Obrázek se stahuje jen pro důležité události (importance >= 60), zmenšuje se
a ukládá do data/img/<id>.jpg. Když se nic použitelného nenajde, událost
obrázek nemá a šablona zobrazí grafický placeholder s ikonou kategorie.
"""
from __future__ import annotations

import logging
import re
from io import BytesIO
from urllib.parse import urljoin

import requests

from . import config

log = logging.getLogger("radar.images")

IMG_DIR = config.DATA_DIR / "img"
MAX_PER_RUN = 12
MIN_IMPORTANCE = 60
MIN_WIDTH = 300           # menší obrázky (loga, ikony) zahazujeme
TARGET_WIDTH = 900
MAX_DOWNLOAD = 5 * 1024 * 1024

OG_PATTERNS = [
    r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]*content=["\']([^"\']+)["\']',
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image(?::url)?["\']',
    r'<meta[^>]+name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)["\']',
]
SKIP_HINTS = ("logo", "favicon", "avatar", "icon", "placeholder", "default")


def _find_image_url(html: str, base_url: str) -> str | None:
    for pattern in OG_PATTERNS:
        m = re.search(pattern, html, re.I)
        if m:
            url = urljoin(base_url, m.group(1).strip())
            if not any(h in url.lower() for h in SKIP_HINTS):
                return url
    return None


def _save_thumbnail(data: bytes, dest) -> bool:
    from PIL import Image
    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001
        return False
    if img.width < MIN_WIDTH:
        return False
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if img.width > TARGET_WIDTH:
        img = img.resize((TARGET_WIDTH, round(img.height * TARGET_WIDTH / img.width)))
    img.save(dest, "JPEG", quality=80, optimize=True)
    return True


def ensure_images(events: list[dict]) -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = config.USER_AGENT

    # kandidáti: důležité události bez obrázku, nejdůležitější první
    candidates = [e for e in events
                  if e.get("importance", 0) >= MIN_IMPORTANCE
                  and not e.get("image") and e.get("articles")]
    candidates.sort(key=lambda e: -e.get("importance", 0))

    done = 0
    for ev in candidates:
        if done >= MAX_PER_RUN:
            break
        dest = IMG_DIR / f"{ev['id']}.jpg"
        if dest.exists():                       # už staženo dřív
            ev["image"] = f"img/{ev['id']}.jpg"
            continue
        for article in ev["articles"][:3]:
            try:
                page = session.get(article["url"], timeout=12)
                img_url = _find_image_url(page.text, article["url"])
                if not img_url:
                    continue
                resp = session.get(img_url, timeout=15, stream=True)
                data = resp.raw.read(MAX_DOWNLOAD + 1, decode_content=True)
                if len(data) > MAX_DOWNLOAD:
                    continue
                if _save_thumbnail(data, dest):
                    ev["image"] = f"img/{ev['id']}.jpg"
                    ev["image_from"] = article["domain"]
                    done += 1
                    log.info("Obrázek pro %s z %s", ev["id"], article["domain"])
                    break
            except Exception:  # noqa: BLE001
                continue

    # úklid obrázků smazaných událostí
    valid = {e["id"] for e in events}
    for f in IMG_DIR.glob("*.jpg"):
        if f.stem not in valid:
            f.unlink(missing_ok=True)
