# SSYoga — Challenge Classes Enrollment Dashboard

Live, interactive Streamlit dashboard for Sri Sri Yoga Challenge Classes
enrollment data (Business Review reporting).

## What it shows
- KPI tiles: Total Enrollments, Unique Subscribers, Resubscribe Rate, Active Subscriptions
- Category mix, per-contact repeat bands, FY period + cumulative, monthly trend,
  Active Subscriptions over time, New vs Repeat subscribers by year
- Sidebar filters: category, financial year, date range, audience
- Inline metric definitions (traceable numbers)

## Data source
`app.py` has a single `DATA_SOURCE` switch at the top:

- `"csv"` — local development, reads the canonical deduped/enriched CSV (not committed).
- `"gsheet"` — production. Reads the Google Sheet your daily job writes to.

**The incoming table must already be deduped, band-fixed and teacher-enriched by the
write-job.** This app only reads — it does a defensive dedup but computes no source-of-truth.

Required columns:
`global_participant_id, global_contact_id, registration_date, registration_date_FY,
event_name_en_gb, course_event_start_date, Repeat pax, VTP_TTP_Status, Is_Teacher_or_VTP_Grad`

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud
1. Set `DATA_SOURCE = "gsheet"` and `GSHEET_URL` in `app.py`.
2. In the app's **Settings → Secrets**, paste the service-account block
   (see `.streamlit/secrets.toml.example`).
3. Share the app's `gcp_service_account` email into the Google Sheet as Viewer.
4. Point Streamlit Cloud at this repo + `app.py`. Deploy.

> Data (CSVs/XLSX) is **git-ignored** — it contains personal data and must never be
> committed. In production the app reads only from the private Google Sheet.
