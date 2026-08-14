"""Naplní data/state.json ukázkovými událostmi pro náhled webu bez API.

Spuštění: python scripts/demo_seed.py && python site/build.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from radar import config  # noqa: E402

now = datetime.now(timezone.utc)


def ev(hours_ago, cat, importance, label, title, what, why, takeaway, articles):
    t = (now - timedelta(hours=hours_ago)).isoformat()
    return {
        "id": f"demo{hours_ago}{cat}"[:10],
        "created": t, "updated": t,
        "category": cat, "importance": importance, "importance_label": label,
        "title": title, "what": what, "why": why, "takeaway": takeaway,
        "multi_source": len({a[1] for a in articles}) >= 2,
        "articles": [
            {"url": u, "title": at, "summary": "", "source": s, "domain": u.split("/")[2],
             "published": t, "score": importance}
            for at, s, u in articles
        ],
    }


events = [
    ev(2, "seo", 88, "zásadní",
       "Google potvrdil core update, dokončení potrvá dva týdny",
       "Google spustil další core update vyhledávání. Podle oficiálního oznámení se bude vydávat postupně "
       "a dokončení potrvá až dva týdny. První výkyvy pozic jsou už vidět v nástrojích pro sledování SERP.",
       "Core updaty pravidelně přeskládají pořadí výsledků a dopadají i na české weby.",
       "Nedělejte teď unáhlené zásahy do webu. Poznamenejte si datum, sledujte pozice v Collabimu a vyhodnoťte dopad až po dokončení rolloutu.",
       [("Google Search Central: core update rolling out", "Google Search Central Blog", "https://developers.google.com/search/blog/example"),
        ("Google Core Update is live", "Search Engine Roundtable", "https://www.seroundtable.com/example"),
        ("What we know about the update", "Search Engine Land", "https://searchengineland.com/example")]),
    ev(4, "ppc", 72, "důležité",
       "Google Ads mění výchozí nastavení Performance Max kampaní",
       "Google Ads začíná u nových Performance Max kampaní automaticky zapínat rozšířené cílení. "
       "Změna se týká všech účtů postupně během příštích týdnů.",
       "Výchozí nastavení přebírá většina inzerentů bez kontroly – může to zvýšit podíl irelevantního trafficu.",
       "Při zakládání nových PMax kampaní zkontrolujte nastavení cílení a případně ho vypněte na úrovni kampaně.",
       [("PMax defaults are changing", "PPC Newsfeed", "https://www.ppcnewsfeed.com/example")]),
    ev(6, "cesky-marketing", 65, "důležité",
       "Seznam.cz spouští nový formát inzerce ve výsledcích hledání",
       "Seznam představil nový reklamní formát v SERPu, který kombinuje produktové karty s textovou inzercí. "
       "Formát je zatím v betě pro vybrané inzerenty přes Sklik.",
       "Pro české e-shopy jde o nový prostor ve vyhledávání, který konkurence zatím neobsadila.",
       "Ozvěte se svému Sklik zástupci ohledně zapojení do bety – u klientů s produktovým feedem to dává smysl otestovat.",
       [("Seznam testuje nový formát", "MediaGuru", "https://www.mediaguru.cz/example"),
        ("Sklik novinky", "Médiář", "https://www.mediar.cz/example")]),
    ev(20, "emailing", 55, "stojí za pozornost",
       "Gmail zpřísňuje pravidla pro hromadné odesílatele od října",
       "Google oznámil zpřísnění požadavků na hromadné odesílatele: povinný one-click unsubscribe "
       "a limit spam rate 0,3 % se nově týká už odesílatelů od 1 000 zpráv denně.",
       "Nesplnění limitů znamená propad doručitelnosti do spamu u celé domény.",
       "Zkontrolujte SPF/DKIM/DMARC a spam rate v Postmaster Tools u všech klientských domén, které rozesílají přes Ecomail.",
       [("Gmail bulk sender update", "Litmus", "https://www.litmus.com/example")]),
    ev(26, "socialni-site", 47, "stojí za pozornost",
       "Instagram testuje delší Reels až do 10 minut",
       "Instagram u části uživatelů testuje možnost nahrávat Reels do délky 10 minut a nový editor střihů.",
       "Delší formát mění strategii obsahu – Instagram tím míří na YouTube publikum.",
       "Zatím jen sledujte; pro klienty s video obsahem se může otevřít nový formát bez nutnosti YouTube produkce.",
       [("IG tests longer Reels", "Social Media Today", "https://www.socialmediatoday.com/example")]),
    ev(30, "ai", 35, "kontext",
       "HubSpot přidal AI agenty do bezplatného CRM",
       "HubSpot zpřístupnil část svých AI agentů (obsahový a prospektovací) i v bezplatné verzi CRM.",
       "Ukazuje směr, kterým jdou všechny velké platformy – AI funkce přestávají být prémiové.",
       "Pokud klient používá HubSpot free, stojí za to agenty vyzkoušet na přípravu podkladů.",
       [("HubSpot AI agents free tier", "MarTech", "https://martech.org/example")]),
]

state = {"seen": {}, "events": events, "last_run": now.isoformat(),
         "source_report": []}
config.DATA_DIR.mkdir(exist_ok=True)
config.STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"Zapsáno {len(events)} ukázkových událostí do {config.STATE_FILE}")
