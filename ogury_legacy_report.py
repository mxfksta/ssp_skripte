"""
Ogury Publisher Reporting API v2.0 - Reporting Script  (LEGACY)
---------------------------------------------------------------
Wird durch ogury_pubdaily_report.py (Ogury One Supply API) abgelöst.
Bleibt nur für den Parallelbetrieb / Zahlenabgleich (ogury_compare_old_vs_new.py),
bis Ogury die alte API abschaltet.

Änderungen ggü. Original: Token aus Umgebungsvariable/.env (OGURY_API_TOKEN),
doppelter main()-Aufruf am Dateiende entfernt, Rückgabewert bei leeren Daten
konsistent (df, summary).

Retrieves impressions/revenue statistics grouped by ad_unit (placement level),
enriched with human-readable ad unit, asset (app/site) and organization names.

Docs:
- Getting started: https://support.ogury.com/reporting-api
- Statistics:       https://support.ogury.com/reporting-api/api-details/statistics
- Information:      https://support.ogury.com/reporting-api/api-details/information
"""

import os
from pathlib import Path

import requests
import pandas as pd
from datetime import date, timedelta

from env_utils import load_env_file

# ---------------------------------------------------------------------------
# 1) CONFIGURATION
# ---------------------------------------------------------------------------

load_env_file(Path(__file__).resolve().parent / ".env")

# Get this from your Ogury Publisher Dashboard > API Tokens section.
API_TOKEN = os.environ.get("OGURY_API_TOKEN")

BASE_URL = "https://exclusive-demand-report-api.ogury.co"

HEADERS = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_TOKEN}",
}

# ---------------------------------------------------------------------------
# 2) DATE RANGE
# ---------------------------------------------------------------------------
# Option A: yesterday only (for daily scheduled runs)
YESTERDAY = date.today() - timedelta(days=1)
DATE_FROM = YESTERDAY.strftime("%Y-%m-%d")
DATE_TO = YESTERDAY.strftime("%Y-%m-%d")

# Option B: custom date range -> just overwrite DATE_FROM / DATE_TO, e.g.:
# DATE_FROM = "2026-07-01"
# DATE_TO   = "2026-07-15"


# ---------------------------------------------------------------------------
# 3) FETCH ENRICHMENT DATA (asset / ad_unit / organization names)
# ---------------------------------------------------------------------------

def get_details():
    """Fetch org/asset/ad_unit hierarchy for ID -> name mapping."""
    resp = requests.get(f"{BASE_URL}/details", headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def build_lookup_maps(details_json):
    """Build ad_unit_id -> {name, asset_name, organization_name} lookup."""
    ad_unit_map = {}
    asset_map = {}

    for asset in details_json.get("assets", []):
        asset_id = asset["asset_id"]
        asset_map[asset_id] = {
            "asset_name": asset.get("name"),
            "asset_bundle": asset.get("bundle"),
            "asset_type": asset.get("asset_type", {}).get("label"),
            "organization_name": asset.get("organization", {}).get("name"),
        }
        for ad_unit in asset.get("ad_units", []):
            ad_unit_map[ad_unit["id"]] = {
                "ad_unit_name": ad_unit.get("name"),
                "format": ad_unit.get("format", {}).get("label"),
                "device_type": ad_unit.get("device_type", {}).get("label"),
                "asset_name": asset.get("name"),
                "asset_bundle": asset.get("bundle"),
                "organization_name": asset.get("organization", {}).get("name"),
            }

    return ad_unit_map, asset_map


# ---------------------------------------------------------------------------
# 4) FETCH STATISTICS
# ---------------------------------------------------------------------------

def get_stats(date_from, date_to, groups=None, metrics=None, date_group="day"):
    """
    Retrieve stats from the /stats endpoint (JSON).

    groups:  list of dimensions, e.g. ["date", "ad_unit"]
    metrics: list of metrics, e.g. ["impressions", "revenues", "ecpm"]
    """
    if groups is None:
        groups = ["date", "ad_unit"]
    if metrics is None:
        metrics = ["impressions", "revenues", "ecpm", "requests"]

    payload = {
        "filters": {
            "from": date_from,
            "to": date_to,
        },
        "groups": groups,
        "metrics": metrics,
        "date_group": date_group,
        "orders": ["date"],
    }

    resp = requests.post(f"{BASE_URL}/stats", headers=HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# 5) FLATTEN RESPONSE INTO A DATAFRAME
# ---------------------------------------------------------------------------

def flatten_stats(stats_json):
    """Flatten the nested {group: {...}, metrics: {...}} rows into a flat DataFrame."""
    rows = []
    for entry in stats_json.get("stats", []):
        row = {}
        row.update(entry.get("group", {}))
        row.update(entry.get("metrics", {}))
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 6) ENRICH WITH HUMAN-READABLE NAMES
# ---------------------------------------------------------------------------

def enrich_with_names(df, ad_unit_map):
    """Add ad_unit_name, asset_name, organization_name columns based on ad_unit id."""
    if "ad_unit" not in df.columns:
        return df

    df["ad_unit_name"] = df["ad_unit"].map(lambda x: ad_unit_map.get(x, {}).get("ad_unit_name"))
    df["asset_name"] = df["ad_unit"].map(lambda x: ad_unit_map.get(x, {}).get("asset_name"))
    df["organization_name"] = df["ad_unit"].map(lambda x: ad_unit_map.get(x, {}).get("organization_name"))
    df["format"] = df["ad_unit"].map(lambda x: ad_unit_map.get(x, {}).get("format"))

    return df


# ---------------------------------------------------------------------------
# 7) MAIN
# ---------------------------------------------------------------------------

def main():
    print(f"Fetching Ogury stats from {DATE_FROM} to {DATE_TO} ...")

    # Get lookup data first
    details_json = get_details()
    ad_unit_map, asset_map = build_lookup_maps(details_json)

    # Get the actual stats, grouped by date + ad_unit
    stats_json = get_stats(
        date_from=DATE_FROM,
        date_to=DATE_TO,
        groups=["date", "ad_unit"],
        metrics=["impressions", "revenues", "ecpm", "requests"],
        date_group="day",
    )

    df = flatten_stats(stats_json)

    if df.empty:
        print("No data returned for the selected date range.")
        return df, pd.DataFrame()

    df = enrich_with_names(df, ad_unit_map)

    # Reorder columns for readability
    preferred_order = [
        "date", "organization_name", "asset_name", "ad_unit_name", "format",
        "ad_unit", "impressions", "requests", "revenues", "ecpm",
    ]
    ordered_cols = [c for c in preferred_order if c in df.columns]
    remaining_cols = [c for c in df.columns if c not in ordered_cols]
    df = df[ordered_cols + remaining_cols]

    # Flag rows whose ad_unit_name contains "GOOGLEOB" (case-sensitive,
    # substring match). Added as the last column.
    df["ad_unit_category"] = df["ad_unit_name"].apply(
        lambda x: "GOOGLEOB" if isinstance(x, str) and "GOOGLEOB" in x else "Other"
    )

    # Group / aggregate by ad_unit_name across the whole date range (summary view)
    summary = (
        df.groupby(["organization_name", "asset_name", "ad_unit_name"], dropna=False)
        .agg(
            impressions=("impressions", "sum"),
            requests=("requests", "sum"),
            revenues=("revenues", "sum"),
        )
        .reset_index()
        .sort_values("revenues", ascending=False)
    )
    summary["ecpm"] = (summary["revenues"] / summary["impressions"] * 1000).round(2)

    # Save both the detailed (daily) and the grouped summary output
    df.to_csv("ogury_report_detailed.csv", index=False)
    summary.to_csv("ogury_report_grouped_by_adunit.csv", index=False)

    print("Detailed report saved to ogury_report_detailed.csv")
    print("Grouped summary saved to ogury_report_grouped_by_adunit.csv")
    print(summary.head(20))

    return df, summary


if __name__ == "__main__":
    df, summary = main()
