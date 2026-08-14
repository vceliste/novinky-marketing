"""Hlavní pipeline Marketing Radaru.

Běh: stáhni feedy → oskóruj a vyfiltruj šum (rychlý model) → slouč články
o stejné věci do událostí → napiš/aktualizuj český souhrn (silný model)
→ ulož stav. Web se generuje zvlášť (site/build.py).

Spuštění:  python -m radar.pipeline
Mock test: RADAR_MOCK=1 python -m radar.pipeline
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from . import config, feeds, llm
from .feeds import Article

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("radar.pipeline")

CATEGORY_KEYS = ["cesky-marketing", "seo", "ppc", "emailing",
                 "socialni-site", "analytika", "obsah", "ai"]

SCORE_SYSTEM = """Jsi editor českého webu Marketing Radar – přehledu novinek z online marketingu \
pro české marketéry (SEO, PPC, e-mailing, sociální sítě, analytika, obsah, AI v marketingu, český trh).

Ohodnoť každý článek podle INFORMAČNÍ HODNOTY ZPRÁVY (0–100):
- 80–100: zásadní novinka měnící praxi (nová funkce/ceník/pravidla platforem, algoritmus, regulace)
- 60–79: důležitá novinka, kterou by měl marketér zaznamenat
- 40–59: stojí za pozornost (menší update, zajímavá data/studie)
- 0–39: ŠUM – návody a evergreen obsah ("jak na..."), marketing nástrojů, PR, opakování starých zpráv, obecné tipy

Zprávy relevantní pro český trh hodnoť o ~10 bodů výš.
Ke každému článku urči i nejvhodnější kategorii z: {cats}.

Odpověz POUZE validním JSON polem:
[{{"id": "...", "score": 0-100, "category": "...", "is_news": true/false}}]
is_news=false pro návody, evergreen obsah a marketing nástrojů (i kdyby byly kvalitní)."""

CLUSTER_SYSTEM = """Jsi editor zpravodajského webu. Dostaneš seznam EXISTUJÍCÍCH UDÁLOSTÍ \
(id + titulek) a seznam NOVÝCH ČLÁNKŮ. Ke každému článku urči, zda popisuje STEJNOU KONKRÉTNÍ \
UDÁLOST jako některá existující (stejné oznámení/funkce/rozhodnutí), nebo je to nová událost.

Slučuj jen jasné případy – při pochybnostech označ jako novou událost ("new").
Články o stejné nové věci mezi sebou slučuj přes "group" (stejné číslo skupiny).

Odpověz POUZE validním JSON polem:
[{"id": "...", "event": "<id existující události>" | "new", "group": <číslo skupiny pro nové, jinak null>}]"""

SUMMARY_SYSTEM = """Píšeš pro český web Marketing Radar. Z dodaných článků (titulky, anotace, zdroje) \
napiš souhrn JEDNÉ události pro české marketéry.

PRAVIDLA:
- Vycházej VÝHRADNĚ z dodaných textů. Nedoplňuj fakta z vlastní paměti. Co ve zdrojích není, nepiš.
- Piš česky, věcně, bez marketingových frází a bez superlativů.
- Pokud se zdroje v něčem liší nebo něco není potvrzené, řekni to.

Odpověz POUZE validním JSON objektem:
{
  "title": "úderný český titulek, max 90 znaků",
  "what": "Co se stalo – 2–3 věty.",
  "why": "Proč je to důležité – 1–2 věty.",
  "takeaway": "Co z toho plyne pro české marketéry – 1–2 konkrétní věty.",
  "importance": 0-100,
  "category": "jedna z: %s"
}""" % ", ".join(CATEGORY_KEYS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_state() -> dict:
    if config.STATE_FILE.exists():
        with open(config.STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"seen": {}, "events": []}


def save_state(state: dict) -> None:
    config.DATA_DIR.mkdir(exist_ok=True)
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def event_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


# ── Krok 1: skórování ────────────────────────────────────────────

def score_articles(articles: list[Article]) -> list[dict]:
    """Vrátí [{article, score, category}] jen pro články nad prahem."""
    if not articles:
        return []
    if config.MOCK:
        return [{"article": a, "score": 55 + (i * 7) % 40, "category": a.category}
                for i, a in enumerate(articles)]

    kept = []
    ids = {}
    for i in range(0, len(articles), 20):
        batch = articles[i:i + 20]
        payload = []
        for a in batch:
            aid = event_id(a.url)
            ids[aid] = a
            payload.append({"id": aid, "title": a.title, "summary": a.summary[:300],
                            "source": a.source, "lang": a.lang, "category_hint": a.category})
        try:
            results = llm.call_json(
                config.FAST_MODEL,
                SCORE_SYSTEM.format(cats=", ".join(CATEGORY_KEYS)),
                json.dumps(payload, ensure_ascii=False),
            )
        except Exception:
            log.exception("Skórování dávky selhalo, dávku přeskakuji")
            continue
        for r in results:
            a = ids.get(r.get("id"))
            if not a or not r.get("is_news", True):
                continue
            score = int(r.get("score", 0))
            bonus = round((a.source_weight - 1.0) * 10)
            if score + bonus >= config.MIN_SCORE:
                cat = r.get("category") if r.get("category") in CATEGORY_KEYS else a.category
                kept.append({"article": a, "score": min(100, score + bonus), "category": cat})
    return kept


# ── Krok 2: slučování do událostí ────────────────────────────────

def cluster(kept: list[dict], events: list[dict]) -> dict[str, list[dict]]:
    """Vrátí mapu event_id -> [nové položky]; nové události dostanou nové id."""
    if not kept:
        return {}
    window = (_now() - timedelta(days=config.EVENT_MATCH_WINDOW_DAYS)).isoformat()
    recent = [e for e in events if e["updated"] >= window]

    if config.MOCK:
        assignments = {}
        for item in kept:
            assignments.setdefault(event_id(item["article"].url), []).append(item)
        return assignments

    payload_events = [{"id": e["id"], "title": e["title"]} for e in recent[-120:]]
    ids = {event_id(item["article"].url): item for item in kept}
    payload_articles = [{"id": aid, "title": it["article"].title, "source": it["article"].source}
                        for aid, it in ids.items()]
    try:
        results = llm.call_json(
            config.FAST_MODEL,
            CLUSTER_SYSTEM,
            json.dumps({"events": payload_events, "articles": payload_articles}, ensure_ascii=False),
        )
    except Exception:
        log.exception("Slučování selhalo – každý článek bude samostatná událost")
        results = [{"id": aid, "event": "new", "group": None} for aid in ids]

    valid_event_ids = {e["id"] for e in recent}
    assignments: dict[str, list[dict]] = {}
    groups: dict[int, str] = {}
    for r in results:
        item = ids.pop(r.get("id"), None)
        if not item:
            continue
        target = r.get("event")
        if target in valid_event_ids:
            key = target
        else:
            g = r.get("group")
            if g is not None and g in groups:
                key = groups[g]
            else:
                key = event_id(item["article"].url)
                if g is not None:
                    groups[g] = key
        assignments.setdefault(key, []).append(item)
    # články, které model vynechal
    for aid, item in ids.items():
        assignments.setdefault(aid, []).append(item)
    return assignments


# ── Krok 3: souhrny ──────────────────────────────────────────────

def summarize_event(event: dict) -> None:
    arts = event["articles"]
    if config.MOCK:
        first = arts[0]
        event.update({
            "title": first["title"][:90],
            "what": first.get("summary") or first["title"],
            "why": "Mock režim – souhrn vytvoří Claude API v produkci.",
            "takeaway": "Mock režim – praktický dopad vytvoří Claude API v produkci.",
            "importance": event.get("importance", 50),
        })
        return
    payload = [{"title": a["title"], "summary": a.get("summary", ""),
                "source": a["source"], "url": a["url"]} for a in arts[:8]]
    result = llm.call_json(config.SMART_MODEL, SUMMARY_SYSTEM,
                           json.dumps(payload, ensure_ascii=False), max_tokens=1500)
    event["title"] = str(result.get("title", arts[0]["title"]))[:120]
    event["what"] = str(result.get("what", ""))
    event["why"] = str(result.get("why", ""))
    event["takeaway"] = str(result.get("takeaway", ""))
    event["importance"] = int(result.get("importance", event.get("importance", 50)))
    if result.get("category") in CATEGORY_KEYS:
        event["category"] = result["category"]


# ── Orchestrace ──────────────────────────────────────────────────

def run() -> int:
    state = load_state()
    events_by_id = {e["id"]: e for e in state["events"]}

    articles, report = feeds.fetch_all(state)
    ok = sum(1 for r in report if r["status"].startswith("ok"))
    log.info("Zdroje: %d/%d OK, nových článků: %d", ok, len(report), len(articles))
    for r in report:
        if r["status"] != "ok":
            log.warning("Zdroj %s: %s", r["name"], r["status"])

    kept = score_articles(articles)
    log.info("Po filtraci šumu: %d článků", len(kept))

    assignments = cluster(kept, state["events"])
    now_iso = _now().isoformat()
    touched = []
    for eid, items in assignments.items():
        ev = events_by_id.get(eid)
        if ev is None:
            ev = {"id": eid, "created": now_iso, "articles": [],
                  "category": items[0]["category"], "importance": 0,
                  "title": items[0]["article"].title}
            events_by_id[eid] = ev
            state["events"].append(ev)
        for item in items:
            a = item["article"]
            if all(x["url"] != a.url for x in ev["articles"]):
                ev["articles"].append({
                    "url": a.url, "title": a.title, "summary": a.summary,
                    "source": a.source, "domain": a.domain,
                    "published": a.published, "score": item["score"],
                })
        ev["updated"] = now_iso
        ev["importance"] = max(ev.get("importance", 0),
                               max(item["score"] for item in items))
        touched.append(ev)

    for ev in touched:
        try:
            summarize_event(ev)
        except Exception:
            log.exception("Souhrn události %s selhal", ev["id"])
        ev["importance_label"] = config.importance_label(ev.get("importance", 0))
        ev["multi_source"] = len({a["domain"] for a in ev["articles"]}) >= 2
    log.info("Aktualizováno událostí: %d", len(touched))

    # označit zpracované články jako viděné (i ty vyfiltrované)
    for a in articles:
        state["seen"][a.url] = now_iso

    # úklid
    seen_cutoff = (_now() - timedelta(days=config.SEEN_RETENTION_DAYS)).isoformat()
    state["seen"] = {u: t for u, t in state["seen"].items() if t >= seen_cutoff}
    ev_cutoff = (_now() - timedelta(days=config.ARCHIVE_DAYS)).isoformat()
    state["events"] = [e for e in state["events"] if e["updated"] >= ev_cutoff]
    state["events"].sort(key=lambda e: e["updated"], reverse=True)
    state["last_run"] = now_iso
    state["source_report"] = report

    save_state(state)
    log.info("Hotovo. Událostí ve stavu: %d", len(state["events"]))
    return 0


if __name__ == "__main__":
    sys.exit(run())
