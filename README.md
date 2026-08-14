# 📡 Marketing Radar

Automatický přehled novinek z online marketingu pro české marketéry, po vzoru
[AI Radaru](https://ai.patrikwagner.cz). Nezobrazuje články, ale **události**:
když o stejné novince napíše víc webů, vznikne jedna karta — co se stalo, proč
je to důležité, co z toho plyne — s odkazy na originály.

Běží úplně samo: **GitHub Actions** (zdarma) každé 2 hodiny stáhnou RSS feedy
kurátorovaných zdrojů, **Claude API** vyfiltruje šum, sloučí duplicity a napíše
české souhrny, web se publikuje přes **GitHub Pages** (zdarma).

## Jak to funguje

```
sources.yaml (≈50 zdrojů, 8 kategorií)
   │  RSS/Atom feedy, každé 2 h          (radar/feeds.py)
   ▼
Claude Haiku: skóre 0–100, filtr šumu    (radar/pipeline.py – score)
   ▼
Claude Haiku: sloučení do událostí       (radar/pipeline.py – cluster)
   ▼
Claude Sonnet: český souhrn + dopad      (radar/pipeline.py – summarize)
   ▼
data/state.json  →  statický web _site/  (site/build.py)
   ▼
GitHub Pages → radar.vceliste.cz
```

Volitelně navíc každý pracovní den v 7:30 pošle digest do Slacku
(`.github/workflows/slack.yml`).

## Nasazení (jednorázově, ~20 minut)

1. **Repozitář**: založ nový **veřejný** GitHub repozitář (Pages je u soukromých repo placená) a nahraj do něj
   obsah této složky.
2. **API klíč**: na [console.anthropic.com](https://console.anthropic.com)
   vytvoř API klíč a dobij kredit (~5 USD vydrží na start). V repozitáři:
   *Settings → Secrets and variables → Actions → New repository secret* —
   jméno `ANTHROPIC_API_KEY`, hodnota = klíč.
3. **GitHub Pages**: *Settings → Pages → Source: GitHub Actions*.
4. **První běh**: záložka *Actions → Aktualizace radaru → Run workflow*.
   Po doběhnutí je web na `https://<uzivatel>.github.io/<repo>/`.
5. **Doména** `radar.vceliste.cz`: *Settings → Pages → Custom domain* zadej
   `radar.vceliste.cz`; u správce DNS pro vceliste.cz přidej záznam
   `CNAME radar → <uzivatel>.github.io`. Zaškrtni *Enforce HTTPS*.
6. **Slack (volitelné)**: vytvoř [incoming webhook](https://api.slack.com/messaging/webhooks)
   pro kanál #marketingovenovinky a ulož ho jako secret `SLACK_WEBHOOK_URL`.

## Lokální vývoj a testování

```bash
pip install -r requirements.txt

# test bez API klíče (mock režim – souhrny jsou zástupné)
RADAR_MOCK=1 python -m radar.pipeline
python site/build.py          # web v _site/

# ostrý běh
export ANTHROPIC_API_KEY=sk-ant-...
python -m radar.pipeline
```

## Ladění

| Co | Kde |
|---|---|
| Přidat/ubrat zdroj | `sources.yaml` (bez `feed:` se zkusí najít automaticky) |
| Přísnost filtru šumu | `RADAR_MIN_SCORE` (výchozí 45) v workflow, nebo `radar/config.py` |
| Frekvence běhu | `cron` v `.github/workflows/update.yml` |
| Modely | `RADAR_FAST_MODEL`, `RADAR_SMART_MODEL` |
| Vzhled webu | `site/templates/` + `site/static/style.css` (logika se nemění) |

## Náklady

GitHub Actions i Pages jsou zdarma. Claude API při ~9 bězích denně a tomto
objemu zdrojů vychází řádově na **jednotky USD měsíčně** (Haiku dělá levnou
většinu práce, Sonnet píše jen finální souhrny).
