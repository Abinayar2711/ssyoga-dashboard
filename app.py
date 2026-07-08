"""
SSYoga Challenge Classes — Live Enrollment Dashboard (Streamlit)
================================================================
Interactive, Zoho-style dashboard for Sri Sri Yoga Challenge Classes
enrollment data.

DATA SOURCE
-----------
Right now this reads the local canonical CSV (the deduped, enriched base).
When your daily automation is writing to Google Sheets, flip DATA_SOURCE to
"gsheet" and fill in the two gsheet_* settings below — no other code changes.

IMPORTANT: dedup + band-fix + teacher enrichment should already be done by your
daily write-job BEFORE the data lands here. This app treats the incoming table
as the canonical base and only *reads* it. (See config at top.)

Run locally:
    pip install -r requirements.txt
    streamlit run app.py
"""

import calendar
import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ----------------------------------------------------------------------------
# CONFIG  — the only things you touch when moving from CSV -> Google Sheet
# ----------------------------------------------------------------------------
DATA_SOURCE = "csv"  # "csv" (now) or "gsheet" (once your daily job writes there)

CSV_PATH = "../Sri Sri Yoga — Enrollment Report - Sheet1.csv"

# For DATA_SOURCE == "gsheet":
GSHEET_URL = "https://docs.google.com/spreadsheets/d/1Cr5PoHXtud8aYNYpi-hugGuRLu_RvjB9BS7gX5wgjZY/edit"
GSHEET_WORKSHEET = "Sheet1"

CACHE_TTL_SECONDS = 3600  # re-read the source at most once an hour per viewer

# The 4 in-scope categories, shortest -> longest, with duration in months.
CATEGORY_ORDER = [
    "1 Month Sri Sri Yoga Challenge Classes",
    "3 Month Sri Sri Yoga Challenge Classes",
    "6 Month Sri Sri Yoga Challenge Classes",
    "1 Year Sri Sri Yoga Challenge Classes",
]
CATEGORY_MONTHS = {
    "1 Month Sri Sri Yoga Challenge Classes": 1,
    "3 Month Sri Sri Yoga Challenge Classes": 3,
    "6 Month Sri Sri Yoga Challenge Classes": 6,
    "1 Year Sri Sri Yoga Challenge Classes": 12,
}
CATEGORY_SHORT = {
    "1 Month Sri Sri Yoga Challenge Classes": "1 Month",
    "3 Month Sri Sri Yoga Challenge Classes": "3 Month",
    "6 Month Sri Sri Yoga Challenge Classes": "6 Month",
    "1 Year Sri Sri Yoga Challenge Classes": "1 Year",
}
BAND_ORDER = ["1 - One Time", "2-5", "6-10", "11-15", "15+"]

# Brand-ish palette (calm, high-contrast, works light/dark)
PRIMARY = "#5B8DEF"
ACCENT = "#F2994A"
INK = "#2D3142"
MUTED = "#9AA0AE"
SERIES = ["#5B8DEF", "#27AE60", "#F2994A", "#EB5757"]

st.set_page_config(
    page_title="SSYoga — Enrollment Dashboard",
    page_icon="🧘",
    layout="wide",
)


# ----------------------------------------------------------------------------
# DATA LOADING  (cached)
# ----------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Loading enrollment data…")
def load_data() -> pd.DataFrame:
    if DATA_SOURCE == "gsheet":
        df = _read_gsheet()
    else:
        df = pd.read_csv(CSV_PATH, low_memory=False)

    # ---- canonicalize -------------------------------------------------------
    # Dedup defensively (the write-job should already do this, but a duplicate
    # row must never inflate a count in the dashboard).
    df = df.drop_duplicates(subset="global_participant_id").copy()

    # keep only the 4 in-scope categories
    df = df[df["event_name_en_gb"].isin(CATEGORY_ORDER)].copy()

    df["registration_date"] = pd.to_datetime(df["registration_date"], errors="coerce")
    df["course_event_start_date"] = pd.to_datetime(
        df["course_event_start_date"], errors="coerce"
    )
    df["reg_month"] = df["registration_date"].dt.to_period("M").dt.to_timestamp()
    df["reg_year"] = df["registration_date"].dt.year
    df["category_short"] = df["event_name_en_gb"].map(CATEGORY_SHORT)

    # lifetime first-registration per contact (for New vs Repeat subscriber logic)
    first_reg = df.groupby("global_contact_id")["registration_date"].transform("min")
    df["contact_first_reg"] = first_reg
    df["is_new_this_reg"] = df["registration_date"].eq(first_reg)

    # contact-level lifetime registration count -> lifetime band (1-Time / Repeater)
    life_count = df.groupby("global_contact_id")["global_participant_id"].transform("count")
    df["contact_lifetime_regs"] = life_count
    return df


def _read_gsheet() -> pd.DataFrame:
    """Read the canonical table from Google Sheets.

    Requires: pip install gspread + a service-account JSON in st.secrets.
    Uncomment when your daily job is populating the sheet.
    """
    import gspread  # noqa: local import so CSV mode has no gspread dependency

    gc = gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    sh = gc.open_by_url(GSHEET_URL) if GSHEET_URL.startswith("http") else gc.open_by_key(GSHEET_URL)
    ws = sh.worksheet(GSHEET_WORKSHEET)
    # get_all_values (raw lists) is far faster than get_all_records on a 56k-row
    # sheet — one batch call, header row -> columns, everything else stays string
    # (fine: date parsing and id counts all work on strings).
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame()
    header, *rows = values
    return pd.DataFrame(rows, columns=header)


# ----------------------------------------------------------------------------
# METRIC HELPERS
# ----------------------------------------------------------------------------
def contact_repeat_band(df: pd.DataFrame) -> pd.DataFrame:
    """Repeat band PER CONTACT (dedup to contacts first — the row-level
    'Repeat pax' column labels every row of a contact, so counting rows
    over-counts). Returns a band->contacts frame in fixed order."""
    counts = df.groupby("global_contact_id")["global_participant_id"].count()

    def band(n):
        if n <= 1:
            return "1 - One Time"
        if n <= 5:
            return "2-5"
        if n <= 10:
            return "6-10"
        if n <= 15:
            return "11-15"
        return "15+"

    b = counts.map(band).value_counts()
    return b.reindex(BAND_ORDER, fill_value=0)


def resubscribe_rate(df: pd.DataFrame) -> float:
    """Per-contact: contacts with 2+ regs / total contacts."""
    counts = df.groupby("global_contact_id")["global_participant_id"].count()
    total = len(counts)
    if total == 0:
        return 0.0
    return (counts >= 2).sum() / total


def active_subscriptions_by_month(df: pd.DataFrame) -> pd.Series:
    """Active subscriptions per calendar month.

    A registration is 'active' in month M if
    [course_event_start_date, start_date + duration_months] overlaps M.
    Clipped at the current month (no future projection). Cohort-based:
    course_event_start_date is a shared batch start, per the data model.
    """
    d = df.dropna(subset=["course_event_start_date"]).copy()
    d["months"] = d["event_name_en_gb"].map(CATEGORY_MONTHS)
    d["end_date"] = d.apply(
        lambda r: r["course_event_start_date"] + pd.DateOffset(months=int(r["months"])),
        axis=1,
    )
    if d.empty:
        return pd.Series(dtype=int)

    start = d["course_event_start_date"].min().to_period("M").to_timestamp()
    today = pd.Timestamp.today().to_period("M").to_timestamp()
    months = pd.date_range(start, today, freq="MS")

    out = {}
    for m in months:
        m_end = m + pd.offsets.MonthEnd(0)
        active = (d["course_event_start_date"] <= m_end) & (d["end_date"] >= m)
        out[m] = int(active.sum())
    return pd.Series(out)


def fmt(n) -> str:
    return f"{int(n):,}"


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
df_all = load_data()

st.title("🧘 Sri Sri Yoga — Challenge Classes Enrollment Dashboard")
st.caption(
    f"Live view • source: **{DATA_SOURCE.upper()}** • "
    f"canonical base after dedup & category filter"
)

# ---- Sidebar filters --------------------------------------------------------
st.sidebar.header("Filters")

cats = st.sidebar.multiselect(
    "Category",
    options=CATEGORY_ORDER,
    default=CATEGORY_ORDER,
    format_func=lambda c: CATEGORY_SHORT[c],
)

fy_opts = sorted(df_all["registration_date_FY"].dropna().unique().tolist())
fys = st.sidebar.multiselect("Financial Year", options=fy_opts, default=fy_opts)

min_d = df_all["registration_date"].min().date()
max_d = df_all["registration_date"].max().date()
date_range = st.sidebar.date_input(
    "Registration date range",
    value=(min_d, max_d),
    min_value=min_d,
    max_value=max_d,
)

audience = st.sidebar.radio(
    "Audience",
    options=["All", "Pure subscribers only", "Teachers / VTP-TTP grads only"],
    index=0,
    help="Teacher/VTP status comes from the enrichment columns in the base table.",
)

# ---- Apply filters ----------------------------------------------------------
df = df_all[df_all["event_name_en_gb"].isin(cats)]
if fys:
    df = df[df["registration_date_FY"].isin(fys)]
if isinstance(date_range, tuple) and len(date_range) == 2:
    lo, hi = date_range
    df = df[(df["registration_date"].dt.date >= lo) & (df["registration_date"].dt.date <= hi)]

if audience != "All" and "Is_Teacher_or_VTP_Grad" in df.columns:
    if audience == "Teachers / VTP-TTP grads only":
        df = df[df["Is_Teacher_or_VTP_Grad"] == "Yes"]
    else:
        df = df[df["Is_Teacher_or_VTP_Grad"] != "Yes"]

df = df.copy()

if df.empty:
    st.warning("No rows match the current filters.")
    st.stop()

# ---- KPI tiles --------------------------------------------------------------
total_enroll = len(df)
unique_contacts = df["global_contact_id"].nunique()
resub = resubscribe_rate(df)
act = active_subscriptions_by_month(df)
current_active = int(act.iloc[-1]) if len(act) else 0

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total Enrollments", fmt(total_enroll), help="Each registration = 1 enrollment (repeats included).")
k2.metric("Unique Subscribers", fmt(unique_contacts), help="Distinct global_contact_id.")
k3.metric("Resubscribe Rate", f"{resub*100:.1f}%", help="Contacts with 2+ registrations ÷ total contacts (per-contact).")
k4.metric("Active Subscriptions (now)", fmt(current_active), help="Registrations whose cohort window overlaps the current month.")

with st.expander("ℹ️  Metric definitions (so the numbers are traceable)"):
    st.markdown(
        """
- **Total Enrollments** — one row per registration; repeat sign-ups counted. Unit = registrations.
- **Unique Subscribers** — distinct `global_contact_id`. Unit = people.
- **Resubscribe Rate** — *per contact*: (contacts with 2+ registrations) ÷ (total contacts). Not per-registration.
- **New Subscriber (period)** — contact whose *earliest-ever* registration falls in that period.
- **Repeat Subscriber (period)** — active in the period but first registered *before* it.
- **1-Time vs Repeater** — *lifetime* label: 1 registration ever vs 2+ ever (fixed per contact, not period-dependent).
- **Active Subscriptions** — cohort window `[course_event_start_date, +duration]` overlaps the month.
  *Not* "MAU": there is no login/attendance signal in this data — only registration events.
        """
    )

st.divider()

# ---- Row 1: category bar + repeat band -------------------------------------
c1, c2 = st.columns(2)

with c1:
    st.subheader("Enrollments by Category")
    cat_counts = (
        df["category_short"]
        .value_counts()
        .reindex([CATEGORY_SHORT[c] for c in CATEGORY_ORDER if CATEGORY_SHORT[c] in df["category_short"].values])
    )
    fig = go.Figure(go.Bar(
        x=cat_counts.index, y=cat_counts.values,
        marker_color=PRIMARY, text=[fmt(v) for v in cat_counts.values],
        textposition="outside",
    ))
    fig.update_layout(margin=dict(t=10, b=10), height=340, yaxis_title="Enrollments")
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("Subscribers by Repeat Band")
    st.caption("Counted **per contact** (not per row).")
    band = contact_repeat_band(df)
    fig = go.Figure(go.Bar(
        x=band.index, y=band.values,
        marker_color=ACCENT, text=[fmt(v) for v in band.values],
        textposition="outside",
    ))
    fig.update_layout(margin=dict(t=10, b=10), height=340, yaxis_title="Contacts")
    st.plotly_chart(fig, use_container_width=True)

# ---- Row 2: FY period + cumulative -----------------------------------------
st.subheader("Enrollments by Financial Year")
fy_counts = df.groupby("registration_date_FY").size().sort_index()
fy_cum = fy_counts.cumsum()

fig = go.Figure()
fig.add_bar(x=fy_counts.index, y=fy_counts.values, name="Per FY",
            marker_color=PRIMARY, text=[fmt(v) for v in fy_counts.values], textposition="outside")
fig.add_scatter(x=fy_cum.index, y=fy_cum.values, name="Cumulative",
                mode="lines+markers", line=dict(color=ACCENT, width=3), yaxis="y2")
fig.update_layout(
    height=360, margin=dict(t=10, b=10),
    yaxis=dict(title="Enrollments per FY"),
    yaxis2=dict(title="Cumulative", overlaying="y", side="right", showgrid=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
)
st.plotly_chart(fig, use_container_width=True)

# ---- Row 3: monthly trend + active subscriptions ---------------------------
st.subheader("Monthly Enrollment Trend")
monthly = df.groupby("reg_month").size()
fig = go.Figure(go.Scatter(
    x=monthly.index, y=monthly.values, mode="lines", fill="tozeroy",
    line=dict(color=PRIMARY, width=2),
))
fig.update_layout(height=320, margin=dict(t=10, b=10), yaxis_title="Enrollments")
st.plotly_chart(fig, use_container_width=True)

st.subheader("Active Subscriptions Over Time")
st.caption("Cohort windows overlapping each month, clipped at the current month (no future projection).")
fig = go.Figure(go.Scatter(
    x=act.index, y=act.values, mode="lines", fill="tozeroy",
    line=dict(color="#27AE60", width=2),
))
fig.update_layout(height=320, margin=dict(t=10, b=10), yaxis_title="Active subscriptions")
st.plotly_chart(fig, use_container_width=True)

# ---- New vs Repeat subscribers by year -------------------------------------
st.subheader("New vs Repeat Subscribers by Year")
st.caption("New = first-ever registration in that year. Repeat = active that year, first registered earlier.")
rows = []
for yr in sorted(df["reg_year"].dropna().unique()):
    yr = int(yr)
    in_yr = df[df["reg_year"] == yr]
    contacts_in_yr = in_yr["global_contact_id"].unique()
    # new = contact's lifetime-first reg is in this year (computed on full base, not filtered)
    firsts = df_all.set_index("global_contact_id")["contact_first_reg"]
    sub = df_all[df_all["global_contact_id"].isin(contacts_in_yr)]
    new_ct = sub[sub["contact_first_reg"].dt.year == yr]["global_contact_id"].nunique()
    total_ct = len(contacts_in_yr)
    rows.append({"Year": yr, "New": new_ct, "Repeat": total_ct - new_ct, "Total active": total_ct})
nr = pd.DataFrame(rows)

fig = go.Figure()
fig.add_bar(x=nr["Year"], y=nr["New"], name="New", marker_color=PRIMARY)
fig.add_bar(x=nr["Year"], y=nr["Repeat"], name="Repeat", marker_color=ACCENT)
fig.update_layout(barmode="stack", height=340, margin=dict(t=10, b=10),
                  legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                  yaxis_title="Contacts")
st.plotly_chart(fig, use_container_width=True)

st.divider()
with st.expander("📄  Underlying data (filtered)"):
    st.dataframe(
        df[[
            "global_contact_id", "global_participant_id", "registration_date",
            "registration_date_FY", "category_short", "Repeat pax",
            "VTP_TTP_Status",
        ]].head(1000),
        use_container_width=True,
    )
    st.caption("Showing first 1,000 filtered rows.")
