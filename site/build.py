"""Generátor statického webu Marketing Radaru (v2).

Struktura:
  /                    homepage – nejdůležitější události po kategoriích (7 dní, max 30)
  /kategorie/<key>.html stránka kategorie
  /udalost/<id>.html   detail události
  /archiv.html         vše po dnech
  /hledani.html        vyhledávání (klientské, nad /search-index.json)
  /o-projektu.html     o projektu
  + feed.xml, data.json, status.json, /img/, /static/

Spuštění: python site/build.py  → výstup do _site/
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from radar import config          # noqa: E402
from radar.feeds import load_sources  # noqa: E402

TZ = ZoneInfo(config.TZ)
CZ_DAYS = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
CZ_MONTHS = ["ledna", "února", "března", "dubna", "května", "června", "července",
             "srpna", "září", "října", "listopadu", "prosince"]

IMPORTANCE_ORDER = {"zásadní": 0, "důležité": 1, "stojí za pozornost": 2, "kontext": 3}

HOME_WINDOW_DAYS = 7        # primární okno homepage
HOME_MAX_DAYS = 30          # maximální stáří při nedostatku důležitých zpráv
HOME_MIN_IMPORTANT = 3      # kolik důležitých událostí chceme v kategorii
HOME_PER_CATEGORY = 5       # hero + 4 další


def cz_date(d: datetime) -> str:
    return f"{CZ_DAYS[d.weekday()]} {d.day}. {CZ_MONTHS[d.month - 1]} {d.year}"


def local(dt_iso: str) -> datetime:
    return datetime.fromisoformat(dt_iso).astimezone(TZ)


def prepare(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        # bez hotového souhrnu se událost nepublikuje (doplní se v dalším běhu)
        if not e.get("title") or not e.get("what"):
            continue
        e = dict(e)
        e["day"] = local(e["created"]).date().isoformat()
        e["day_label"] = cz_date(local(e["created"]))
        e["sources_list"] = sorted({(a["source"], a["url"]) for a in e["articles"]},
                                   key=lambda s: s[0])
        e["image_url"] = "/" + e["image"] if e.get("image") else None
        out.append(e)
    return out


def by_importance(evs):
    return sorted(evs, key=lambda e: (IMPORTANCE_ORDER.get(e.get("importance_label"), 3),
                                      -e.get("importance", 0), e["day"]), reverse=False)


def build() -> None:
    state_file = config.STATE_FILE
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() \
        else {"events": [], "last_run": None}

    sources_cfg = load_sources()
    categories = sources_cfg["categories"]
    cat_by_key = {c["key"]: c for c in categories}

    events = prepare(state["events"])
    now = datetime.now(TZ)
    today = now.date()

    # ── Homepage: nejdůležitější po kategoriích ──────────────────
    week_cutoff = (today - timedelta(days=HOME_WINDOW_DAYS)).isoformat()
    month_cutoff = (today - timedelta(days=HOME_MAX_DAYS)).isoformat()

    home_sections = []
    for c in categories:
        cat_events = [e for e in events if e["category"] == c["key"]]
        pool = [e for e in cat_events if e["day"] >= week_cutoff]
        important = [e for e in pool if e.get("importance", 0) >= 60]
        if len(important) < HOME_MIN_IMPORTANT:
            # doplň staršími důležitými (max měsíc)
            older = [e for e in cat_events
                     if month_cutoff <= e["day"] < week_cutoff
                     and e.get("importance", 0) >= 60]
            pool = pool + older
        picks = by_importance(pool)[:HOME_PER_CATEGORY]
        if picks:
            home_sections.append({"cat": c, "hero": picks[0], "rest": picks[1:],
                                  "total": len(cat_events)})

    # ── Stránky kategorií ────────────────────────────────────────
    category_pages = []
    for c in categories:
        cat_events = [e for e in events if e["category"] == c["key"]]
        if not cat_events:
            category_pages.append({"cat": c, "hero": None, "days": []})
            continue
        hero = by_importance([e for e in cat_events if e["day"] >= month_cutoff])
        hero = hero[0] if hero and hero[0].get("importance", 0) >= 60 else None
        by_day = defaultdict(list)
        for e in cat_events:
            by_day[e["day"]].append(e)
        days = [{"label": cz_date(datetime.fromisoformat(d)),
                 "events": by_importance(by_day[d])}
                for d in sorted(by_day, reverse=True)]
        category_pages.append({"cat": c, "hero": hero, "days": days})

    # ── Render ───────────────────────────────────────────────────
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.globals.update(
        categories=categories,
        cat=lambda key: cat_by_key.get(key, {"name": key, "icon": "📰",
                                             "msym": "article", "key": key}),
        site_name="Novinky z marketingu od Včeliště",
        last_run=local(state["last_run"]).strftime("%d. %m. %Y %H:%M") if state.get("last_run") else "—",
        year=now.year,
    )

    out = config.OUTPUT_DIR
    if out.exists():
        shutil.rmtree(out)
    (out / "kategorie").mkdir(parents=True)
    (out / "udalost").mkdir()

    (out / "index.html").write_text(env.get_template("index.html").render(
        home_sections=home_sections), encoding="utf-8")

    for page in category_pages:
        (out / "kategorie" / f"{page['cat']['key']}.html").write_text(
            env.get_template("category.html").render(**page), encoding="utf-8")

    for e in events:
        (out / "udalost" / f"{e['id']}.html").write_text(
            env.get_template("event.html").render(e=e), encoding="utf-8")

    (out / "hledani.html").write_text(env.get_template("hledani.html").render(),
                                      encoding="utf-8")
    (out / "o-projektu.html").write_text(env.get_template("o-projektu.html").render(
        sources=sources_cfg["sources"], n_sources=len(sources_cfg["sources"])),
        encoding="utf-8")

    shutil.copytree(Path(__file__).parent / "static", out / "static")
    img_dir = config.DATA_DIR / "img"
    if img_dir.exists():
        shutil.copytree(img_dir, out / "img")

    # ── Vyhledávací index ────────────────────────────────────────
    (out / "search-index.json").write_text(json.dumps([
        {"id": e["id"], "t": e["title"],
         "x": " ".join(filter(None, [e.get("what"), e.get("why"), e.get("takeaway"),
                                     " ".join(s for s, _ in e["sources_list"])])),
         "c": e["category"], "d": e["day"], "i": e.get("importance_label", "")}
        for e in events], ensure_ascii=False), encoding="utf-8")

    # ── data.json, status.json, feed ─────────────────────────────
    (out / "data.json").write_text(json.dumps(
        {"generated": now.isoformat(), "events": events[:100]},
        ensure_ascii=False, indent=1), encoding="utf-8")

    report = state.get("source_report", [])
    (out / "status.json").write_text(json.dumps({
        "generated": now.isoformat(),
        "last_run": state.get("last_run"),
        "events_total": len(events),
        "sources_total": len(report),
        "sources_ok": sum(1 for r in report if str(r.get("status", "")).startswith("ok")),
        "sources_failing": [r for r in report if not str(r.get("status", "")).startswith("ok")],
        "sources_via_google_news": [r["name"] for r in report
                                    if str(r.get("status", "")).startswith("ok (")],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    items = []
    for e in sorted(events, key=lambda x: x["updated"], reverse=True)[:40]:
        items.append(
            "<item><title>{}</title><link>https://novinky.vceliste.cz/udalost/{}.html</link>"
            "<guid>{}</guid><pubDate>{}</pubDate><description>{}</description></item>".format(
                _x(e["title"]), e["id"], e["id"],
                datetime.fromisoformat(e["updated"]).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                _x((e.get("what") or "") + " " + (e.get("takeaway") or ""))))
    (out / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>Novinky z marketingu od Včeliště</title><link>https://novinky.vceliste.cz</link>"
        "<description>Novinky z online marketingu bez šumu</description>"
        + "".join(items) + "</channel></rss>", encoding="utf-8")

    print(f"Web vygenerován do {out}: {len(events)} událostí, "
          f"{len(home_sections)} kategorií na homepage")


def _x(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    build()
