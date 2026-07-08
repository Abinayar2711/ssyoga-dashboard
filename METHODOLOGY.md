# SSYoga Dashboard — Methodology & Definitions

The single reference for how every number on the dashboard is calculated.
All logic is computed **live in `app.py`** from the raw Google Sheet on each load —
nothing is hand-edited or written back to the Sheet.

---

## Scope & units

- **In scope:** the 4 Challenge-Class categories only —
  *1 Month · 3 Month · 6 Month · 1 Year Sri Sri Yoga Challenge Classes.*
- **Dedup:** rows are deduped on `global_participant_id` before anything is counted
  (a duplicate export row must never inflate a total).
- **Two different units — do not mix them:**
  - **Enrollment** = one *registration* (a transaction). One person can have many.
  - **Subscriber** = one *unique person* (`global_contact_id`).

## Financial Year (FY)

Indian FY, **April → March**. Label = `FYstart-end` (e.g. **FY2025-26 = Apr 2025 – Mar 2026**).

---

## The anchor: each person's **first-ever FY**

For every person we compute their **earliest-ever registration date** across their whole
history, and the FY it falls in. This single value drives New / Returning / Renewal.

| For the FY you are viewing… | Label | Unit |
|---|---|---|
| viewed FY **==** their first-ever FY | 🟦 **New** (first time ever) | person |
| viewed FY **≠** first-ever FY (first-ever was earlier) | 🟧 **Returning** (came back) | person |
| **2+ registrations within the same viewed FY** | 🔁 **Renewal** | enrollment (transaction) |

**Key consequences**
- New / Returning is **per-FY and relative** — the *same person* is **New** in the FY they
  first join and **Returning** in any later FY they come back. Their future registrations
  never change a past FY's label (first-ever date is fixed).
- **Renewal does not change New/Returning.** A New person who registers twice in that FY is
  still **1 New person**, just **2 enrollments**. Renewals surface only in the
  *people → enrollments bridge* ("+ renewed more than once"), never as an extra person.
- A **New** person cannot have subscribed earlier — the moment they had, they'd be Returning.
- **Anchor check:** every person is New exactly once → New counts across all FYs sum to the
  total unique subscribers (47,409).

### Worked example — "Bala"
Bala registers in FY2025-26, then again in FY2026-27 → his first-ever FY = **2025-26**.
- Viewing **FY2025-26**: first-ever (2025-26) == viewed → 🟦 **New**
- Viewing **FY2026-27**: first-ever (2025-26) ≠ viewed → 🟧 **Returning**

---

## Repeat pax / Lifetime loyalty (a *separate* calculation)

**Not derived from New/Returning.** Counts each person's **total registrations, ever**:

- **1-Time** = exactly 1 registration ever.
- **Repeater** = 2+ registrations ever. **Fixed per person**, independent of the FY selected.

A person becomes a Repeater by *either* path (whole dataset):
| Path | People | Also shows as |
|---|---|---|
| Came back in a **later FY** | 4,489 | 🟧 Returning |
| Registered 2+ times **within a single FY** only | 2,606 | 🔁 Renewal (never Returning) |
| **Total Repeaters** | **7,095** | |

So **Repeater ≠ "will be Returning"** — 2,606 repeaters never appear as Returning.

- **New / Returning** = *when* they subscribed, relative to an FY (changes per FY).
- **Repeat pax** = *how many times* total, ever (fixed per person).

---

## Other metrics

- **Total Enrollments** — count of registrations (repeats included). Unit = transactions.
- **Unique Subscribers** — distinct `global_contact_id`. Unit = people.
- **Resubscribe Rate** — *per person*: (people with 2+ registrations) ÷ (total people).
  **Not** per-registration.
- **Active Subscriptions** *(renamed from "MAU")* — a registration is "active" in month *M*
  if its cohort window `[course_event_start_date, +duration]` overlaps *M*, clipped at the
  current month (no future projection). Renamed from MAU because the data has **no
  login/attendance signal** — only registration events. `course_event_start_date` is a
  shared **cohort/batch** start (only ~33 distinct values), not a per-person start date.

---

## Reference anchor numbers (full dataset)

| Metric | Value |
|---|---|
| Deduped rows (enrollments) | 56,808 |
| Unique subscribers | 47,409 |
| 1-Time / Repeater (lifetime) | 40,314 / 7,095 |
| Enrollments by CY (2024/25/26) | 8,410 / 26,621 / 21,777 |
| Enrollments by FY (23-24 … 26-27) | 1,889 / 8,366 / 33,492 / 13,061 |
| New subscribers (2024/25/26) | 8,074 / 21,809 / 17,526 |

*Numbers shift slightly as the Sheet is refreshed with new registrations — recompute, don't
assume these are frozen.*
