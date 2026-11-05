"""
Ogury One Supply API – Daily Reporting (pubdaily)
--------------------------------------------------
Nachfolger des alten "Publisher Reporting API v2.0"-Skripts (ogury_legacy_report.py).

Docs:
- Daily Reporting: https://support.ogury.com/ogury-one-supply-api/reporting/daily
- Access tokens:   https://support.ogury.com/ogury-one-supply-api/authentication/access-tokens

Unterschiede zur alten API:
- Auth: OAuth2 Client Credentials (client_id + client_secret -> Bearer-Token, 1h gültig)
  statt statischem API-Token. Credentials gibt es beim Ogury Account Manager.
- Namen (Account/Property/Placement) kommen direkt im Report -> kein /details-Lookup mehr.
- Metriken heißen jetzt revenue / adRequest / eCpm (alt: revenues / requests / ecpm).
- Lookback max. 31 Tage, mit groupBy=country nur 7 Tage.
- Paginierung über page/limit (Default 100 Zeilen/Seite).

Aufruf:
    py ogury_pubdaily_report.py                         # gestern
    py ogury_pubdaily_report.py 2026-10-01 2026-10-18   # eigener Zeitraum
"""

import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests
import pandas as pd

from env_utils import load_env_file

# ─────────────────────────────────────────────
# 1. KONFIGURATION
#    Credentials aus Umgebungsvariablen bzw. ssp_skripte/.env (nicht im Code hardcoden!)
# ─────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"
load_env_file(SCRIPT_DIR / ".env")

TOKEN_URL  = "https://api.ogury.com/oauth2/token"
REPORT_URL = "https://api.ogury.com/v1/reporting/pubdaily"

# Default der API: date, account, property, propertyType, placement, placementType, country.
# Wir lassen "country" weg (sonst nur 7 Tage Lookback + deutlich mehr Zeilen).
# Erlaubt sind lt. API (Stand 11/2026): date, account, property, propertyType, placement,
# placementType, bundleOrDomain, country. "integrationPartner" wird trotz Doku mit 400
# abgelehnt und auch nicht mitgeliefert -> bei Ogury angefragt; sobald verfügbar hier ergänzen.
GROUP_BY = [
    "date",
    "account",
    "property",
    "propertyType",
    "placement",
    "placementType",
]
METRICS    = ["impressions", "clicks", "revenue", "adRequest", "eCpm", "ctr"]
PAGE_LIMIT = 100   # Default lt. Doku; max. Seitengröße ist nicht dokumentiert

MAX_LOOKBACK_DAYS         = 31
MAX_LOOKBACK_DAYS_COUNTRY = 7


# ─────────────────────────────────────────────
# 2. AUTHENTIFIZIERUNG
#    POST /oauth2/token, Basic Auth (client_id:client_secret), grant_type=client_credentials
#    Token ist 3600 s gültig -> wird gecacht und kurz vor Ablauf neu geholt
# ─────────────────────────────────────────────
_token = {"value": None, "expires_at": 0.0}


def get_access_token(force_refresh=False):
    if not force_refresh and _token["value"] and time.time() < _token["expires_at"] - 60:
        return _token["value"]

    client_id     = os.environ.get("OGURY_CLIENT_ID")
    client_secret = os.environ.get("OGURY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("OGURY_CLIENT_ID / OGURY_CLIENT_SECRET fehlen oder sind leer (Umgebung bzw. ssp_skripte/.env)")

    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Ogury auth failed: {resp.status_code} {resp.text}")

    token_data = resp.json()
    _token["value"]      = token_data["access_token"]
    _token["expires_at"] = time.time() + int(token_data.get("expires_in", 3600))
    return _token["value"]


def _get(url, params, max_retries=5):
    """GET mit Bearer-Token; 401 -> Token einmal erneuern, 429/5xx -> Backoff + Retry."""
    refreshed = False
    for attempt in range(max_retries):
        resp = requests.get(
            url,
            headers={
                "accept": "application/json",
                "Authorization": f"Bearer {get_access_token()}",
            },
            params=params,
            timeout=60,
        )
        if resp.status_code == 401 and not refreshed:
            get_access_token(force_refresh=True)
            refreshed = True
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            try:
                wait = int(resp.headers.get("Retry-After", ""))
            except ValueError:
                wait = 5 * 2 ** attempt
            print(f"  HTTP {resp.status_code}, neuer Versuch in {wait}s ...")
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"Ogury request failed: {resp.status_code} {resp.text}")
        return resp.json()

    raise RuntimeError(f"Ogury request failed after {max_retries} attempts: {resp.status_code} {resp.text}")


# ─────────────────────────────────────────────
# 3. REPORT ABRUFEN (alle Seiten)
#    GET /v1/reporting/pubdaily
#    Response: {"stats": [{...}, ...], "total": n, "currentPage": p, "totalPages": tp}
# ─────────────────────────────────────────────
def check_lookback(start_date, group_by):
    max_days = MAX_LOOKBACK_DAYS_COUNTRY if "country" in group_by else MAX_LOOKBACK_DAYS
    days_back = (date.today() - date.fromisoformat(start_date)).days
    if days_back > max_days:
        raise ValueError(
            f"startDate {start_date} liegt {days_back} Tage zurück – "
            f"Ogury erlaubt max. {max_days} Tage"
            + (" bei groupBy=country." if "country" in group_by else ".")
        )


def fetch_pubdaily(start_date, end_date, group_by=None, metrics=None,
                   page_limit=PAGE_LIMIT, **filters):
    """
    Holt den Daily-Report über alle Seiten und gibt ihn als DataFrame zurück.

    filters (optional, lt. Doku): propertyIds="id1,id2", propertyType="WEB|ANDROID|IOS",
                                  countries="DE,AT"
    """
    group_by = group_by or GROUP_BY
    metrics  = metrics or METRICS
    check_lookback(start_date, group_by)

    params = {
        "startDate": start_date,
        "endDate":   end_date,
        "groupBy":   ",".join(group_by),
        "metrics":   ",".join(metrics),
        "sort":      "date:asc",
        "limit":     page_limit,
        **filters,
    }

    rows = []
    page = 1
    while True:
        data  = _get(REPORT_URL, {**params, "page": page})
        stats = data.get("stats", [])
        rows.extend(stats)

        total_pages = data.get("totalPages") or 1
        print(f"  Seite {page}/{total_pages}: {len(stats)} Zeilen")
        if page >= total_pages or not stats:
            break
        page += 1

    total = data.get("total")
    if total is not None and total != len(rows):
        print(f"  WARNUNG: API meldet total={total}, abgerufen wurden {len(rows)} Zeilen.")

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────
# 4. MAIN: Detail-CSV + Summary pro Placement + Summary pro Integration Partner
# ─────────────────────────────────────────────
def main(start_date=None, end_date=None):
    yesterday  = (date.today() - timedelta(days=1)).isoformat()
    start_date = start_date or yesterday
    end_date   = end_date or start_date

    print(f"Ogury pubdaily: {start_date} bis {end_date}, groupBy={','.join(GROUP_BY)}")
    df = fetch_pubdaily(start_date, end_date)

    if df.empty:
        print("Keine Zeilen zurückgegeben.")
        return df, pd.DataFrame(), pd.DataFrame()

    preferred_order = [
        "date", "accountName", "propertyName", "propertyType", "bundleOrDomain",
        "placementName", "placementType", "integrationPartner", "integrationType",
        "paymentSource", "deviceType", "country",
        "accountId", "propertyId", "placementId",
        "impressions", "adRequest", "clicks", "revenue", "eCpm", "ctr",
    ]
    ordered_cols   = [c for c in preferred_order if c in df.columns]
    remaining_cols = [c for c in df.columns if c not in ordered_cols]
    df = df[ordered_cols + remaining_cols]

    # Wie im alten Skript: Placements mit "GOOGLEOB" im Namen markieren (case-sensitive).
    if "placementName" in df.columns:
        df["ad_unit_category"] = df["placementName"].apply(
            lambda x: "GOOGLEOB" if isinstance(x, str) and "GOOGLEOB" in x else "Other"
        )

    def summarize(keys):
        keys = [k for k in keys if k in df.columns]
        out = (
            df.groupby(keys, dropna=False)
            .agg(
                impressions=("impressions", "sum"),
                adRequest=("adRequest", "sum"),
                clicks=("clicks", "sum"),
                revenue=("revenue", "sum"),
            )
            .reset_index()
            .sort_values("revenue", ascending=False)
        )
        out["eCpm"] = (out["revenue"] / out["impressions"].where(out["impressions"] > 0) * 1000).round(2)
        return out

    summary_placement = summarize(["accountName", "propertyName", "placementName", "placementId"])

    OUTPUT_DIR.mkdir(exist_ok=True)
    suffix = f"{start_date}_{end_date}"
    df.to_csv(OUTPUT_DIR / f"ogury_pubdaily_detailed_{suffix}.csv", index=False)
    summary_placement.to_csv(OUTPUT_DIR / f"ogury_pubdaily_by_placement_{suffix}.csv", index=False)
    print(f"{len(df)} Zeilen, CSVs gespeichert in {OUTPUT_DIR}")

    summary_partner = pd.DataFrame()
    if "integrationPartner" in df.columns:
        summary_partner = summarize(["integrationPartner"])
        summary_partner.to_csv(OUTPUT_DIR / f"ogury_pubdaily_by_partner_{suffix}.csv", index=False)
        print("\nNach Integration Partner:")
        print(summary_partner.to_string(index=False))
    print("\nTop 20 Placements:")
    print(summary_placement.head(20).to_string(index=False))

    return df, summary_placement, summary_partner


if __name__ == "__main__":
    main(*sys.argv[1:3])
