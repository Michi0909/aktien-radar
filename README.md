# Aktien-Radar

Eigene Web-App: Aktien mit erhöhtem Handelsvolumen und niedrigem KGV (USA + Europa), Käufe großer US-Fonds laut 13F-Meldungen und eine eigene Watchlist.

Keine Anlageberatung – die App filtert und sortiert nur öffentliche Daten.

## Einrichtung (einmalig, ca. 10 Minuten)

1. **Neues Repository** auf GitHub anlegen, z. B. `aktien-radar` (öffentlich, damit GitHub Pages gratis ist).
2. **Dateien hochladen** („Add file → Upload files“): `index.html`, `fetch_data.py`, `config.json`, `requirements.txt`, `README.md`.
3. **Workflow anlegen:** „Add file → Create new file“, als Namen genau `.github/workflows/update.yml` eintippen (die Schrägstriche legen die Ordner an) und den Inhalt der Datei `update.yml` hineinkopieren. Speichern.
4. **E-Mail hinterlegen:** Die US-Börsenaufsicht SEC verlangt für automatische Abrufe eine Kontaktadresse, sonst gibt es keine Fondsdaten. Damit sie in deinem öffentlichen Repo nicht sichtbar ist: Settings → Secrets and variables → Actions → **New repository secret**, Name `SEC_KONTAKT`, Wert deine E-Mail-Adresse. (Alternativ in `config.json` bei `sec_kontakt` eintragen – dort ist sie aber öffentlich lesbar.)
5. **Schreibrechte für den Job:** Settings → Actions → General → ganz unten „Workflow permissions“ → **Read and write permissions** → Save.
6. **GitHub Pages einschalten:** Settings → Pages → Source „Deploy from a branch“, Branch `main`, Ordner `/ (root)` → Save.
7. **Ersten Lauf starten:** Reiter **Actions** → „Daten aktualisieren“ → **Run workflow**. Dauert ca. 5–10 Minuten. Danach liegt `data.json` im Repository.
8. App öffnen unter `https://DEIN-NAME.github.io/aktien-radar/` und am iPhone über Teilen → „Zum Home-Bildschirm“ ablegen.

Ab dann aktualisiert sich alles automatisch Mo–Fr gegen 23:40 Uhr (Wiener Zeit).

## Anpassen (`config.json`)

- `maerkte`: welche Börsen geladen werden, ab welchem Börsenwert (in Landeswährung) und wie viele Aktien höchstens.
- `zusatz_ticker`: einzelne Aktien, die sonst fehlen würden, im Yahoo-Format, z. B. `["PAL.VI", "ROS.VI"]`.
- `fonds`: beobachtete US-Fonds mit ihrer SEC-Nummer (CIK). Die CIK findest du auf sec.gov über die Firmensuche („EDGAR Full Text Search“ bzw. „Company Search“).

## Wenn etwas nicht klappt

- **Actions-Lauf rot:** Reiter Actions → Lauf antippen → Protokoll lesen. Häufigste Ursache: Schritt 5 (Schreibrechte) vergessen.
- **„Kennzahlen konnten nicht aktualisiert werden“:** Yahoo Finance hat die Anfrage abgelehnt (passiert gelegentlich bei GitHub-Servern). Die alten Daten bleiben erhalten; der nächste Lauf klappt meist wieder.
- **Keine Fondsdaten:** Secret `SEC_KONTAKT` prüfen (Schritt 4).
- Optional: Mit einem kostenlosen Schlüssel von openfigi.com (als Repository-Secret `OPENFIGI_KEY`) werden Fonds-Positionen schneller Tickern zugeordnet. Nötig ist das nicht.

## Datenquellen

- Kennzahlen: Yahoo Finance (inoffiziell, über die Python-Bibliothek `yfinance`)
- Fondsbestände: SEC EDGAR, Formular 13F-HR
- CUSIP → Ticker: OpenFIGI
