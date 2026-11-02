"""Aktien-Radar – Datenabruf.

Läuft als GitHub Action (siehe .github/workflows/update.yml) und schreibt data.json.
  1. Kennzahlen (Kurs, Volumen, KGV, Marktkap.) über den Yahoo-Finance-Screener
  2. 13F-Meldungen ausgewählter Fonds direkt von der SEC (EDGAR)
  3. CUSIP -> Ticker über OpenFIGI (Ergebnis wird in cusip_map.json zwischengespeichert)

Fällt eine Quelle aus, bleiben die alten Daten dieser Quelle erhalten.
"""
import json
import os
import re
import sys
import time
import datetime as dt
import xml.etree.ElementTree as ET

import requests
import yfinance as yf
from yfinance import EquityQuery

HIER = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(HIER, "config.json"), encoding="utf-8"))
DATA_PFAD = os.path.join(HIER, "data.json")
CUSIP_PFAD = os.path.join(HIER, "cusip_map.json")


def log(*a):
    print(*a, flush=True)


def zahl(x):
    try:
        if x is None:
            return None
        f = float(x)
        return f if f == f else None  # NaN raus
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- Wechselkurse
WAEHRUNGEN = ["USD", "GBP", "CHF", "SEK", "DKK", "NOK"]


def lade_fx():
    """Faktor: 1 Einheit Währung = x EUR."""
    fx = {"EUR": 1.0}
    paare = [f"{w}EUR=X" for w in WAEHRUNGEN]
    try:
        df = yf.download(paare, period="5d", progress=False, auto_adjust=False)["Close"]
        for w in WAEHRUNGEN:
            s = df[f"{w}EUR=X"].dropna()
            if len(s):
                fx[w] = float(s.iloc[-1])
    except Exception as e:
        log("FX-Fehler:", e)
    fx.setdefault("USD", 0.86)
    fx["GBp"] = fx.get("GBP", 1.15) / 100  # Pence
    fx["GBX"] = fx["GBp"]
    return fx


# ---------------------------------------------------------------- Kennzahlen
def zeile_aus_quote(q, markt, fx):
    sym = q.get("symbol")
    if not sym:
        return None
    waehr = q.get("currency") or "USD"
    kurs = zahl(q.get("regularMarketPrice"))
    faktor = fx.get(waehr)
    vol = zahl(q.get("regularMarketVolume"))
    v10 = zahl(q.get("averageDailyVolume10Day"))
    v3m = zahl(q.get("averageDailyVolume3Month"))
    aktien = zahl(q.get("sharesOutstanding"))
    mkap = zahl(q.get("marketCap"))
    # Marktkap. in EUR: wenn möglich aus Kurs x Aktien (robust bei Pence-Notierung)
    if kurs and aktien and faktor:
        mkap_eur = kurs * aktien * faktor
    elif mkap and faktor:
        mkap_eur = mkap * (fx["GBP"] if waehr in ("GBp", "GBX") else faktor)
    else:
        mkap_eur = None
    kgv = zahl(q.get("trailingPE"))
    if kgv is not None and kgv <= 0:
        kgv = None
    fkgv = zahl(q.get("forwardPE"))
    if fkgv is not None and fkgv <= 0:
        fkgv = None
    div = zahl(q.get("trailingAnnualDividendYield"))
    p52 = zahl(q.get("fiftyTwoWeekChangePercent"))      # Screener: schon in %
    if p52 is None and zahl(q.get("52WeekChange")) is not None:
        p52 = zahl(q.get("52WeekChange")) * 100           # .info: als Anteil
    return {
        "s": sym,
        "n": q.get("longName") or q.get("shortName") or sym,
        "m": markt,
        "b": q.get("fullExchangeName") or q.get("exchange"),
        "w": waehr,
        "p": kurs,
        "c": zahl(q.get("regularMarketChangePercent")),
        "v": vol,
        "v10": v10,
        "v3m": v3m,
        "kgv": round(kgv, 2) if kgv else None,
        "fkgv": round(fkgv, 2) if fkgv else None,
        "kbv": zahl(q.get("priceToBook")),
        "div": round(div * 100, 2) if div else None,
        "mk": round(mkap_eur) if mkap_eur else None,
        "ums": round(v3m * kurs * faktor) if (v3m and kurs and faktor) else None,
        "h52": zahl(q.get("fiftyTwoWeekHigh")),
        "l52": zahl(q.get("fiftyTwoWeekLow")),
        "p52": p52,
    }


def screen_markt(name, cfg, fx):
    boersen = cfg["boersen"]
    if len(boersen) == 1:
        b_q = EquityQuery("eq", ["exchange", boersen[0]])
    else:
        b_q = EquityQuery("is-in", ["exchange", *boersen])
    q = EquityQuery("and", [b_q, EquityQuery("gt", ["intradaymarketcap", cfg["min_marktkap"]])])
    zeilen, offset, maxn = [], 0, cfg.get("max_anzahl", 250)
    while offset < maxn:
        for versuch in range(3):
            try:
                r = yf.screen(q, offset=offset, size=min(250, maxn - offset),
                              sortField="intradaymarketcap", sortAsc=False)
                break
            except Exception as e:
                log(f"  {name}: Fehler ({e}), neuer Versuch …")
                time.sleep(5 * (versuch + 1))
        else:
            break
        quotes = r.get("quotes", []) if isinstance(r, dict) else []
        for qu in quotes:
            z = zeile_aus_quote(qu, name, fx)
            if z:
                zeilen.append(z)
        total = r.get("total", 0) if isinstance(r, dict) else 0
        offset += len(quotes)
        if not quotes or offset >= total:
            break
        time.sleep(1.5)
    log(f"  {name}: {len(zeilen)} Aktien")
    return zeilen


def zusatz_ticker(ticker, fx):
    zeilen = []
    for t in ticker:
        try:
            info = yf.Ticker(t).info
            info["symbol"] = info.get("symbol") or t
            z = zeile_aus_quote(info, "Zusatz", fx)
            if z:
                zeilen.append(z)
        except Exception as e:
            log(f"  Zusatz {t}: {e}")
        time.sleep(1)
    return zeilen


def lade_aktien():
    fx = lade_fx()
    log("Wechselkurse:", {k: round(v, 4) for k, v in fx.items()})
    alle, gesehen = [], set()
    for name, cfg in CONFIG["maerkte"].items():
        for z in screen_markt(name, cfg, fx):
            if z["s"] not in gesehen:
                gesehen.add(z["s"])
                alle.append(z)
        time.sleep(1.5)
    extra = [t for t in CONFIG.get("zusatz_ticker", []) if t not in gesehen]
    for z in zusatz_ticker(extra, fx):
        gesehen.add(z["s"])
        alle.append(z)
    return alle


# ---------------------------------------------------------------- 13F / SEC
def sec_session():
    s = requests.Session()
    kontakt = os.environ.get("SEC_KONTAKT") or CONFIG.get("sec_kontakt") or ""
    if "@" not in kontakt or "example.com" in kontakt:
        raise RuntimeError("keine E-Mail für die SEC hinterlegt (Secret SEC_KONTAKT oder config.json)")
    s.headers["User-Agent"] = kontakt if kontakt.lower().startswith("aktien") else "Aktien-Radar " + kontakt
    s.headers["Accept-Encoding"] = "gzip, deflate"
    return s


def sec_get(s, url, als_json=True):
    for versuch in range(3):
        r = s.get(url, timeout=30)
        time.sleep(0.25)  # SEC erlaubt max. 10 Anfragen/Sekunde
        if r.status_code == 200:
            return r.json() if als_json else r.text
        if r.status_code in (429, 503):
            time.sleep(3 * (versuch + 1))
            continue
        raise RuntimeError(f"HTTP {r.status_code} für {url}")
    raise RuntimeError(f"keine Antwort von {url}")


def letzte_13f(s, cik):
    sub = sec_get(s, f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    rec = sub["filings"]["recent"]
    meldungen = {}
    for form, acc, periode, datum in zip(rec["form"], rec["accessionNumber"],
                                         rec["reportDate"], rec["filingDate"]):
        if form != "13F-HR" or not periode:
            continue
        # pro Stichtag die neueste Meldung
        if periode not in meldungen or datum > meldungen[periode]["datum"]:
            meldungen[periode] = {"acc": acc, "periode": periode, "datum": datum}
    sortiert = sorted(meldungen.values(), key=lambda m: m["periode"], reverse=True)
    return sub.get("name"), sortiert[:2]


def lade_bestand(s, cik, acc):
    ordner = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
    idx = sec_get(s, ordner + "/index.json")
    xmls = [i for i in idx["directory"]["item"]
            if i["name"].lower().endswith(".xml") and i["name"].lower() != "primary_doc.xml"]
    if not xmls:
        raise RuntimeError("keine Informationstabelle gefunden")
    datei = max(xmls, key=lambda i: int(i.get("size") or 0))["name"]
    text = sec_get(s, f"{ordner}/{datei}", als_json=False)
    text = re.sub(r"""\sxmlns(:\w+)?=("[^"]*"|'[^']*')""", "", text)   # Namespaces entfernen
    text = re.sub(r"""\s\w+:[\w-]+=("[^"]*"|'[^']*')""", "", text)      # z. B. xsi:schemaLocation
    text = re.sub(r"<(/?)\w+:", r"<\1", text)
    wurzel = ET.fromstring(text.encode("utf-8"))
    bestand = {}
    for e in wurzel.iter("infoTable"):
        if (e.findtext("putCall") or "").strip():
            continue  # Optionen ignorieren
        if (e.findtext("shrsOrPrnAmt/sshPrnamtType") or "SH").strip() != "SH":
            continue
        cusip = (e.findtext("cusip") or "").strip().upper()
        stueck = zahl(e.findtext("shrsOrPrnAmt/sshPrnamt")) or 0
        wert = zahl(e.findtext("value")) or 0
        if not cusip:
            continue
        b = bestand.setdefault(cusip, {"name": (e.findtext("nameOfIssuer") or "").strip(),
                                       "stueck": 0, "wert": 0})
        b["stueck"] += stueck
        b["wert"] += wert
    return bestand


def vergleiche(neu, alt, min_plus=0.05):
    kaeufe, verkaeufe = [], []
    for cusip, b in neu.items():
        vor = alt.get(cusip, {}).get("stueck", 0)
        if b["stueck"] <= 0:
            continue
        preis = b["wert"] / b["stueck"]
        delta = b["stueck"] - vor
        if vor == 0:
            kaeufe.append({"cusip": cusip, "name": b["name"], "art": "neu", "pct": None,
                           "wert": round(b["wert"]), "plus": round(b["wert"])})
        elif delta / vor >= min_plus:
            kaeufe.append({"cusip": cusip, "name": b["name"], "art": "plus",
                           "pct": round(delta / vor * 100, 1),
                           "wert": round(b["wert"]), "plus": round(delta * preis)})
    for cusip, b in alt.items():
        jetzt = neu.get(cusip, {}).get("stueck", 0)
        if b["stueck"] > 0 and jetzt == 0:
            verkaeufe.append({"cusip": cusip, "name": b["name"], "art": "raus",
                              "wert": round(b["wert"])})
    kaeufe.sort(key=lambda k: k["plus"], reverse=True)
    verkaeufe.sort(key=lambda k: k["wert"], reverse=True)
    return kaeufe, verkaeufe


def cusip_zu_ticker(cusips):
    cache = {}
    if os.path.exists(CUSIP_PFAD):
        cache = json.load(open(CUSIP_PFAD, encoding="utf-8"))
    offen = [c for c in cusips if c not in cache]
    key = os.environ.get("OPENFIGI_KEY")
    paket = 100 if key else 10
    kopf = {"Content-Type": "application/json"}
    if key:
        kopf["X-OPENFIGI-APIKEY"] = key
    for i in range(0, len(offen), paket):
        teil = offen[i:i + paket]
        jobs = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in teil]
        try:
            r = requests.post("https://api.openfigi.com/v3/mapping", json=jobs,
                              headers=kopf, timeout=30)
            if r.status_code == 429:
                time.sleep(60)
                r = requests.post("https://api.openfigi.com/v3/mapping", json=jobs,
                                  headers=kopf, timeout=30)
            r.raise_for_status()
            for c, antwort in zip(teil, r.json()):
                daten = antwort.get("data") or []
                t = daten[0].get("ticker") if daten else None
                cache[c] = t.replace("/", "-") if t else None
        except Exception as e:
            log("OpenFIGI-Fehler:", e)
            break
        time.sleep(0.5 if key else 2.6)  # ohne Key max. 25 Anfragen/Minute
    json.dump(cache, open(CUSIP_PFAD, "w", encoding="utf-8"), indent=0, sort_keys=True)
    return cache


def lade_fonds():
    s = sec_session()
    fonds_out, sammel = [], {}
    heute = dt.date.today()
    for f in CONFIG["fonds"]:
        try:
            sec_name, meldungen = letzte_13f(s, f["cik"])
            if len(meldungen) < 2:
                log(f"  {f['name']}: zu wenige 13F-Meldungen")
                continue
            neu_m, alt_m = meldungen
            if (heute - dt.date.fromisoformat(neu_m["periode"])).days > 200:
                log(f"  {f['name']}: letzte Meldung zu alt ({neu_m['periode']})")
                continue
            neu = lade_bestand(s, f["cik"], neu_m["acc"])
            alt = lade_bestand(s, f["cik"], alt_m["acc"])
            kaeufe, verkaeufe = vergleiche(neu, alt)
            gesamt = sum(b["wert"] for b in neu.values())
            fonds_out.append({
                "name": f["name"], "sec_name": sec_name, "cik": f["cik"],
                "periode": neu_m["periode"], "vorperiode": alt_m["periode"],
                "gemeldet": neu_m["datum"], "positionen": len(neu), "volumen": round(gesamt),
                "kaeufe": kaeufe[:40], "verkaeufe": verkaeufe[:20],
            })
            for k in kaeufe:
                e = sammel.setdefault(k["cusip"], {"cusip": k["cusip"], "name": k["name"],
                                                   "fonds": [], "plus": 0})
                e["fonds"].append({"f": f["name"], "art": k["art"], "pct": k["pct"],
                                   "plus": k["plus"], "anteil": round(k["wert"] / gesamt * 100, 2) if gesamt else None})
                e["plus"] += k["plus"]
            log(f"  {f['name']}: {len(neu)} Positionen, {len(kaeufe)} Käufe ({neu_m['periode']})")
        except Exception as e:
            log(f"  {f['name']}: Fehler – {e}")
    top = sorted(sammel.values(), key=lambda e: (len(e["fonds"]), e["plus"]), reverse=True)[:150]
    cusips = {e["cusip"] for e in top}
    for f in fonds_out:
        cusips.update(k["cusip"] for k in f["kaeufe"] + f["verkaeufe"])
    mapping = cusip_zu_ticker(sorted(cusips))
    for e in top:
        e["t"] = mapping.get(e["cusip"])
    for f in fonds_out:
        for k in f["kaeufe"] + f["verkaeufe"]:
            k["t"] = mapping.get(k["cusip"])
    return {"fonds": fonds_out, "top": top}


# ---------------------------------------------------------------- Hauptteil
def main():
    alt = {}
    if os.path.exists(DATA_PFAD):
        try:
            alt = json.load(open(DATA_PFAD, encoding="utf-8"))
        except Exception:
            pass
    jetzt = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    out = {"version": 1, "stand": alt.get("stand", {}), "hinweise": []}

    log("== Kennzahlen (Yahoo Finance)")
    try:
        aktien = lade_aktien()
        if len(aktien) < 50:
            raise RuntimeError(f"nur {len(aktien)} Aktien erhalten")
        out["aktien"] = aktien
        out["stand"]["aktien"] = jetzt
    except Exception as e:
        log("Kennzahlen fehlgeschlagen:", e)
        out["aktien"] = alt.get("aktien", [])
        out["hinweise"].append(f"Kennzahlen konnten zuletzt nicht aktualisiert werden ({e}).")

    log("== 13F-Meldungen (SEC)")
    try:
        f = lade_fonds()
        if not f["fonds"]:
            raise RuntimeError("keine Fondsdaten erhalten")
        out["fonds"] = f
        out["stand"]["fonds"] = jetzt
    except Exception as e:
        log("13F fehlgeschlagen:", e)
        out["fonds"] = alt.get("fonds", {"fonds": [], "top": []})
        out["hinweise"].append(f"Fondsdaten konnten zuletzt nicht aktualisiert werden ({e}).")

    if not os.path.exists(CUSIP_PFAD):
        json.dump({}, open(CUSIP_PFAD, "w", encoding="utf-8"))
    with open(DATA_PFAD, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    log(f"Fertig: {len(out['aktien'])} Aktien, {len(out['fonds']['fonds'])} Fonds")
    if not out["aktien"] and not out["fonds"]["fonds"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
