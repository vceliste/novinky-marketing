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
import re
import sys
from datetime import datetime, timedelta, timezone

from . import config, feeds, llm
from .feeds import Article

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("radar.pipeline")

CATEGORY_KEYS = ["cesky-marketing", "seo", "ppc", "emailing",
                 "socialni-site", "analytika", "obsah", "ai"]

PERSONA_RUBRIC = """CÍLOVÉ PUBLIKUM (důležitost hodnoť VÝHRADNĚ jeho očima):
1. PRIMÁRNÍ: marketingový manažer české firmy. Řídí rozpočet a kanály (Google Ads, Sklik, Meta, \
e-mail, SEO, web, sociální sítě). Zajímá ho jen: Co mám změnit? Co mě bude stát/ušetří peníze? \
Jaká nová příležitost nebo riziko se objevuje?
2. SEKUNDÁRNÍ: český freelance specialista na jednu oblast (SEO / PPC / e-mailing / soc. sítě / \
analytika) – ocení i hlubší novinky ve své specializaci.

ŠKÁLA DŮLEŽITOSTI:
- 80–100 (zásadní): mění praxi nebo rozpočty v ČR – změny algoritmů a pravidel platforem, ceníky, \
nové formáty/funkce dostupné v ČR/EU, regulace a termíny (DSA, GDPR, consent), zásadní změny \
nástrojů běžně používaných v ČR (GA4, Ecomail, Collabim, Sklik…).
- 60–79 (důležité): významná novinka s reálným dopadem na práci cílovky, i když ne okamžitým; \
důležitá čísla o českém trhu; velké novinky zatím jen v USA, které do ČR pravděpodobně dorazí.
- 40–59 (stojí za pozornost): menší updaty, zajímavé studie, kontext trendů.
- 0–39 (šum/zákulisí): OBOROVÉ ZÁKULISÍ – tendry a výběrová řízení, personální změny, dění \
v asociacích a mediálních/reklamních agenturách (např. „SPIR hledá dodavatele AdMonitoringu" – \
pro cílovku NEDŮLEŽITÉ), PR a fundraising firem, obecné AI zprávy bez marketingového využití, \
návody a evergreen obsah.

Bonus ~10 bodů za přímou relevanci pro český trh (Sklik, Zboží, Heureka, česká regulace, česká data)."""

SCORE_SYSTEM = """Jsi editor českého webu Marketing Radar – přehledu novinek z online marketingu.

""" + PERSONA_RUBRIC + """

Ohodnoť každý článek skóre 0–100 podle škály výše.
Ke každému článku urči i nejvhodnější kategorii z: {cats}.

Odpověz POUZE validním JSON polem:
[{{"id": "...", "score": 0-100, "category": "...", "is_news": true/false}}]
is_news=false pro návody, evergreen obsah a marketing nástrojů (i kdyby byly kvalitní)."""

CLUSTER_SYSTEM = """Jsi editor zpravodajského webu. Dostaneš seznam EXISTUJÍCÍCH UDÁLOSTÍ \
(id + titulek) a seznam NOVÝCH ČLÁNKŮ. Ke každému článku urči, zda popisuje STEJNOU KONKRÉTNÍ \
UDÁLOST jako některá existující (stejné oznámení/funkce/rozhodnutí), nebo je to nová událost.

Pozor: stejná událost bývá formulovaná různě – „srpnový spam update" = „třetí letošní spam update", \
pokud jde o tentýž update. Rozhoduje věcná totožnost, ne slova.
Slučuj jen jasné případy – při pochybnostech označ jako novou událost ("new").
Články o stejné nové věci mezi sebou slučuj přes "group" (stejné číslo skupiny).

Odpověz POUZE validním JSON polem:
[{"id": "...", "event": "<id existující události>" | "new", "group": <číslo skupiny pro nové, jinak null>}]"""

SUMMARY_SYSTEM = """Píšeš pro český web Marketing Radar. Z dodaných článků (titulky, anotace, zdroje) \
napiš souhrn JEDNÉ události.

""" + PERSONA_RUBRIC + """

PRAVIDLA:
- Vycházej VÝHRADNĚ z dodaných textů. Nedoplňuj fakta z vlastní paměti. Co ve zdrojích není, nepiš.
- Piš česky, věcně, bez marketingových frází a bez superlativů.
- Pokud se zdroje v něčem liší nebo něco není potvrzené, řekni to.
- "takeaway" piš jako konkrétní doporučení pro primární personu (marketingový manažer české firmy): \
co udělat, zkontrolovat, naplánovat nebo sledovat.
- "importance" urči přísně podle škály výše – při pochybnostech dej NIŽŠÍ hodnotu.

Odpověz POUZE validním JSON objektem:
{
  "title": "úderný český titulek, max 90 znaků",
  "what": "Co se stalo – 2–3 věty.",
  "why": "Proč je to důležité pro cílovku – 1–2 věty.",
  "takeaway": "Co z toho plyne – 1–2 konkrétní věty pro marketingového manažera v ČR.",
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
    # malé/přehledové klíče první, velké (seen, events) na konec souboru
    ordered = {k: state[k] for k in ("last_run", "source_report", "feed_overrides") if k in state}
    ordered.update({k: v for k, v in state.items() if k not in ordered})
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=1)


def event_id(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def _norm_title(t: str) -> str:
    return re.sub(r"\W+", " ", (t or "").lower()).strip()


def _add_article(ev: dict, a, score: int) -> None:
    """Přidá článek do události; duplicity (stejný zdroj + titulek) slučuje.

    Když máme stejný článek přes Google News i napřímo, drží se přímý odkaz.
    """
    for x in ev["articles"]:
        if x["url"] == a.url or (x["source"] == a.source
                                 and _norm_title(x["title"]) == _norm_title(a.title)):
            if "news.google.com" in x["url"] and "news.google.com" not in a.url:
                x.update({"url": a.url, "title": a.title, "published": a.published})
            return
    ev["articles"].append({
        "url": a.url, "title": a.title, "summary": a.summary,
        "source": a.source, "domain": a.domain,
        "published": a.published, "score": score,
    })


BACKFILL_FLAG = config.DATA_DIR / "backfill.flag"

MERGE_SYSTEM = """Jsi editor zpravodajského webu. Dostaneš seznam publikovaných UDÁLOSTÍ \
(id, titulek, kategorie, datum). Najdi skupiny událostí, které popisují TU SAMOU konkrétní věc \
(stejné oznámení, tentýž update platformy – i když jsou titulky formulované různě, \
např. „srpnový spam update" = „třetí letošní spam update").

Buď konzervativní: slučuj jen jasné případy. Různé updaty, různé funkce nebo jen podobná témata NEslučuj.
Odpověz POUZE validním JSON polem skupin id: [["id1","id2"], ...]. Když není co sloučit, vrať []."""


def merge_duplicate_events(state: dict, events_by_id: dict) -> None:
    """Douklízecí krok: sloučí duplicitní události z posledních dní."""
    window = (_now() - timedelta(days=10)).isoformat()
    recent = [e for e in state["events"] if e["updated"] >= window and e.get("title")]
    if len(recent) < 2:
        return
    payload = [{"id": e["id"], "title": e["title"], "category": e["category"],
                "day": e["created"][:10]} for e in recent[:150]]
    try:
        groups = llm.call_json(config.FAST_MODEL, MERGE_SYSTEM,
                               json.dumps(payload, ensure_ascii=False))
    except Exception:
        log.exception("Hledání duplicitních událostí selhalo")
        return
    if not isinstance(groups, list):
        return
    valid = {e["id"] for e in recent}
    merged = 0
    for group in groups:
        ids = [i for i in group if isinstance(i, str) and i in valid and i in events_by_id]
        if len(ids) < 2:
            continue
        evs = sorted((events_by_id[i] for i in ids), key=lambda e: e["created"])
        keep, rest = evs[0], evs[1:]
        for r in rest:
            for x in r.get("articles", []):
                dup = next((y for y in keep["articles"]
                            if y["url"] == x["url"]
                            or (y["source"] == x["source"]
                                and _norm_title(y["title"]) == _norm_title(x["title"]))), None)
                if dup is None:
                    keep["articles"].append(x)
            keep["importance"] = max(keep.get("importance", 0), r.get("importance", 0))
            if r.get("image") and not keep.get("image"):
                keep["image"], keep["image_from"] = r["image"], r.get("image_from")
            state["events"].remove(r)
            events_by_id.pop(r["id"], None)
            valid.discard(r["id"])
        keep["updated"] = _now().isoformat()
        try:
            summarize_event(keep)
        except Exception:
            log.exception("Přegenerování souhrnu po sloučení %s selhalo", keep["id"])
        keep["importance_label"] = config.importance_label(keep.get("importance", 0))
        keep["multi_source"] = len({a["source"] for a in keep["articles"]}) >= 2
        merged += len(rest)
    if merged:
        log.info("Sloučeno %d duplicitních událostí", merged)


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

    # Jednorázový backfill: soubor data/backfill.flag s počtem dní (např. "30")
    backfill = None
    if BACKFILL_FLAG.exists():
        try:
            backfill = int(BACKFILL_FLAG.read_text().strip() or "30")
        except ValueError:
            backfill = 30
        config.MAX_ARTICLE_AGE_DAYS = backfill
        config.MAX_NEW_PER_RUN = 120
        log.info("BACKFILL: sahám %d dní zpět, až 120 článků v tomto běhu", backfill)

    articles, report = feeds.fetch_all(state)

    if backfill and len(articles) < 20:
        BACKFILL_FLAG.unlink(missing_ok=True)
        log.info("BACKFILL dokončen (nových článků už jen %d), vracím se k běžnému režimu",
                 len(articles))
    ok = sum(1 for r in report if r["status"].startswith("ok"))
    log.info("Zdroje: %d/%d OK, nových článků: %d", ok, len(report), len(articles))
    for r in report:
        if not r["status"].startswith("ok"):
            log.warning("Zdroj %s: %s", r["name"], r["status"])
        elif r["status"] != "ok":
            log.info("Zdroj %s: %s", r["name"], r["status"])

    kept = score_articles(articles)
    log.info("Po filtraci šumu: %d článků", len(kept))

    assignments = cluster(kept, state["events"])
    now_iso = _now().isoformat()
    touched = []
    for eid, items in assignments.items():
        ev = events_by_id.get(eid)
        if ev is None:
            # datum události = publikace nejstaršího článku (důležité pro archiv/backfill)
            created = min(item["article"].published for item in items)
            ev = {"id": eid, "created": created, "articles": [],
                  "category": items[0]["category"], "importance": 0,
                  "title": items[0]["article"].title}
            events_by_id[eid] = ev
            state["events"].append(ev)
        for item in items:
            _add_article(ev, item["article"], item["score"])
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
        ev["multi_source"] = len({a["source"] for a in ev["articles"]}) >= 2
    log.info("Aktualizováno událostí: %d", len(touched))

    # Doplnění souhrnů, které v minulých bězích selhaly (událost se bez nich nepublikuje)
    if not config.MOCK:
        attempted = {ev["id"] for ev in touched}
        missing = [e for e in state["events"]
                   if not e.get("what") and e.get("articles")
                   and e["id"] not in attempted][:20]
        if missing:
            log.info("Doplňuji %d chybějících souhrnů z minulých běhů", len(missing))
        for ev in missing:
            try:
                summarize_event(ev)
                ev["importance_label"] = config.importance_label(ev.get("importance", 0))
                ev["multi_source"] = len({a["source"] for a in ev["articles"]}) >= 2
            except Exception:
                log.exception("Doplnění souhrnu %s selhalo", ev["id"])

    # Jednorázový úklid duplicitních článků ve starších událostech
    if not state.get("article_dedup_v1"):
        for e in state["events"]:
            kept: dict[str, dict] = {}
            for x in e.get("articles", []):
                key = _norm_title(x.get("title", "")) or x["url"]
                if key in kept:
                    k = kept[key]
                    if "news.google.com" in k["url"] and "news.google.com" not in x["url"]:
                        k.update({"url": x["url"], "title": x["title"],
                                  "published": x["published"], "domain": x["domain"]})
                    continue
                kept[key] = x
            e["articles"] = list(kept.values())
            e["multi_source"] = len({x["source"] for x in e["articles"]}) >= 2
        state["article_dedup_v1"] = True
        log.info("Úklid duplicitních článků dokončen")

    # Jednorázové přehodnocení starších událostí novým hodnocením pro cílovou personu
    if not config.MOCK and not state.get("persona_rescore_v2"):
        touched_ids = {ev["id"] for ev in touched}
        candidates = [e for e in state["events"]
                      if e["id"] not in touched_ids and e.get("title")
                      and e.get("importance", 0) >= 40][:60]
        log.info("Persona rescore: přehodnocuji %d starších událostí", len(candidates))
        for ev in candidates:
            try:
                summarize_event(ev)
                ev["importance_label"] = config.importance_label(ev.get("importance", 0))
            except Exception:
                log.exception("Rescore události %s selhal", ev["id"])
        state["persona_rescore_v2"] = True

    # Douklízení: sloučení duplicitních událostí (stejná věc, různé titulky)
    if not config.MOCK:
        merge_duplicate_events(state, events_by_id)

    # Náhledové obrázky k důležitým událostem (og:image z původních článků)
    if not config.MOCK:
        try:
            from . import images
            images.ensure_images(state["events"])
        except Exception:
            log.exception("Stahování obrázků selhalo")

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
