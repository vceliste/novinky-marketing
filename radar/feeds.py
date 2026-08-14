"""Stahování článků z RSS/Atom feedů kurátorovaných zdrojů."""
from __future__ import annotations

import calendar
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml

from . import config

log = logging.getLogger("radar.feeds")

COMMON_FEED_PATHS = ["feed/", "feed", "rss/", "rss", "rss.xml", "feed.xml", "index.xml", "atom.xml", "blog/feed/"]


@dataclass
class Article:
    url: str
    title: str
    summary: str
    source: str
    source_weight: float
    category: str
    lang: str
    published: str  # ISO 8601 UTC
    domain: str = field(init=False)

    def __post_init__(self):
        self.domain = urlparse(self.url).netloc.removeprefix("www.")


def load_sources() -> dict:
    with open(config.SOURCES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_url(url: str) -> str:
    """Odstraní UTM parametry a fragmenty, sjednotí tvar URL."""
    url = re.sub(r"[?&](utm_[^=&]+|fbclid|gclid|ref)=[^&]*", "", url)
    url = url.rstrip("?&").split("#")[0]
    return url.rstrip("/")


def _entry_datetime(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


def discover_feed(site_url: str, session: requests.Session) -> str | None:
    """Zkusí najít feed přes <link rel="alternate"> a běžné cesty."""
    try:
        resp = session.get(site_url, timeout=8)
        m = re.search(
            r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*href=["\']([^"\']+)["\']',
            resp.text, re.I,
        ) or re.search(
            r'<link[^>]+href=["\']([^"\']+)["\'][^>]*type=["\']application/(?:rss|atom)\+xml["\']',
            resp.text, re.I,
        )
        if m:
            return urljoin(site_url, m.group(1))
    except requests.RequestException:
        pass
    for path in COMMON_FEED_PATHS:
        candidate = urljoin(site_url.rstrip("/") + "/", path)
        try:
            r = session.get(candidate, timeout=6)
            if r.ok and ("<rss" in r.text[:2000] or "<feed" in r.text[:2000]):
                return candidate
        except requests.RequestException:
            continue
    return None


def fetch_all(state: dict) -> tuple[list[Article], list[dict]]:
    """Stáhne všechny feedy, vrátí (nové články, report o zdrojích).

    Funkční adresy feedů nalezené auto-detekcí se ukládají do
    state["feed_overrides"], aby se detekce neopakovala při každém běhu.
    """
    seen = state.get("seen", {})
    overrides = state.setdefault("feed_overrides", {})
    cfg = load_sources()
    session = requests.Session()
    session.headers["User-Agent"] = config.USER_AGENT

    cutoff = datetime.now(timezone.utc) - timedelta(days=config.MAX_ARTICLE_AGE_DAYS)
    articles: list[Article] = []
    report: list[dict] = []

    for src in cfg["sources"]:
        feed_url = overrides.get(src["name"]) or src.get("feed")
        status = "ok"
        entries = []

        for attempt in (1, 2):
            if not feed_url:
                break
            try:
                resp = session.get(feed_url, timeout=15)
                parsed = feedparser.parse(resp.content)
                if parsed.entries:
                    entries = parsed.entries
                    if feed_url != src.get("feed"):
                        overrides[src["name"]] = feed_url
                    break
                raise ValueError("empty feed")
            except Exception as e:  # noqa: BLE001
                if attempt == 1:
                    discovered = discover_feed(src["url"], session)
                    if discovered and discovered != feed_url:
                        log.info("%s: feed %s selhal (%s), zkouším %s", src["name"], feed_url, e, discovered)
                        feed_url = discovered
                        continue
                status = f"chyba: {e}"
                break
        if not src.get("feed") and not feed_url:
            status = "bez feedu"

        fresh = 0
        for entry in entries[:50]:
            link = entry.get("link")
            if not link:
                continue
            url = normalize_url(link)
            if url in seen:
                continue
            published = _entry_datetime(entry)
            if published and published < cutoff:
                continue
            # bez data: bereme jako čerstvý (poprvé viděný), datum = teď
            published = published or datetime.now(timezone.utc)
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", "") or "")
            summary = re.sub(r"\s+", " ", summary).strip()[:600]
            articles.append(Article(
                url=url,
                title=(entry.get("title") or "").strip()[:300],
                summary=summary,
                source=src["name"],
                source_weight=float(src.get("weight", 1.0)),
                category=src["category"],
                lang=src.get("lang", "en"),
                published=published.isoformat(),
            ))
            fresh += 1

        report.append({"name": src["name"], "feed": feed_url, "status": status, "new": fresh})

    # nejnovější první, limit na běh
    articles.sort(key=lambda a: a.published, reverse=True)
    if len(articles) > config.MAX_NEW_PER_RUN:
        log.warning("Limit %d nových článků na běh, %d odloženo na příště",
                    config.MAX_NEW_PER_RUN, len(articles) - config.MAX_NEW_PER_RUN)
        articles = articles[:config.MAX_NEW_PER_RUN]
    return articles, report
