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
# "csv" locally (default), "gsheet" in the cloud. Set via secrets so the SAME
# commit works both places — locally there's no secrets file so it falls to csv;
# on Streamlit Cloud add  data_source = "gsheet"  to Secrets.
try:
    DATA_SOURCE = st.secrets.get("data_source", "csv")
    # A bare key pasted AFTER a [table] header gets absorbed into that table, so
    # `data_source` under [auth] reads as auth.data_source and we silently fall
    # back to csv. That looks like "no secrets set" but is really "wrong order" --
    # detect it explicitly rather than letting it present as a missing-file error.
    if DATA_SOURCE == "csv":
        for _table in ("auth", "gcp_service_account"):
            _misplaced = st.secrets.get(_table, {})
            if hasattr(_misplaced, "get") and _misplaced.get("data_source"):
                DATA_SOURCE = _misplaced["data_source"]
                st.warning(
                    f"`data_source` was found inside the `[{_table}]` section of Secrets. "
                    "In TOML a bare key belongs to the table above it — move "
                    "`data_source`, `gsheet_url` and `gsheet_worksheet` to the very top "
                    "of the Secrets box, above every `[section]` header.",
                    icon="⚠️",
                )
                break
except Exception:
    DATA_SOURCE = "csv"

CSV_PATH = "../Sri Sri Yoga — Enrollment Report - Sheet1.csv"

# For DATA_SOURCE == "gsheet". Read from secrets so pointing the app at a new
# Sheet is a secrets edit, not a commit + redeploy. The literals below are only
# a local-dev fallback.
def _secret(key: str, default: str = "") -> str:
    """Read a top-level secret, tolerating the common paste-order mistake.

    If the key was pasted below a [table] header it lives inside that table; look
    there too so a misordered Secrets box degrades to a warning, not a failure.
    """
    try:
        if key in st.secrets:
            return st.secrets[key]
        for table in ("auth", "gcp_service_account"):
            section = st.secrets.get(table, {})
            if hasattr(section, "get") and section.get(key):
                return section[key]
    except Exception:
        pass
    return default


GSHEET_URL = _secret("gsheet_url")
GSHEET_WORKSHEET = _secret("gsheet_worksheet", "Sheet1")

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

# Google sign-in gate — must run before anything renders data.
from auth_gate import require_login, sidebar_account  # noqa: E402

require_login("SSYoga — Enrollment Dashboard")
sidebar_account()


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

    # contact's lifetime-first FINANCIAL YEAR (Indian FY, Apr-Mar), so the
    # New/Repeat breakdown can be done by FY as well as calendar year.
    fm = first_reg.dt.month
    fy_start = first_reg.dt.year.where(fm >= 4, first_reg.dt.year - 1)
    df["contact_first_fy"] = fy_start.astype("Int64").astype(str) + "-" + (fy_start + 1).astype("Int64").astype(str).str[-2:]
    df["contact_first_year"] = first_reg.dt.year

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

    # Fail loudly and specifically: a missing URL or key otherwise surfaces as an
    # opaque gspread error that looks like a permissions problem.
    if not GSHEET_URL:
        st.error(
            "`data_source` is \"gsheet\" but no `gsheet_url` is set in Secrets. "
            "Add the full Google Sheet URL and reboot the app."
        )
        st.stop()
    if "gcp_service_account" not in st.secrets:
        st.error(
            "`data_source` is \"gsheet\" but there is no `[gcp_service_account]` block "
            "in Secrets. Paste the service-account JSON, and share the Sheet with its "
            "`client_email` as Viewer."
        )
        st.stop()

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


def _with_coverage_window(df: pd.DataFrame) -> pd.DataFrame:
    """Attach each registration's coverage window [cover_start, cover_end).

    Both ends are normalised to midnight. Cohort starts carry a time component
    (usually 06:00), so an un-normalised end never lands exactly on a month
    boundary -- a 1-Month plan starting 1 Jul 06:00 ends 1 Aug 06:00, which a
    naive `end >= start_of_month` test scores as active in August too. Dropping
    the time and treating cover_end as EXCLUSIVE makes a 1-Month plan occupy one
    month, not two, while a mid-month plan (16% of rows) still correctly spans
    the two months it genuinely runs across.
    """
    d = df.dropna(subset=["course_event_start_date"]).copy()
    if d.empty:
        return d
    d["months"] = d["event_name_en_gb"].map(CATEGORY_MONTHS)
    d["cover_start"] = d["course_event_start_date"].dt.normalize()
    d["cover_end"] = [
        s + pd.DateOffset(months=int(m))
        for s, m in zip(d["cover_start"], d["months"])
    ]
    return d


def _active_mask(d: pd.DataFrame, m_start: pd.Timestamp, m_end: pd.Timestamp):
    """Rows whose coverage window overlaps the month [m_start, m_end].

    cover_end is exclusive, hence the strict `>` -- a plan ending on the 1st is
    not active in that month.
    """
    return (d["cover_start"] <= m_end) & (d["cover_end"] > m_start)


def active_subscriptions_by_month(df: pd.DataFrame) -> pd.Series:
    """Active subscriptions per calendar month.

    A registration is 'active' in month M if
    [course_event_start_date, start_date + duration_months] overlaps M.
    Clipped at the current month (no future projection). Cohort-based:
    course_event_start_date is a shared batch start, per the data model.
    """
    d = _with_coverage_window(df)
    if d.empty:
        return pd.Series(dtype=int)

    start = d["cover_start"].min().to_period("M").to_timestamp()
    today = pd.Timestamp.today().to_period("M").to_timestamp()
    months = pd.date_range(start, today, freq="MS")

    out = {}
    for m in months:
        m_end = m + pd.offsets.MonthEnd(0)
        out[m] = int(_active_mask(d, m, m_end).sum())
    return pd.Series(out)


def people_served_between(df: pd.DataFrame, w_start: pd.Timestamp, w_end: pd.Timestamp) -> int:
    """Distinct people whose plan was running at ANY point in [w_start, w_end].

    Each person counts ONCE however many months they were covered for, which is
    what makes this the only honest way to put a single number on a year. The
    monthly series cannot be summed for that purpose -- someone on a 12-month
    plan appears in all 12 monthly counts, so the sum overstates by ~5x.

    Equivalent to the union of the monthly active sets, computed in one pass:
    a window overlaps the range iff it starts before the range ends and ends
    after the range starts (cover_end exclusive, as everywhere else).
    """
    d = _with_coverage_window(df)
    if d.empty:
        return 0
    overlaps = (d["cover_start"] <= w_end) & (d["cover_end"] > w_start)
    return int(d.loc[overlaps, "global_contact_id"].nunique())


def active_people_in_month(df: pd.DataFrame, month: pd.Timestamp) -> tuple[int, int]:
    """(distinct contacts, contacts holding 2+ plans) active in `month`.

    The people count is lower than the subscription count because a contact can
    hold several overlapping plans (e.g. a 1-Year and a 1-Month running
    together). Note the two are not interchangeable: a contact with 3 plans
    adds 2 to the subscription-vs-people gap but is only one person.
    """
    d = _with_coverage_window(df)
    if d.empty:
        return 0, 0
    m_end = month + pd.offsets.MonthEnd(0)
    per_contact = d.loc[_active_mask(d, month, m_end)].groupby("global_contact_id").size()
    return int(len(per_contact)), int((per_contact >= 2).sum())


def fmt(n) -> str:
    return f"{int(n):,}"


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
try:
    df_all = load_data()
except FileNotFoundError:
    st.error(
        "**No data source configured.**\n\n"
        f"The app is running in **{DATA_SOURCE.upper()}** mode but couldn't find the data.\n\n"
        "On Streamlit Cloud there is no local CSV (by design — the CSV holds personal "
        "data and is never uploaded). To connect the Google Sheet, open **Manage app → "
        "Settings → Secrets** and add:\n\n"
        "```toml\n"
        "data_source = \"gsheet\"\n\n"
        "[gcp_service_account]\n"
        "# ...full contents of your service-account JSON...\n"
        "```\n\n"
        "Then share the Sheet with the service account's `client_email` (Viewer)."
    )
    st.stop()
except Exception as e:  # gspread auth / permission / worksheet errors
    st.error(
        "**Could not read the Google Sheet.**\n\n"
        f"`{type(e).__name__}: {e}`\n\n"
        "Common causes: the Sheet isn't shared with the service account's "
        "`client_email` (share it as Viewer), the `gcp_service_account` secret is "
        "malformed, or the Sheets/Drive API isn't enabled on the Cloud project."
    )
    st.stop()

st.title("🧘 Sri Sri Yoga — Challenge Classes Enrollment Dashboard")
st.caption(
    f"Live view • source: **{DATA_SOURCE.upper()}** • "
    f"canonical base after dedup & category filter"
)

# ---- Sidebar filters --------------------------------------------------------
st.sidebar.header("Filters")

fy_opts = sorted(df_all["registration_date_FY"].dropna().unique().tolist())
min_d = df_all["registration_date"].min().date()
max_d = df_all["registration_date"].max().date()

# Reset button — clears every filter back to default (also rescues the date
# picker if it gets stuck mid-selection, which Streamlit shows as a red box).
_FILTER_KEYS = ("flt_cat", "flt_fy", "flt_dates", "flt_aud")


def _reset_filters():
    for _k in _FILTER_KEYS:
        st.session_state.pop(_k, None)


st.sidebar.button("↺  Reset all filters", on_click=_reset_filters,
                  use_container_width=True)

cats = st.sidebar.multiselect(
    "Category",
    options=CATEGORY_ORDER,
    default=CATEGORY_ORDER,
    format_func=lambda c: CATEGORY_SHORT[c],
    key="flt_cat",
)

fys = st.sidebar.multiselect("Financial Year", options=fy_opts, default=fy_opts,
                             key="flt_fy")

date_range = st.sidebar.date_input(
    "Registration date range",
    value=(min_d, max_d),
    min_value=min_d,
    max_value=max_d,
    key="flt_dates",
)
st.sidebar.caption("Pick a start **and** end date. Stuck on red? Hit **Reset all filters**.")

audience = st.sidebar.radio(
    "Audience",
    options=["All", "Pure subscribers only", "Teachers / VTP-TTP grads only"],
    index=0,
    help="Teacher/VTP status comes from the enrichment columns in the base table.",
    key="flt_aud",
)

# Normalize the date range once — a mid-selection returns a 1-tuple, which we
# treat as the full span (no filtering) so nothing downstream crashes on [1].
if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    date_lo, date_hi = date_range
else:
    date_lo, date_hi = min_d, max_d

# ---- Apply filters ----------------------------------------------------------
df = df_all[df_all["event_name_en_gb"].isin(cats)]
if fys:
    df = df[df["registration_date_FY"].isin(fys)]
df = df[(df["registration_date"].dt.date >= date_lo) & (df["registration_date"].dt.date <= date_hi)]

if audience != "All" and "Is_Teacher_or_VTP_Grad" in df.columns:
    if audience == "Teachers / VTP-TTP grads only":
        df = df[df["Is_Teacher_or_VTP_Grad"] == "Yes"]
    else:
        df = df[df["Is_Teacher_or_VTP_Grad"] != "Yes"]

df = df.copy()

if df.empty:
    st.warning("No rows match the current filters.")
    st.stop()

# ---- Filter-scope awareness (so labels never lie) --------------------------
default_cats = set(cats) == set(CATEGORY_ORDER)
fy_filter_active = bool(fys) and set(fys) != set(fy_opts)
default_dates = (date_lo == min_d and date_hi == max_d)
default_audience = audience == "All"
any_filter = not (default_cats and not fy_filter_active and default_dates and default_audience)

scope_bits = []
if not default_cats:
    scope_bits.append("categories: " + ", ".join(CATEGORY_SHORT[c] for c in cats))
if fy_filter_active:
    scope_bits.append("FY: " + ", ".join(fys))
if not default_dates:
    scope_bits.append(f"dates: {date_lo} → {date_hi}")
if not default_audience:
    scope_bits.append(f"audience: {audience}")

scope_suffix = "all-time" if not any_filter else "within filter"
if any_filter:
    st.info("🔎 **Filtered view** — " + "; ".join(scope_bits) +
            ". All numbers below reflect only this subset. Clear the sidebar filters for the full dataset.")
else:
    st.success("Showing the **full dataset** — no filters applied.")

# ---- Shared computations ----------------------------------------------------
total_enroll = len(df)
unique_contacts = df["global_contact_id"].nunique()
resub = resubscribe_rate(df)

# Active Subscriptions uses the WHOLE pool — Category + Audience filters apply,
# but NOT the registration-date/FY filter. "Active in month M" is about the
# coverage window, not when the person registered, so a plan bought in an
# earlier FY that is still active must still be counted. The FY/date selection
# instead zooms the trend and drives the peak / period-end numbers (below).
df_active = df_all[df_all["event_name_en_gb"].isin(cats)].copy()
if audience != "All" and "Is_Teacher_or_VTP_Grad" in df_active.columns:
    if audience == "Teachers / VTP-TTP grads only":
        df_active = df_active[df_active["Is_Teacher_or_VTP_Grad"] == "Yes"]
    else:
        df_active = df_active[df_active["Is_Teacher_or_VTP_Grad"] != "Yes"]
act = active_subscriptions_by_month(df_active)
current_active = int(act.iloc[-1]) if len(act) else 0
current_active_people, current_active_multi = (
    active_people_in_month(df_active, act.index.max()) if len(act) else (0, 0)
)


def numbers_table(df_str):
    """Render a compact, comma-formatted numbers table (index hidden)."""
    st.dataframe(df_str, use_container_width=True, hide_index=True)


# ---- KPI tiles (at-a-glance) -----------------------------------------------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Total Enrollments", fmt(total_enroll), help="Each registration = 1 enrollment (repeats included).")
k2.metric("Unique Subscribers", fmt(unique_contacts), help="Distinct global_contact_id.")
k3.metric("Active Subscriptions (now)", fmt(current_active), help="Registrations whose cohort window overlaps the current month.")
k3.caption(f"held by {fmt(current_active_people)} people")
k4.metric("Resubscribe Rate", f"{resub*100:.1f}%", help="Contacts with 2+ registrations ÷ total contacts (per-contact).")

with st.expander("ℹ️  Metric definitions (so the numbers are traceable)"):
    st.markdown(
        """
- **Total Enrollments** — one row per registration; repeat sign-ups counted. Unit = registrations.
- **Unique Subscribers** — distinct `global_contact_id`. Unit = people.
- **New Subscriber (period)** — contact whose *earliest-ever* registration falls in that period.
- **Repeat Subscriber (period)** — active in the period but first registered *before* it.
- **1-Time vs Repeater** — *lifetime* label: 1 registration ever vs 2+ ever (fixed per contact, not period-dependent).
- **Active Subscriptions** — cohort window `[course_event_start_date, +duration]` overlaps the month.
  *Not* "MAU": there is no login/attendance signal in this data — only registration events.
- **Resubscribe Rate** — *per contact*: (contacts with 2+ registrations) ÷ (total contacts). Not per-registration.
        """
    )

# ============================================================================
# 1 · CATEGORIES
# ============================================================================
st.divider()
st.header("1 · Categories")
cat_counts = (
    df["category_short"]
    .value_counts()
    .reindex([CATEGORY_SHORT[c] for c in CATEGORY_ORDER if CATEGORY_SHORT[c] in df["category_short"].values])
)
cc1, cc2 = st.columns([1, 2])
with cc1:
    numbers_table(pd.DataFrame({
        "Category": cat_counts.index,
        "Enrollments": [fmt(v) for v in cat_counts.values],
    }))
with cc2:
    fig = go.Figure(go.Bar(
        x=cat_counts.index, y=cat_counts.values,
        marker_color=PRIMARY, text=[fmt(v) for v in cat_counts.values], textposition="outside",
    ))
    fig.update_layout(margin=dict(t=10, b=10), height=320, yaxis_title="Enrollments")
    st.plotly_chart(fig, use_container_width=True)

# ============================================================================
# 2 · ENROLLMENTS  (Calendar Year · Financial Year · Monthly)
# ============================================================================
st.divider()
st.header("2 · Enrollments")
st.caption("Each registration = 1 enrollment (repeat sign-ups included).")

# --- Calendar Year ---
st.subheader("By Calendar Year")
cy_counts = df.groupby("reg_year").size().sort_index()
cy_counts.index = cy_counts.index.astype(int)
e1, e2 = st.columns([1, 2])
with e1:
    numbers_table(pd.DataFrame({
        "Calendar Year": cy_counts.index.astype(str),
        "Enrollments": [fmt(v) for v in cy_counts.values],
    }))
with e2:
    fig = go.Figure(go.Bar(
        x=cy_counts.index.astype(str), y=cy_counts.values,
        marker_color=PRIMARY, text=[fmt(v) for v in cy_counts.values], textposition="outside",
    ))
    fig.update_layout(margin=dict(t=10, b=10), height=300, yaxis_title="Enrollments")
    st.plotly_chart(fig, use_container_width=True)

# --- Financial Year (period + cumulative) ---
st.subheader("By Financial Year")
fy_counts = df.groupby("registration_date_FY").size().sort_index()
fy_cum = fy_counts.cumsum()
f1, f2 = st.columns([1, 2])
with f1:
    numbers_table(pd.DataFrame({
        "FY": fy_counts.index,
        "Enrollments": [fmt(v) for v in fy_counts.values],
        "Cumulative": [fmt(v) for v in fy_cum.values],
    }))
with f2:
    fig = go.Figure()
    fig.add_bar(x=fy_counts.index, y=fy_counts.values, name="Per FY",
                marker_color=PRIMARY, text=[fmt(v) for v in fy_counts.values], textposition="outside")
    fig.add_scatter(x=fy_cum.index, y=fy_cum.values, name="Cumulative",
                    mode="lines+markers", line=dict(color=ACCENT, width=3), yaxis="y2")
    fig.update_layout(
        height=320, margin=dict(t=10, b=10),
        yaxis=dict(title="Per FY"),
        yaxis2=dict(title="Cumulative", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    st.plotly_chart(fig, use_container_width=True)

# --- Monthly ---
st.subheader("Monthly Trend")
monthly = df.groupby("reg_month").size()
fig = go.Figure(go.Scatter(
    x=monthly.index, y=monthly.values, mode="lines", fill="tozeroy",
    line=dict(color=PRIMARY, width=2),
))
fig.update_layout(height=300, margin=dict(t=10, b=10), yaxis_title="Enrollments")
st.plotly_chart(fig, use_container_width=True)

# ============================================================================
# 3 · SUBSCRIBERS  (people — New vs Returning, with the people->enrollments bridge)
# ============================================================================
st.divider()
st.header("3 · Subscribers")
st.caption("Counted per **unique person**, not per registration. A *subscriber* = "
           "anyone who subscribed — **New and Returning both count**.")
_anchor = "FY" if fy_filter_active else "calendar year"
st.markdown(
    "<div style='font-size:0.8rem; color:#5B6472; background:rgba(91,141,239,0.08); "
    "border-left:3px solid #5B8DEF; padding:6px 10px; border-radius:4px; margin:2px 0 8px;'>"
    f"<b>Rule of thumb</b> — everything below is anchored on a person's <b>first-ever {_anchor}</b>: "
    f"viewed {_anchor} = first-ever → 🟦 <b>New</b> · earlier first-ever → 🟧 <b>Returning</b> · "
    f"2+ entries in the same {_anchor} → 🔁 <b>Renewal</b> (an extra enrollment, same person)."
    "</div>",
    unsafe_allow_html=True,
)

# --- Headline: how many people subscribed, split New vs Returning ------------
# New       = their FIRST-EVER subscription falls within this view.
# Returning = they subscribed BEFORE this view's start and came back.
scope_word = "all-time" if not any_filter else "in this view"
contacts_scope = df["global_contact_id"].unique()
first_reg_scope = (
    df_all.groupby("global_contact_id")["contact_first_reg"].first().reindex(contacts_scope)
)
scope_start = df["registration_date"].min()
returning_n = int((first_reg_scope < scope_start).sum())
new_n = len(contacts_scope) - returning_n
renew_extra = total_enroll - unique_contacts
# how many *people* renewed (2+ registrations within this view) — distinct from
# renew_extra, which is the count of extra enrollments those people generated.
_scope_cnt = df.groupby("global_contact_id")["global_participant_id"].count()
renewers_n = int((_scope_cnt >= 2).sum())

st.markdown(f"### {unique_contacts:,} people subscribed &nbsp;·&nbsp; _{scope_word}_")

# Two honest rulers side by side: PEOPLE (New/Returning) vs ENROLLMENTS (incl.
# renewals). Different totals make it obvious they measure different things, so
# nobody sums renewal into the New/Returning people-split.
box_people, box_enroll = st.columns(2)
with box_people:
    with st.container(border=True):
        st.markdown(f"#### 👥 People — {unique_contacts:,}")
        p1, p2 = st.columns(2)
        p1.metric("🟦 New", fmt(new_n),
                  help="Never subscribed before this view — very first time.")
        p2.metric("🟧 Returning", fmt(returning_n),
                  help="Had already subscribed earlier and came back.")
        if returning_n == 0 and not fy_filter_active and default_dates:
            st.caption("↳ **Returning is 0** because the view covers the **whole timeline** "
                       "(nothing comes before the start). **Pick one FY/year** on the left to "
                       "see returning subscribers — or use the by-year table below.")
        else:
            st.caption("New + Returning = every person, counted **once**.")
with box_enroll:
    with st.container(border=True):
        st.markdown(f"#### 🧾 Enrollments — {total_enroll:,}")
        e1, e2 = st.columns(2)
        e1.metric("First sign-up", fmt(unique_contacts),
                  help="One enrollment per person (their first in this view).")
        e2.metric("🔁 Renewals (extra)", fmt(renew_extra),
                  help="Extra sign-ups by people who registered more than once.")
        st.caption(f"🔁 by **{renewers_n:,} people** who renewed.")

st.caption(
    f"↔️ **Same {unique_contacts:,} people**, two rulers. The Enrollments box counts their "
    f"sign-ups, and **{renew_extra:,}** of those are renewals"
    + (" within this view" if any_filter else " over time")
    + ". People ≠ transactions — that's why the two totals differ. "
    "🔁 Renewal cuts across **both** New and Returning (it's not part of the People split)."
)

# Click-to-open renewal drill-down: how many times renewers signed up (spots bursts)
with st.expander(f"🔁 Renewal detail — how many times people signed up ({renewers_n:,} renewers)"):
    st.caption(
        "Only people who signed up **2+ times** in this view. "
        "“2” = signed up twice (renewed once) … “6+” = six or more. "
        "A tall bar on the right flags an unusual burst that period."
    )
    _ren = _scope_cnt[_scope_cnt >= 2]

    def _renew_band(n):
        return "2" if n == 2 else "3" if n == 3 else "4" if n == 4 else "5" if n == 5 else "6+"

    _dist = _ren.map(_renew_band).value_counts().reindex(["2", "3", "4", "5", "6+"], fill_value=0)
    rd1, rd2 = st.columns([1, 2])
    with rd1:
        numbers_table(pd.DataFrame({
            "Sign-ups": _dist.index,
            "People": [fmt(v) for v in _dist.values],
        }))
        st.caption(f"Most by one person: **{int(_scope_cnt.max()):,}** sign-ups.")
    with rd2:
        fig = go.Figure(go.Bar(
            x=_dist.index, y=_dist.values, marker_color=ACCENT,
            text=[fmt(v) for v in _dist.values], textposition="outside",
        ))
        fig.update_layout(margin=dict(t=10, b=10), height=280,
                          yaxis_title="People", xaxis_title="Times signed up in this view")
        st.plotly_chart(fig, use_container_width=True)

# --- New vs Returning by period (FY when an FY filter is on, else cal. year) --
# Match the breakdown unit to the filter unit, so an FY filter isn't sliced into
# calendar-year rows that straddle the FY boundary.
if fy_filter_active:
    period_col, first_period_col, period_label = "registration_date_FY", "contact_first_fy", "FY"
else:
    period_col, first_period_col, period_label = "reg_year", "contact_first_year", "Year"

st.subheader(f"New vs Returning Subscribers by {period_label}")
p = period_label.lower()
st.caption(
    f"**New** = had **never subscribed in any earlier {p}** — this {p} is their very first time. "
    f"(If they'd subscribed in a previous {p}, they'd be counted as Returning, not New.) "
    f"**Returning** = **already subscribed in an earlier {p}** and came back this {p} "
    f"— which is *different* from renewing more than once inside the same {p}. "
    + ("Split by **FY** to match your FY filter." if fy_filter_active
       else "Split by **calendar year**.")
)

with st.expander("❓ How New vs Returning works (worked example — read this if the numbers seem odd)"):
    st.markdown(
        """
There are **three different things** that sound similar. For any selected year:

| Concept | Means | Counted as |
|---|---|---|
| 🟦 **New** | Never subscribed before this year — **first time ever** | a person |
| 🟧 **Returning** | Subscribed in an **earlier year**, came back this year | a person |
| 🔁 **Renewal (same year)** | Subscribed **2+ times inside the same year** | an extra *enrollment* |

**Renewal is a *separate axis* — it cuts across BOTH New and Returning.** It's not tied to
being New: a renewal = anyone with 2+ registrations *inside* the same year, whether they're
New or Returning that year. New/Returning splits the **people**; renewal lives in the **gap**
between people and enrollments *(e.g. FY2025-26: 29,845 people but 33,492 enrollments — 2,743
of those people renewed within the year, 2,599 New + 144 Returning)*.

The status is judged **per year** — the *same person* can be New one year and Returning the next.

**Example — "Bala" subscribes in FY2025-26, then again in FY2026-27:**

| Year viewed | Bala counts as | Why |
|---|---|---|
| FY**2025-26** | 🟦 **New** | never subscribed before → first time |
| FY**2026-27** | 🟧 **Returning** | had already subscribed (in 2025-26) |

So a New person **cannot** have subscribed earlier — the moment they had, they'd be Returning.
Their *future* registrations don't change this year's label. And if Bala subscribed **twice within
2025-26**, that second one is a 🔁 **renewal** — it shows up in the *enrollments* count (People ≠
transactions), not as a second person.

*The one label that never changes* is **lifetime loyalty** (1-Time vs Repeater, in the expander
below) — that's fixed per person across all history, independent of the year you pick.
        """
    )

first_period = df_all.groupby("global_contact_id")[first_period_col].first()
rows = []
for p in sorted(df[period_col].dropna().unique()):
    contacts_in_p = df[df[period_col] == p]["global_contact_id"].unique()
    fp = first_period.reindex(contacts_in_p)
    new_ct = int((fp == p).sum())
    total_ct = len(contacts_in_p)
    rows.append({"Period": str(int(p)) if period_label == "Year" else str(p),
                 "New": new_ct, "Returning": total_ct - new_ct, "Total people": total_ct})
nr = pd.DataFrame(rows)
n1, n2 = st.columns([1, 2])
with n1:
    numbers_table(pd.DataFrame({
        period_label: nr["Period"],
        "New": [fmt(v) for v in nr["New"]],
        "Returning": [fmt(v) for v in nr["Returning"]],
        "Total": [fmt(v) for v in nr["Total people"]],
    }))
with n2:
    fig = go.Figure()
    fig.add_bar(x=nr["Period"], y=nr["New"], name="New", marker_color=PRIMARY)
    fig.add_bar(x=nr["Period"], y=nr["Returning"], name="Returning", marker_color=ACCENT)
    fig.update_layout(barmode="stack", height=320, margin=dict(t=10, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
                      yaxis_title="People")
    st.plotly_chart(fig, use_container_width=True)

# --- Lifetime loyalty (separate question — computed on the FULL dataset) -----
# This is a fixed, per-person, all-history label, so it is deliberately computed
# on df_all (ignores every filter) to match its "lifetime" meaning.
band = contact_repeat_band(df_all)
one_time = int(band["1 - One Time"])
repeater = int(band.drop("1 - One Time").sum())
# split repeaters: came back across 2+ FYs (=> Returning) vs 2+ within one FY (renewal only)
_cnt = df_all.groupby("global_contact_id")["global_participant_id"].count()
_fys = df_all.groupby("global_contact_id")["registration_date_FY"].nunique()
_rep_ids = _cnt[_cnt >= 2].index
rep_across = int((_fys.loc[_rep_ids] >= 2).sum())
rep_within = int((_fys.loc[_rep_ids] == 1).sum())

with st.expander("🔁 Lifetime loyalty — 1-Time vs Repeater (whole dataset, ignores ALL filters)"):
    st.caption("A *different* question from New/Returning above: over a person's **entire history**, "
               "did they subscribe once (**1-Time**) or more than once (**Repeater**)? "
               "This label is fixed per person and does not depend on the year selected — so these "
               "numbers stay the same whatever filters are on.")
    lt1, lt2 = st.columns([1, 2])
    with lt1:
        numbers_table(pd.DataFrame({
            "Type": ["1-Time", "Repeater (2+)"],
            "People": [fmt(one_time), fmt(repeater)],
        }))
    with lt2:
        fig = go.Figure(go.Bar(
            x=["1-Time", "Repeater (2+)"], y=[one_time, repeater],
            marker_color=[PRIMARY, ACCENT], text=[fmt(one_time), fmt(repeater)], textposition="outside",
        ))
        fig.update_layout(margin=dict(t=10, b=10), height=280, yaxis_title="People")
        st.plotly_chart(fig, use_container_width=True)
    st.markdown("**How many times they subscribed (repeat bands):**")
    fig = go.Figure(go.Bar(
        x=band.index, y=band.values,
        marker_color=ACCENT, text=[fmt(v) for v in band.values], textposition="outside",
    ))
    fig.update_layout(margin=dict(t=10, b=10), height=280, yaxis_title="People")
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.markdown("**Repeat pax vs New/Returning — why they're NOT the same calculation**")
    st.markdown(
        f"""
**Repeat pax counts *total* subscriptions per person, ever** — it is **not** derived from the
year-by-year New/Returning logic. A person becomes a **Repeater** ({repeater:,}) by *either* path:

| Path to becoming a Repeater | People | Shows up as |
|---|---|---|
| Came back in a **later FY** | {rep_across:,} | 🟧 Returning (in that later FY) |
| Subscribed 2+ times **within a single FY** only | {rep_within:,} | 🔁 Renewal (never Returning) |
| **Total Repeaters** | **{repeater:,}** | |

So **Repeater ≠ "will be Returning"** — **{rep_within:,}** repeaters *never* appear as Returning;
they simply renewed inside one FY. That's why Repeat pax is counted directly (total registrations),
not built from the per-year New/Returning split.

- **New / Returning** = *when* they subscribed, relative to a year (changes per year).
- **Repeat pax** = *how many times* total, ever (fixed per person).
        """
    )

# ============================================================================
# 4 · ACTIVE SUBSCRIPTIONS  (Active MAU)
# ============================================================================
st.divider()
st.header("4 · Active Subscriptions")

# Determine the view window. A time filter (FY or date range) ZOOMS the trend
# and drives the peak / period-end numbers; it does NOT shrink the pool.
cur_month = act.index.max() if len(act) else None
if not default_dates:
    win_start = pd.Timestamp(date_lo).to_period("M").to_timestamp()
    win_end = pd.Timestamp(date_hi).to_period("M").to_timestamp()
    time_filter_active = True
elif fy_filter_active:
    starts = [pd.Timestamp(year=int(fy.split("-")[0]), month=4, day=1) for fy in fys]
    ends = [pd.Timestamp(year=int(fy.split("-")[0]) + 1, month=3, day=1) for fy in fys]
    win_start, win_end = min(starts), max(ends)
    time_filter_active = True
else:
    win_start = act.index.min() if len(act) else None
    win_end = cur_month
    time_filter_active = False

# clamp to available months (never project past the current month)
if len(act):
    win_start = max(win_start, act.index.min())
    win_end = min(win_end, cur_month)
    act_view = act[(act.index >= win_start) & (act.index <= win_end)]
else:
    act_view = act

st.caption(
    "**How many subscriptions' coverage windows (cohort start → start + plan length) cover a "
    "given month** — a **monthly** count. Pick a year to zoom in on it."
)
with st.expander("ℹ️ How Active Subscriptions is calculated"):
    st.markdown(
        """
- **Coverage window** — each subscription is "active" from its **cohort start date** to
  **start + plan length** (1 / 3 / 6 / 12 months by category). It's active in any month that
  window touches. So the figure is always **per-month**, never a single yearly number.
- **Whole pool** — Category + Audience filters apply, but the **registration-date / FY filter
  does *not*** shrink it; it only **zooms** the trend. "Active in month *M*" is about the
  window, not when the person registered — a plan bought in an earlier FY that's still active
  must still count.
- **What the numbers mean:**
  - *No year selected* → **Active now** (current month) + **all-time peak** (highest month ever).
  - *A year selected* → zoom to it → **Peak in period** (highest month in it) + **Active at
    period-end** (its last month).
  - Either way → **People served**, each person counted **once** for the whole period.
- **Counts subscriptions, not people** — someone with 2 overlapping plans counts as 2.
  *People served* is the exception: that one counts people.

---

##### ⚠️ Why there is no single "active this year" number — and which one to quote

A 3-month plan starting in April is genuinely active in April, May **and** June, so that
person appears in three monthly counts. That is correct. It also means the twelve monthly
figures **must never be added together** — a year-long subscriber would be counted twelve
times. For FY2025-26 the sum comes to ~145,000 against ~29,000 real people, a 5× overstatement.

Think of a gym. You can say how many members were on the books in March. For a whole year
there is no single "members" number, because people join and leave. So you quote one of:

| Question being asked | Use |
|---|---|
| "How busy did we get?" | **Peak in period** |
| "Where do we stand now?" | **Active at period-end** |
| "How many people did we serve this year?" | **People served** ← usually this one |

**Careful:** *People served* (plan **running** during the period) is not the same as the
subscriber counts in section 3 (people who **registered** during the period). Someone who
bought a 1-Year plan last January is still being served this year but did not register this
year. Related numbers, different questions — don't swap them.
- **Cohort-based start**, clipped at the current month (no future projection). Renamed from
  *MAU* — the data has **no login/attendance signal**, only registrations.
        """
    )

a1, a2, a3 = st.columns(3)
if time_filter_active and len(act_view):
    pk_val = int(act_view.max()); pk_month = act_view.idxmax().strftime("%b %Y")
    end_m = act_view.index.max(); end_val = int(act_view.loc[end_m]); end_lbl = end_m.strftime("%b %Y")
    a1.metric("Peak in period", fmt(pk_val), help=f"Highest active month in the selected period ({pk_month}).")
    a2.metric(f"Active at {end_lbl}", fmt(end_val),
              help="Active subscriptions at the end of the selected period (or current month, whichever is earlier).")
    # The single-number answer to "how many were active this year?" -- each person
    # once, so it can be quoted for a period without the sum-the-months error.
    served = people_served_between(df_active, act_view.index.min(), end_m + pd.offsets.MonthEnd(0))
    a3.metric("People served in period", fmt(served),
              help="Distinct people whose plan was running at any point in the selected period. "
                   "Counted once each, however many months they were covered for.")
    a3.caption("Each person counted **once** — this is the number to quote for a year.")
else:
    peak_val = int(act.max()) if len(act) else 0
    peak_month = act.idxmax().strftime("%b %Y") if len(act) else "—"
    a1.metric("Active now", fmt(current_active), help=f"Current month ({cur_month.strftime('%b %Y') if cur_month is not None else '—'}).")
    a1.caption(
        f"{fmt(current_active)} live plans held by {fmt(current_active_people)} people — "
        f"{fmt(current_active_multi)} people run 2+ overlapping plans."
    )
    a2.metric("All-time peak", fmt(peak_val), help=f"Highest active month ever ({peak_month}).")
    if len(act):
        served = people_served_between(df_active, act.index.min(), act.index.max() + pd.offsets.MonthEnd(0))
        a3.metric("People served (all time)", fmt(served),
                  help="Distinct people who have ever held a running plan. Counted once each.")
        a3.caption("Each person counted **once** — pick a year to see it for that year.")

fig = go.Figure(go.Scatter(
    x=act_view.index, y=act_view.values, mode="lines", fill="tozeroy",
    line=dict(color="#27AE60", width=2),
))
fig.update_layout(height=320, margin=dict(t=10, b=10), yaxis_title="Active subscriptions")
st.plotly_chart(fig, use_container_width=True)

# ---- Underlying data --------------------------------------------------------
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

# ---- Developer footnote (tiny, for devs — not meant to draw attention) ------
st.markdown(
    "<div style='font-size:0.62rem; line-height:1.35; color:#9AA0AE; margin-top:2.5rem; "
    "opacity:0.75;'>"
    "Data source: Google Sheet (raw registration rows) · Logic: computed live in "
    "<code>app.py</code> (github: Abinayar2711/ssyoga-dashboard) on each load — nothing is "
    "written back to the Sheet or hand-edited. New/Returning is derived per person from "
    "their earliest-ever registration date; lifetime Repeat pax from their total "
    "registration count. Deduped on <code>global_participant_id</code>; 4 Challenge-Class "
    "categories only."
    "</div>",
    unsafe_allow_html=True,
)
