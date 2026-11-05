"""
Ogury: Parallelbetrieb alte API (Publisher Reporting v2.0) vs. neue API (One Supply pubdaily)
---------------------------------------------------------------------------------------------
Holt denselben Zeitraum aus beiden APIs und vergleicht Impressions, Requests und Revenue
  a) pro Tag (Gesamtsumme)
  b) pro Tag + Placement (Join über die Placement-/Ad-Unit-ID)

Aufruf:
    py ogury_compare_old_vs_new.py                         # die letzten 3 Tage bis gestern
    py ogury_compare_old_vs_new.py 2026-10-12 2026-10-18   # eigener Zeitraum
"""

import sys
from datetime import date, timedelta

import pandas as pd

import ogury_legacy_report as old
import ogury_pubdaily_report as new

# Metrik-Mapping alt -> neu
METRIC_MAP = {"impressions": "impressions", "requests": "adRequest", "revenues": "revenue"}

# Abweichungen ab dieser Schwelle (in %) werden markiert
TOLERANCE_PCT = 1.0


def fetch_old(start_date, end_date):
    if not old.API_TOKEN:
        raise RuntimeError("OGURY_API_TOKEN (alter API-Token) fehlt in Umgebung/.env")
    ad_unit_map, _ = old.build_lookup_maps(old.get_details())
    stats_json = old.get_stats(start_date, end_date, groups=["date", "ad_unit"],
                               metrics=list(METRIC_MAP), date_group="day")
    df = old.enrich_with_names(old.flatten_stats(stats_json), ad_unit_map)
    if df.empty:
        return pd.DataFrame(columns=["date", "placement", "placement_id", *METRIC_MAP.values()])
    return pd.DataFrame({
        "date":         pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "placement":    df["ad_unit_name"],
        "placement_id": df["ad_unit"].astype(str),
        **{new_name: pd.to_numeric(df[old_name]) for old_name, new_name in METRIC_MAP.items()},
    })


def fetch_new(start_date, end_date):
    df = new.fetch_pubdaily(start_date, end_date, group_by=["date", "placement"],
                            metrics=list(METRIC_MAP.values()))
    if df.empty:
        return pd.DataFrame(columns=["date", "placement", "placement_id", *METRIC_MAP.values()])
    return pd.DataFrame({
        "date":         pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "placement":    df["placementName"],
        "placement_id": df["placementId"].astype(str),
        **{m: pd.to_numeric(df[m]) for m in METRIC_MAP.values()},
    })


def compare(old_df, new_df, keys):
    metrics = list(METRIC_MAP.values())
    a = old_df.groupby(keys, dropna=False)[metrics].sum()
    b = new_df.groupby(keys, dropna=False)[metrics].sum()
    out = a.join(b, how="outer", lsuffix="_old", rsuffix="_new").fillna(0).reset_index()

    flags = []
    for m in metrics:
        out[f"{m}_diff"] = out[f"{m}_new"] - out[f"{m}_old"]
        out[f"{m}_diff_pct"] = (out[f"{m}_diff"] / out[f"{m}_old"].where(out[f"{m}_old"] != 0) * 100).round(2)
        flags.append(out[f"{m}_diff_pct"].abs().gt(TOLERANCE_PCT)
                     | (out[f"{m}_old"].eq(0) & out[f"{m}_new"].ne(0)))
    out["abweichung"] = pd.concat(flags, axis=1).any(axis=1)
    return out


def main(start_date=None, end_date=None):
    yesterday  = date.today() - timedelta(days=1)
    start_date = start_date or (yesterday - timedelta(days=2)).isoformat()
    end_date   = end_date or yesterday.isoformat()

    print(f"Vergleich alt vs. neu: {start_date} bis {end_date}")
    print("Alte API ...")
    old_df = fetch_old(start_date, end_date)
    print(f"  {len(old_df)} Zeilen")
    print("Neue API ...")
    new_df = fetch_new(start_date, end_date)
    print(f"  {len(new_df)} Zeilen")

    by_date      = compare(old_df, new_df, ["date"])
    # Join über die ID (sind lt. Test 11/2026 in alt und neu identisch); Name nur zur Info
    by_placement = compare(old_df, new_df, ["date", "placement_id"])
    names = pd.concat([new_df, old_df]).drop_duplicates("placement_id").set_index("placement_id")["placement"]
    by_placement.insert(2, "placement", by_placement["placement_id"].map(names))

    # Info: sind die Placement-IDs zwischen alt und neu identisch?
    shared_ids = set(old_df["placement_id"]) & set(new_df["placement_id"])
    print(f"\nPlacement-IDs: alt={old_df['placement_id'].nunique()}, "
          f"neu={new_df['placement_id'].nunique()}, gemeinsam={len(shared_ids)}")

    only_old = by_placement[(by_placement["impressions_new"] == 0) & (by_placement["impressions_old"] > 0)]
    only_new = by_placement[(by_placement["impressions_old"] == 0) & (by_placement["impressions_new"] > 0)]
    if len(only_old) or len(only_new):
        print(f"Placements nur in alt: {only_old['placement_id'].nunique()}, "
              f"nur in neu: {only_new['placement_id'].nunique()}")

    new.OUTPUT_DIR.mkdir(exist_ok=True)
    suffix = f"{start_date}_{end_date}"
    by_date.to_csv(new.OUTPUT_DIR / f"ogury_compare_by_date_{suffix}.csv", index=False)
    by_placement.to_csv(new.OUTPUT_DIR / f"ogury_compare_by_placement_{suffix}.csv", index=False)

    cols = ["date"] + [f"{m}_{s}" for m in METRIC_MAP.values() for s in ("old", "new", "diff_pct")] + ["abweichung"]
    print("\nPro Tag:")
    print(by_date[cols].to_string(index=False))
    n_flagged = int(by_placement["abweichung"].sum())
    print(f"\nPlacement-Zeilen mit Abweichung > {TOLERANCE_PCT}%: {n_flagged} von {len(by_placement)}")
    print(f"CSVs gespeichert in {new.OUTPUT_DIR}")

    return by_date, by_placement


if __name__ == "__main__":
    main(*sys.argv[1:3])
