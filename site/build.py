"""Generátor statického webu Marketing Radaru.

Spuštění: python site/build.py  → výstup do _site/
"""
from __future__ import annotations

import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
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


def cz_date(d: datetime) -> str:
    return f"{CZ_DAYS[d.weekday()]} {d.day}. {CZ_MONTHS[d.month - 1]} {d.year}"


def local(dt_iso: str) -> datetime:
    return datetime.fromisoformat(dt_iso).astimezone(TZ)


def prepare(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        if not e.get("title"):
            continue
        e = dict(e)
        e["day"] = local(e["created"]).date().isoformat()
        e["day_updated"] = local(e["updated"]).date().isoformat()
        e["sources_list"] = sorted({(a["source"], a["url"]) for a in e["articles"]},
                                   key=lambda s: s[0])
        out.append(e)
    return out


def build() -> None:
    state_file = config.STATE_FILE
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() \
        else {"events": [], "last_run": None}

    sources_cfg = load_sources()
    categories = sources_cfg["categories"]
    cat_by_key = {c["key"]: c for c in categories}

    events = prepare(state["events"])

    # Dnešní přehled = události založené dnes; když nic, poslední den s obsahem
    now = datetime.now(TZ)
    today = now.date().isoformat()
    days = sorted({e["day"] for e in events}, reverse=True)
    front_day = today if today in days else (days[0] if days else today)
    front_events = [e for e in events if e["day"] == front_day]
    front_events.sort(key=lambda e: (IMPORTANCE_ORDER.get(e.get("importance_label"), 3),
                                     -e.get("importance", 0)))
    highlights = [e for e in front_events if e.get("importance", 0) >= 60][:3]

    # Archiv: den -> události
    by_day: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_day[e["day"]].append(e)
    archive_days = []
    for day in sorted(by_day, reverse=True):
        evs = sorted(by_day[day], key=lambda e: (IMPORTANCE_ORDER.get(e.get("importance_label"), 3),
                                                 -e.get("importance", 0)))
        d = datetime.fromisoformat(day)
        archive_days.append({"day": day, "label": cz_date(d), "events": evs})

    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    env.globals.update(
        categories=categories,
        cat=lambda key: cat_by_key.get(key, {"name": key, "icon": "📰"}),
        site_name="Marketing Radar",
        last_run=local(state["last_run"]).strftime("%d. %m. %Y %H:%M") if state.get("last_run") else "—",
        year=now.year,
    )

    out = config.OUTPUT_DIR
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    pages = {
        "index.html": env.get_template("index.html").render(
            front_label=cz_date(datetime.fromisoformat(front_day)),
            is_today=front_day == today,
            events=front_events, highlights=highlights),
        "archiv.html": env.get_template("archiv.html").render(archive_days=archive_days),
        "o-projektu.html": env.get_template("o-projektu.html").render(
            sources=sources_cfg["sources"], n_sources=len(sources_cfg["sources"])),
    }
    for name, html in pages.items():
        (out / name).write_text(html, encoding="utf-8")

    shutil.copytree(Path(__file__).parent / "static", out / "static")

    # JSON + RSS výstup webu
    (out / "data.json").write_text(json.dumps(
        {"generated": now.isoformat(), "events": events[:100]},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # status.json: zdraví zdrojů pro vzdálenou kontrolu bez logů
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
    for e in events[:40]:
        link = e["sources_list"][0][1] if e["sources_list"] else ""
        items.append(
            "<item><title>{}</title><link>{}</link><guid>{}</guid>"
            "<pubDate>{}</pubDate><description>{}</description></item>".format(
                _x(e["title"]), _x(link), e["id"],
                datetime.fromisoformat(e["updated"]).strftime("%a, %d %b %Y %H:%M:%S +0000"),
                _x((e.get("what") or "") + " " + (e.get("takeaway") or ""))))
    (out / "feed.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        "<title>Marketing Radar</title><link>https://novinky.vceliste.cz</link>"
        "<description>Novinky z online marketingu bez šumu</description>"
        + "".join(items) + "</channel></rss>", encoding="utf-8")

    print(f"Web vygenerován do {out} ({len(events)} událostí, den na titulce: {front_day})")


def _x(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


if __name__ == "__main__":
    build()
