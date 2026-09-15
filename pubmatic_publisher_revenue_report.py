import os
import sys
import requests
import pandas as pd

# ─────────────────────────────────────────────
# 1. CREDENTIALS (aus Umgebungsvariablen, nicht im Code hardcoden!)
# ─────────────────────────────────────────────
PM_USER     = os.environ["PM_USER"]       # PubMatic-Login-E-Mail
PM_PASSWORD = os.environ["PM_PASSWORD"]   # PubMatic-Passwort
PM_PUB_ID   = os.environ["PM_PUB_ID"]     # numerische Publisher-ID

BASE_URL = "https://api.pubmatic.com"

# ─────────────────────────────────────────────
# 2. AUTHENTIFIZIERUNG
#    POST /v1/developer-integrations/developer/token
#    Token läuft nach einiger Zeit ab -> vor Ablauf refreshen (refreshToken)
# ─────────────────────────────────────────────
token_resp = requests.post(
    f"{BASE_URL}/v1/developer-integrations/developer/token",
    json={
        "userName":   PM_USER,
        "password":   PM_PASSWORD,
        "apiProduct": "PUBLISHER",
    },
    headers={"Content-Type": "application/json"},
)

if token_resp.status_code not in (200, 201):
    raise Exception(f"Auth failed: {token_resp.status_code} {token_resp.text}")

token_data   = token_resp.json()
access_token = token_data["accessToken"]

headers = {"Authorization": f"Bearer {access_token}"}

# ─────────────────────────────────────────────
# 3. ZEITRAUM
#    Format lt. Doku: YYYY-MM-DDTHH:MM
# ─────────────────────────────────────────────
REPORT_FROM = "2026-09-01T00:00"
REPORT_TO   = "2026-09-13T23:59"

# ─────────────────────────────────────────────
# 4. REPORT ABRUFEN
#    GET /v1/analytics/data/publisher/{publisherId}
#    Dimensionen/Metriken lt. "Analytics Standard API"-Referenz
# ─────────────────────────────────────────────
params = {
    "fromDate":   REPORT_FROM,
    "toDate":     REPORT_TO,
    "dateUnit":   "date",
    "dimensions": "date,siteId",
    "metrics":    "revenue,netRevenue,paidImpressions,ecpm,clicks,ctr",
    "pageSize":   40000,
}

report_resp = requests.get(
    f"{BASE_URL}/v1/analytics/data/publisher/{PM_PUB_ID}",
    headers=headers,
    params=params,
)

if report_resp.status_code != 200:
    raise Exception(f"Report failed: {report_resp.status_code} {report_resp.text}")

report_data = report_resp.json()

# ─────────────────────────────────────────────
# 5. FLATTEN & ANZEIGEN
#    Response: {"columns": [...], "rows": [[...], ...], "displayValue": {...}, "currency": "EUR"}
# ─────────────────────────────────────────────
columns = report_data.get("columns", [])
rows    = report_data.get("rows", [])

if not rows:
    print("Keine Zeilen zurückgegeben.")
    sys.exit(0)

df = pd.DataFrame(rows, columns=columns)

# IDs (z.B. siteId) durch Klarnamen ersetzen, falls vorhanden
display_value = report_data.get("displayValue") or {}
for col, mapping in display_value.items():
    if col in df.columns:
        df[f"{col}_name"] = df[col].astype(str).map(mapping)

currency = report_data.get("currency", "")
print(f"{len(df)} Zeilen, Währung: {currency}")
print(df.to_string())
