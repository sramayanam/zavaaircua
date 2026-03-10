"""
Lunar Air – Open Mirroring sample data generator
Produces Parquet files for three tables in the Microsoft Fabric landing zone format:
  • Passengers  – reference/dimension table
  • Flights     – flight operations table
  • Complaints  – fact table (initial load + incremental CDC changes)

Landing zone spec:
  https://learn.microsoft.com/en-us/fabric/mirroring/open-mirroring-landing-zone-format

File naming: 20-digit zero-padded sequence numbers (00000000000000000001.parquet, …)
__rowMarker__ values: 0=Insert, 1=Update, 2=Delete, 4=Upsert  (MUST be last column)
Initial load files have no __rowMarker__ column → treated as bulk INSERT.
"""

import os
import random
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import datetime, timedelta, timezone

random.seed(42)

BASE = os.path.join(os.path.dirname(__file__), "LandingZone", "lunarair.schema")

# ── helpers ──────────────────────────────────────────────────────────────────

def file_name(seq: int) -> str:
    return f"{seq:020d}.parquet"

# ── Explicit pyarrow schemas for Complaints ───────────────────────────────────
# Both files MUST share the same types for every data column so Fabric can
# merge schemas without a SchemaMergeFailure error.

COMPLAINTS_SCHEMA = pa.schema([
    pa.field("complaint_id",      pa.int64()),
    pa.field("passenger_id",      pa.int64()),
    pa.field("flight_id",         pa.int64()),
    pa.field("flight_number",     pa.string()),
    pa.field("complaint_date",    pa.timestamp("ns", tz="UTC")),
    pa.field("category",          pa.string()),
    pa.field("subcategory",       pa.string()),
    pa.field("description",       pa.string()),
    pa.field("severity",          pa.string()),
    pa.field("status",            pa.string()),
    pa.field("assigned_agent",    pa.string()),
    pa.field("resolution_notes",  pa.string()),
    pa.field("resolution_date",   pa.timestamp("ns", tz="UTC")),
    pa.field("satisfaction_score", pa.float64()),
])

# CDC file: same data columns + __rowMarker__ as int32 at the end (required)
COMPLAINTS_CDC_SCHEMA = pa.schema([
    *COMPLAINTS_SCHEMA,
    pa.field("__rowMarker__", pa.int32()),
])

PASSENGERS_SCHEMA = pa.schema([
    pa.field("passenger_id",        pa.int64()),
    pa.field("first_name",          pa.string()),
    pa.field("last_name",           pa.string()),
    pa.field("email",               pa.string()),
    pa.field("phone",               pa.string()),
    pa.field("country",             pa.string()),
    pa.field("frequent_flyer_tier", pa.string()),
    pa.field("total_flights",       pa.int64()),
    pa.field("member_since",        pa.timestamp("ns")),   # tz-naive, matches initial load
])
PASSENGERS_CDC_SCHEMA = pa.schema([*PASSENGERS_SCHEMA, pa.field("__rowMarker__", pa.int32())])

FLIGHTS_SCHEMA = pa.schema([
    pa.field("flight_id",           pa.int64()),
    pa.field("flight_number",       pa.string()),
    pa.field("origin_code",         pa.string()),
    pa.field("origin_city",         pa.string()),
    pa.field("destination_code",    pa.string()),
    pa.field("destination_city",    pa.string()),
    pa.field("scheduled_departure", pa.timestamp("ns", tz="UTC")),
    pa.field("actual_departure",    pa.timestamp("ns", tz="UTC")),
    pa.field("scheduled_arrival",   pa.timestamp("ns", tz="UTC")),
    pa.field("actual_arrival",      pa.timestamp("ns", tz="UTC")),
    pa.field("aircraft_type",       pa.string()),
    pa.field("flight_status",       pa.string()),
    pa.field("delay_minutes",       pa.int64()),
])
FLIGHTS_CDC_SCHEMA = pa.schema([*FLIGHTS_SCHEMA, pa.field("__rowMarker__", pa.int32())])


def write_parquet(df: pd.DataFrame, path: str, schema: pa.Schema | None = None):
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    pq.write_table(table, path)
    rows = len(df)
    print(f"  wrote {rows:>4} rows → {os.path.relpath(path)}")

def rand_ts(start: datetime, days: int = 30) -> datetime:
    return start + timedelta(
        days=random.randint(0, days),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )

# ── static reference data ─────────────────────────────────────────────────────

AIRPORTS = [
    ("LAX", "Los Angeles"),
    ("JFK", "New York"),
    ("ORD", "Chicago"),
    ("MIA", "Miami"),
    ("SEA", "Seattle"),
    ("HOU", "Houston"),
    ("DEN", "Denver"),
    ("LHR", "London"),
    ("CDG", "Paris"),
    ("NRT", "Tokyo"),
    ("SYD", "Sydney"),
    ("YYZ", "Toronto"),
]

AIRCRAFT = [
    "Zava Cruiser 737",
    "Selene A320",
    "Crescent 787-9",
    "Moonbeam A350",
    "Tycho 777X",
]

FLIGHT_STATUSES = ["On Time", "Delayed", "Cancelled", "Diverted"]
FLIGHT_STATUS_WEIGHTS = [0.60, 0.28, 0.08, 0.04]

FF_TIERS = ["None", "Bronze", "Silver", "Gold", "Platinum"]
FF_WEIGHTS = [0.40, 0.25, 0.20, 0.10, 0.05]

COMPLAINT_CATEGORIES = {
    "Baggage": ["Lost Baggage", "Damaged Baggage", "Delayed Baggage", "Overweight Fee Dispute"],
    "Flight Operations": ["Flight Delay", "Flight Cancellation", "Overbooking", "Gate Change"],
    "In-Flight Service": ["Food Quality", "Crew Behavior", "Entertainment System Failure", "Cabin Temperature"],
    "Seating": ["Seat Malfunction", "Seat Assignment Error", "Insufficient Legroom", "Neighbour Issue"],
    "Booking & Refunds": ["Refund Delay", "Incorrect Charge", "Website/App Error", "Cancellation Policy"],
    "Safety": ["Safety Protocol Concern", "Maintenance Issue Observed", "Turbulence Handling"],
    "Special Assistance": ["Wheelchair Service Failure", "Medical Assistance Delay", "Unaccompanied Minor"],
}

DESCRIPTIONS = {
    "Lost Baggage": [
        "My luggage did not arrive at {dest}. I checked in two bags at {orig} and only one arrived.",
        "After my flight ZA{fn} from {orig} to {dest} my suitcase was nowhere to be found at baggage claim.",
        "Baggage carousel at {dest} ran for 45 minutes with no sign of my bag tagged with reference #{ref}.",
    ],
    "Damaged Baggage": [
        "My hardshell suitcase arrived at {dest} with a cracked wheel and a broken zipper.",
        "The handle of my luggage was completely ripped off during flight ZA{fn}.",
        "Contents of my bag were visibly tampered with and a laptop sleeve was torn.",
    ],
    "Flight Delay": [
        "Flight ZA{fn} departed {delay} minutes late from {orig}. No explanation was given to passengers.",
        "We sat on the tarmac at {orig} for over {delay} minutes without any crew communication.",
        "A {delay}-minute delay on ZA{fn} caused me to miss a connecting flight at {dest}.",
    ],
    "Flight Cancellation": [
        "Flight ZA{fn} from {orig} to {dest} was cancelled with less than 2 hours' notice.",
        "My ZA{fn} booking was cancelled. Rescheduled flight offered was 18 hours later.",
        "Cancellation of ZA{fn} stranded me overnight at {orig} without hotel voucher.",
    ],
    "Overbooking": [
        "I was involuntarily bumped from ZA{fn} despite arriving at the gate on time.",
        "Gate agent informed me the flight was oversold and I was placed on standby.",
        "Family of three was split across two flights due to overbooking with no compensation.",
    ],
    "Crew Behavior": [
        "A cabin crew member was dismissive and rude when I asked for an extra blanket.",
        "Flight attendant on ZA{fn} made a sarcastic remark when I requested a dietary meal.",
        "Crew member refused to assist passenger with mobility issue during boarding.",
    ],
    "Food Quality": [
        "The hot meal served on ZA{fn} was cold and the packaging was damaged.",
        "My pre-ordered vegan meal was substituted without notice with a standard meal.",
        "Food options on the {orig}-{dest} route have decreased significantly in quality.",
    ],
    "Seat Malfunction": [
        "Seat 24C on flight ZA{fn} would not recline and the tray table was broken.",
        "My in-flight entertainment screen was black for the entire {orig}-{dest} journey.",
        "Overhead reading light above seat 11A was flickering continuously throughout the flight.",
    ],
    "Refund Delay": [
        "I cancelled my ticket 45 days ago and have not received my refund of $847.",
        "Refund for cancelled flight ZA{fn} was promised in 7 business days; it has been 6 weeks.",
        "Despite multiple follow-up emails, my refund case #{ref} remains unresolved.",
    ],
    "Website/App Error": [
        "The Zava Air app crashed during seat selection, charging me twice for the same booking.",
        "Online check-in failed repeatedly for booking #{ref} forcing me to queue at the airport.",
        "My boarding pass disappeared from the app 20 minutes before boarding.",
    ],
    "Wheelchair Service Failure": [
        "Requested wheelchair assistance for flight ZA{fn} was not available upon arrival.",
        "My elderly mother was left unattended at gate for 40 minutes after wheelchair was requested.",
        "Wheelchair was promised at {dest} but staff at gate were unaware of the request.",
    ],
    "Safety Protocol Concern": [
        "Overhead bin was severely overpacked on ZA{fn} and a bag fell during turbulence.",
        "Seat belt extension was unavailable despite my advance request for flight ZA{fn}.",
        "Exit row passengers were not briefed on emergency procedures before departure.",
    ],
}

FIRST_NAMES = [
    "Amara", "Bjorn", "Chloe", "Diego", "Elif", "Fatima", "Gabriel", "Hana",
    "Ivan", "Jasmine", "Kenji", "Layla", "Marcus", "Nadia", "Oliver", "Priya",
    "Quinton", "Rosa", "Sanjay", "Tariq", "Uma", "Victor", "Wei", "Xiomara",
    "Yusuf",
]
LAST_NAMES = [
    "Adeyemi", "Bergman", "Chen", "Delacroix", "Eriksson", "Flores", "Gupta",
    "Hassan", "Iwata", "Jensen", "Kim", "Leclerc", "Müller", "Nakamura",
    "Okafor", "Patel", "Quiroz", "Reyes", "Singh", "Tanaka", "Ueda",
    "Vasquez", "Wang", "Xu", "Yamamoto",
]
COUNTRIES = [
    "United States", "United Kingdom", "Canada", "Australia", "Japan",
    "Germany", "France", "Brazil", "India", "South Korea", "Nigeria", "Mexico",
]

AGENTS = [
    "Luna Vasquez", "Neil Armstrong II", "Selene Park", "Orion Bailey",
    "Cassidy Moon", "Atlas Rivera", "Nova Singh", "Cosmo Lee",
]

STATUSES = ["Open", "Under Review", "Resolved", "Closed", "Escalated"]
SEVERITIES = ["Low", "Medium", "High", "Critical"]
SEVERITY_WEIGHTS = [0.35, 0.40, 0.18, 0.07]

# ── generate Passengers ───────────────────────────────────────────────────────

def generate_passengers(n: int = 25) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        first = FIRST_NAMES[(i - 1) % len(FIRST_NAMES)]
        last = LAST_NAMES[(i - 1) % len(LAST_NAMES)]
        rows.append({
            "passenger_id":       i,
            "first_name":         first,
            "last_name":          last,
            "email":              f"{first.lower()}.{last.lower()}@example.com",
            "phone":              f"+1-{random.randint(200,999)}-{random.randint(100,999)}-{random.randint(1000,9999)}",
            "country":            random.choice(COUNTRIES),
            "frequent_flyer_tier": random.choices(FF_TIERS, FF_WEIGHTS)[0],
            "total_flights":      random.randint(1, 312),
            "member_since":       pd.Timestamp(
                                    year=random.randint(2015, 2023),
                                    month=random.randint(1, 12),
                                    day=random.randint(1, 28),
                                  ),
        })
    return pd.DataFrame(rows)

# ── generate Flights ──────────────────────────────────────────────────────────

def generate_flights(n: int = 20) -> pd.DataFrame:
    rows = []
    base_date = datetime(2025, 11, 1, tzinfo=timezone.utc)
    for i in range(1, n + 1):
        orig_ap, orig_city = random.choice(AIRPORTS[:7])   # domestic as origin
        dest_ap, dest_city = random.choice([a for a in AIRPORTS if a[0] != orig_ap])
        sched_dep = rand_ts(base_date, days=60)
        delay = 0
        status = random.choices(FLIGHT_STATUSES, FLIGHT_STATUS_WEIGHTS)[0]
        if status == "Delayed":
            delay = random.randint(20, 240)
        actual_dep = sched_dep + timedelta(minutes=delay) if status != "Cancelled" else None
        flight_dur = timedelta(hours=random.randint(2, 14))
        sched_arr = sched_dep + flight_dur
        actual_arr = actual_dep + flight_dur if actual_dep else None
        rows.append({
            "flight_id":           i,
            "flight_number":       f"ZA{100 + i:03d}",
            "origin_code":         orig_ap,
            "origin_city":         orig_city,
            "destination_code":    dest_ap,
            "destination_city":    dest_city,
            "scheduled_departure": pd.Timestamp(sched_dep),
            "actual_departure":    pd.Timestamp(actual_dep) if actual_dep else pd.NaT,
            "scheduled_arrival":   pd.Timestamp(sched_arr),
            "actual_arrival":      pd.Timestamp(actual_arr) if actual_arr else pd.NaT,
            "aircraft_type":       random.choice(AIRCRAFT),
            "flight_status":       status,
            "delay_minutes":       delay,
        })
    return pd.DataFrame(rows)

# ── generate Complaints (initial load) ───────────────────────────────────────

def _pick_description(subcategory: str, flight_num: str, orig: str, dest: str, ref: int) -> str:
    templates = DESCRIPTIONS.get(subcategory)
    if not templates:
        return f"Complaint regarding {subcategory} on flight {flight_num} from {orig} to {dest}."
    t = random.choice(templates)
    delay = random.randint(45, 300)
    return t.format(fn=flight_num[2:], orig=orig, dest=dest, delay=delay, ref=f"LR{ref:06d}")

def generate_complaints_initial(flights_df: pd.DataFrame, n: int = 40) -> pd.DataFrame:
    rows = []
    base_date = datetime(2025, 11, 5, tzinfo=timezone.utc)
    for i in range(1, n + 1):
        flight = flights_df.sample(1).iloc[0]
        category = random.choice(list(COMPLAINT_CATEGORIES.keys()))
        subcategory = random.choice(COMPLAINT_CATEGORIES[category])
        passenger_id = random.randint(1, 25)
        comp_date = rand_ts(base_date, days=55)
        status = random.choices(
            ["Open", "Under Review", "Resolved", "Closed"],
            [0.30, 0.35, 0.25, 0.10],
        )[0]
        severity = random.choices(SEVERITIES, SEVERITY_WEIGHTS)[0]
        resolved = status in ("Resolved", "Closed")
        rows.append({
            "complaint_id":       i,
            "passenger_id":       int(passenger_id),
            "flight_id":          int(flight["flight_id"]),
            "flight_number":      flight["flight_number"],
            "complaint_date":     pd.Timestamp(comp_date),
            "category":           category,
            "subcategory":        subcategory,
            "description":        _pick_description(
                                    subcategory,
                                    flight["flight_number"],
                                    flight["origin_city"],
                                    flight["destination_city"],
                                    i,
                                  ),
            "severity":           severity,
            "status":             status,
            "assigned_agent":     random.choice(AGENTS),
            "resolution_notes":   (
                "Issue investigated and resolved to customer satisfaction."
                if resolved else None
            ),
            "resolution_date":    (
                pd.Timestamp(comp_date + timedelta(days=random.randint(1, 14)))
                if resolved else pd.NaT
            ),
            "satisfaction_score": (
                random.randint(1, 5) if resolved else None
            ),
        })
    return pd.DataFrame(rows)

# ── generate Complaints incremental (CDC file) ────────────────────────────────

def generate_complaints_incremental(initial_df: pd.DataFrame) -> pd.DataFrame:
    """
    Simulates a CDC batch:
      • 5 updates  – complaint status changes (Under Review → Resolved / Escalated)
      • 1 delete   – withdrawn duplicate complaint
      • 3 inserts  – brand-new complaints filed
    __rowMarker__ MUST be the last column.
    """
    rows = []
    # tz-aware UTC timestamps — must match the timestamp[ns, tz=UTC] type in file 1
    resolution_ts = pd.Timestamp("2026-01-10 14:30:00", tz="UTC")

    # ── Updates: resolve 4 "Under Review" complaints ─────────────────────────
    candidates = initial_df[initial_df["status"] == "Under Review"].head(4)
    for _, row in candidates.iterrows():
        updated = row.to_dict()
        updated["status"] = "Resolved"
        updated["resolution_notes"] = "After investigation, Zava Air issued full compensation."
        updated["resolution_date"] = resolution_ts
        updated["satisfaction_score"] = float(random.randint(3, 5))  # double to match file 1
        updated["__rowMarker__"] = 1   # UPDATE
        rows.append(updated)

    # ── Update: escalate one "Open" complaint to Escalated ───────────────────
    escalation_candidate = initial_df[initial_df["status"] == "Open"].head(1)
    for _, row in escalation_candidate.iterrows():
        updated = row.to_dict()
        updated["status"] = "Escalated"
        updated["severity"] = "Critical"
        updated["assigned_agent"] = "Nova Singh"
        updated["resolution_notes"] = "Escalated to senior complaints team per passenger request."
        updated["__rowMarker__"] = 1   # UPDATE
        rows.append(updated)

    # ── Delete: withdraw a duplicate complaint ────────────────────────────────
    delete_candidate = initial_df[initial_df["status"] == "Open"].iloc[1:2]
    for _, row in delete_candidate.iterrows():
        deleted = row.to_dict()
        deleted["__rowMarker__"] = 2   # DELETE
        rows.append(deleted)

    # ── Inserts: 3 brand-new complaints ──────────────────────────────────────
    # complaint_date must be tz-aware UTC to match timestamp[ns, tz=UTC] in file 1
    new_complaints = [
        {
            "complaint_id":       41,
            "passenger_id":       7,
            "flight_id":          12,
            "flight_number":      "LA212",
            "complaint_date":     pd.Timestamp("2026-01-08 09:15:00", tz="UTC"),
            "category":           "Baggage",
            "subcategory":        "Lost Baggage",
            "description":        (
                "My two checked bags did not arrive at NRT after flight LA212 from LAX. "
                "Baggage reference LR000041 filed at airport. No update after 48 hours."
            ),
            "severity":           "High",
            "status":             "Open",
            "assigned_agent":     "Orion Bailey",
            "resolution_notes":   None,
            "resolution_date":    pd.NaT,
            "satisfaction_score": None,
            "__rowMarker__":      0,   # INSERT
        },
        {
            "complaint_id":       42,
            "passenger_id":       14,
            "flight_id":          5,
            "flight_number":      "LA205",
            "complaint_date":     pd.Timestamp("2026-01-09 17:45:00", tz="UTC"),
            "category":           "Booking & Refunds",
            "subcategory":        "Refund Delay",
            "description":        (
                "Refund of $1,240 for cancelled flight LA205 was promised within 7 days. "
                "It has been 5 weeks and my case LR000042 shows no update."
            ),
            "severity":           "Medium",
            "status":             "Under Review",
            "assigned_agent":     "Selene Park",
            "resolution_notes":   None,
            "resolution_date":    pd.NaT,
            "satisfaction_score": None,
            "__rowMarker__":      0,   # INSERT
        },
        {
            "complaint_id":       43,
            "passenger_id":       21,
            "flight_id":          18,
            "flight_number":      "LA218",
            "complaint_date":     pd.Timestamp("2026-01-11 11:00:00", tz="UTC"),
            "category":           "Safety",
            "subcategory":        "Safety Protocol Concern",
            "description":        (
                "An overhead bin on flight LA218 was severely overpacked. "
                "A bag fell and struck a passenger in row 22 during descent."
            ),
            "severity":           "Critical",
            "status":             "Escalated",
            "assigned_agent":     "Atlas Rivera",
            "resolution_notes":   "Safety incident report filed. Pending aviation authority review.",
            "resolution_date":    pd.NaT,
            "satisfaction_score": None,
            "__rowMarker__":      0,   # INSERT
        },
    ]
    rows.extend(new_complaints)

    df = pd.DataFrame(rows)

    # __rowMarker__ must be the last column
    cols = [c for c in df.columns if c != "__rowMarker__"] + ["__rowMarker__"]
    return df[cols]

# ── Passengers CDC ───────────────────────────────────────────────────────────

def generate_passengers_cdc(initial_df: pd.DataFrame) -> pd.DataFrame:
    """
    Simulates real passenger master-data changes:
      • 3 updates  – FF tier upgrades + total_flights increments + one email change
      • 2 inserts  – new passengers who signed up after initial load
      • 1 delete   – closed/merged account
    """
    rows = []
    data_cols = list(PASSENGERS_SCHEMA.names)

    # ── Updates ───────────────────────────────────────────────────────────────
    upgrades = [
        # (passenger_id, new_tier, flights_to_add)
        (3,  "Silver",   15),   # Chloe Chen:  Bronze → Silver
        (10, "Gold",     22),   # Jasmine Iwata: Silver → Gold
        (20, "Bronze",    8),   # Rosa Reyes:  None → Bronze (first milestone)
    ]
    for pid, new_tier, extra_flights in upgrades:
        row = initial_df[initial_df["passenger_id"] == pid].iloc[0].to_dict()
        row["frequent_flyer_tier"] = new_tier
        row["total_flights"] = int(row["total_flights"]) + extra_flights
        row["__rowMarker__"] = 1
        rows.append(row)

    # Email change (passenger married, changed surname)
    row = initial_df[initial_df["passenger_id"] == 8].iloc[0].to_dict()
    row["last_name"] = "Hassan-Rivera"
    row["email"]     = "hana.hassan-rivera@example.com"
    row["__rowMarker__"] = 1
    rows.append(row)

    # ── Inserts – new passengers ──────────────────────────────────────────────
    new_passengers = [
        {
            "passenger_id":        26,
            "first_name":          "Zara",
            "last_name":           "Osei",
            "email":               "zara.osei@example.com",
            "phone":               "+1-415-882-3301",
            "country":             "Ghana",
            "frequent_flyer_tier": "None",
            "total_flights":       1,
            "member_since":        pd.Timestamp("2026-01-15"),
            "__rowMarker__":       0,
        },
        {
            "passenger_id":        27,
            "first_name":          "Luca",
            "last_name":           "Ferreira",
            "email":               "luca.ferreira@example.com",
            "phone":               "+1-212-554-7890",
            "country":             "Brazil",
            "frequent_flyer_tier": "Bronze",
            "total_flights":       14,
            "member_since":        pd.Timestamp("2026-01-20"),
            "__rowMarker__":       0,
        },
    ]
    rows.extend(new_passengers)

    # ── Delete – closed account ───────────────────────────────────────────────
    row = initial_df[initial_df["passenger_id"] == 25].iloc[0].to_dict()
    row["__rowMarker__"] = 2
    rows.append(row)

    df = pd.DataFrame(rows)
    cols = [c for c in df.columns if c != "__rowMarker__"] + ["__rowMarker__"]
    return df[cols]


# ── Flights CDC ───────────────────────────────────────────────────────────────

def generate_flights_cdc(initial_df: pd.DataFrame) -> pd.DataFrame:
    """
    Simulates real-time ops updates:
      • 2 updates  – status changes: On Time → Delayed, On Time → Cancelled
      • 2 inserts  – newly scheduled flights
    Flights are historical records; deletes are not used.
    """
    rows = []

    # ── Updates ───────────────────────────────────────────────────────────────

    # Flight 2 (LA102): On Time → Delayed 45 min
    row = initial_df[initial_df["flight_id"] == 2].iloc[0].to_dict()
    row["flight_status"]    = "Delayed"
    row["delay_minutes"]    = 45
    row["actual_departure"] = pd.Timestamp(row["scheduled_departure"]) + pd.Timedelta(minutes=45)
    row["actual_arrival"]   = pd.Timestamp(row["scheduled_arrival"])   + pd.Timedelta(minutes=45)
    row["__rowMarker__"]    = 1
    rows.append(row)

    # Flight 5 (LA105): On Time → Cancelled (nullify actual times)
    row = initial_df[initial_df["flight_id"] == 5].iloc[0].to_dict()
    row["flight_status"]    = "Cancelled"
    row["delay_minutes"]    = 0
    row["actual_departure"] = pd.NaT
    row["actual_arrival"]   = pd.NaT
    row["__rowMarker__"]    = 1
    rows.append(row)

    # ── Inserts – newly scheduled flights ─────────────────────────────────────
    new_flights = [
        {
            "flight_id":           21,
            "flight_number":       "LA121",
            "origin_code":         "LAX",
            "origin_city":         "Los Angeles",
            "destination_code":    "LHR",
            "destination_city":    "London",
            "scheduled_departure": pd.Timestamp("2026-02-01 22:00:00", tz="UTC"),
            "actual_departure":    pd.Timestamp("2026-02-01 22:00:00", tz="UTC"),
            "scheduled_arrival":   pd.Timestamp("2026-02-02 16:00:00", tz="UTC"),
            "actual_arrival":      pd.Timestamp("2026-02-02 16:00:00", tz="UTC"),
            "aircraft_type":       "Moonbeam A350",
            "flight_status":       "On Time",
            "delay_minutes":       0,
            "__rowMarker__":       0,
        },
        {
            "flight_id":           22,
            "flight_number":       "LA122",
            "origin_code":         "JFK",
            "origin_city":         "New York",
            "destination_code":    "NRT",
            "destination_city":    "Tokyo",
            "scheduled_departure": pd.Timestamp("2026-02-03 01:30:00", tz="UTC"),
            "actual_departure":    pd.NaT,
            "scheduled_arrival":   pd.Timestamp("2026-02-03 05:30:00", tz="UTC"),
            "actual_arrival":      pd.NaT,
            "aircraft_type":       "Tycho 777X",
            "flight_status":       "Cancelled",
            "delay_minutes":       0,
            "__rowMarker__":       0,
        },
    ]
    rows.extend(new_flights)

    df = pd.DataFrame(rows)
    cols = [c for c in df.columns if c != "__rowMarker__"] + ["__rowMarker__"]
    return df[cols]


# ── Complaints CDC batch 2 (file 3) ───────────────────────────────────────────

def generate_complaints_cdc2(initial_df: pd.DataFrame) -> pd.DataFrame:
    """
    Second CDC batch for Complaints (written as file 3 since file 2 already exists):
      • 3 updates  – close already-resolved complaints + escalate one
      • 4 inserts  – new complaints triggered by the cancelled/delayed flights
    """
    rows = []
    batch_ts = pd.Timestamp("2026-01-25 09:00:00", tz="UTC")

    # ── Updates: close 3 Resolved complaints ──────────────────────────────────
    to_close = initial_df[initial_df["status"] == "Resolved"].head(3)
    for _, row in to_close.iterrows():
        updated = row.to_dict()
        updated["status"] = "Closed"
        updated["__rowMarker__"] = 1
        rows.append(updated)

    # ── Update: escalate a High-severity Open complaint ───────────────────────
    high_open = initial_df[
        (initial_df["severity"] == "High") & (initial_df["status"] == "Open")
    ].head(1)
    for _, row in high_open.iterrows():
        updated = row.to_dict()
        updated["status"]           = "Escalated"
        updated["assigned_agent"]   = "Neil Armstrong II"
        updated["resolution_notes"] = "Escalated due to unresolved high-severity issue exceeding SLA."
        updated["__rowMarker__"]    = 1
        rows.append(updated)

    # ── Inserts: complaints from cancelled/delayed flight passengers ───────────
    new_complaints = [
        {   # LA102 delay → missed connection complaint
            "complaint_id":       44,
            "passenger_id":       26,          # new passenger Zara Osei
            "flight_id":          2,
            "flight_number":      "LA102",
            "complaint_date":     pd.Timestamp("2026-01-20 11:30:00", tz="UTC"),
            "category":           "Flight Operations",
            "subcategory":        "Flight Delay",
            "description":        "Flight LA102 was delayed 45 minutes causing me to miss my connecting flight at ORD. "
                                  "No rebooking assistance was offered at the gate.",
            "severity":           "Medium",
            "status":             "Open",
            "assigned_agent":     "Cassidy Moon",
            "resolution_notes":   None,
            "resolution_date":    pd.NaT,
            "satisfaction_score": None,
            "__rowMarker__":      0,
        },
        {   # LA105 cancellation → refund complaint
            "complaint_id":       45,
            "passenger_id":       14,
            "flight_id":          5,
            "flight_number":      "LA105",
            "complaint_date":     pd.Timestamp("2026-01-21 14:00:00", tz="UTC"),
            "category":           "Booking & Refunds",
            "subcategory":        "Refund Delay",
            "description":        "Flight LA105 was cancelled with 1-hour notice. "
                                  "Promised refund of $620 has not appeared after 10 business days.",
            "severity":           "High",
            "status":             "Under Review",
            "assigned_agent":     "Orion Bailey",
            "resolution_notes":   None,
            "resolution_date":    pd.NaT,
            "satisfaction_score": None,
            "__rowMarker__":      0,
        },
        {   # LA105 cancellation → stranded overnight complaint
            "complaint_id":       46,
            "passenger_id":       27,          # new passenger Luca Ferreira
            "flight_id":          5,
            "flight_number":      "LA105",
            "complaint_date":     pd.Timestamp("2026-01-21 17:45:00", tz="UTC"),
            "category":           "Flight Operations",
            "subcategory":        "Flight Cancellation",
            "description":        "Cancellation of LA105 left me stranded overnight at MIA. "
                                  "No hotel voucher was provided and Lunar Air staff left the gate unattended.",
            "severity":           "Critical",
            "status":             "Escalated",
            "assigned_agent":     "Nova Singh",
            "resolution_notes":   "Escalated to senior team. Hotel reimbursement initiated.",
            "resolution_date":    pd.NaT,
            "satisfaction_score": None,
            "__rowMarker__":      0,
        },
        {   # LA122 pre-cancellation complaint (proactive)
            "complaint_id":       47,
            "passenger_id":       9,
            "flight_id":          22,
            "flight_number":      "LA122",
            "complaint_date":     pd.Timestamp("2026-01-24 08:20:00", tz="UTC"),
            "category":           "Booking & Refunds",
            "subcategory":        "Cancellation Policy",
            "description":        "Received cancellation notice for LA122 JFK→NRT with only 48 hours before departure. "
                                  "Business trip cannot be rescheduled. Requesting full refund and compensation.",
            "severity":           "High",
            "status":             "Open",
            "assigned_agent":     "Selene Park",
            "resolution_notes":   None,
            "resolution_date":    pd.NaT,
            "satisfaction_score": None,
            "__rowMarker__":      0,
        },
    ]
    rows.extend(new_complaints)

    df = pd.DataFrame(rows)
    cols = [c for c in df.columns if c != "__rowMarker__"] + ["__rowMarker__"]
    return df[cols]


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n=== Lunar Air – Open Mirroring sample data generator ===\n")

    # ── Passengers ────────────────────────────────────────────────────────────
    print("Passengers (initial load):")
    passengers = generate_passengers(25)
    out = os.path.join(BASE, "Passengers", file_name(1))
    write_parquet(passengers, out)

    # ── Flights ───────────────────────────────────────────────────────────────
    print("\nFlights (initial load):")
    flights = generate_flights(20)
    out = os.path.join(BASE, "Flights", file_name(1))
    write_parquet(flights, out)

    # ── Complaints – initial load (no __rowMarker__) ──────────────────────────
    print("\nComplaints (initial load – file 1, no rowMarker):")
    complaints_init = generate_complaints_initial(flights, n=40)
    out = os.path.join(BASE, "Complaints", file_name(1))
    write_parquet(complaints_init, out, schema=COMPLAINTS_SCHEMA)

    # ── Complaints – CDC batch 1 (file 2) ────────────────────────────────────
    print("\nComplaints (CDC batch 1 – file 2):")
    complaints_cdc1 = generate_complaints_incremental(complaints_init)
    out = os.path.join(BASE, "Complaints", file_name(2))
    write_parquet(complaints_cdc1, out, schema=COMPLAINTS_CDC_SCHEMA)

    # ── Passengers – CDC (file 2) ─────────────────────────────────────────────
    print("\nPassengers (CDC – file 2):")
    passengers_cdc = generate_passengers_cdc(passengers)
    out = os.path.join(BASE, "Passengers", file_name(2))
    write_parquet(passengers_cdc, out, schema=PASSENGERS_CDC_SCHEMA)

    # ── Flights – CDC (file 2) ────────────────────────────────────────────────
    print("\nFlights (CDC – file 2):")
    flights_cdc = generate_flights_cdc(flights)
    out = os.path.join(BASE, "Flights", file_name(2))
    write_parquet(flights_cdc, out, schema=FLIGHTS_CDC_SCHEMA)

    # ── Complaints – CDC batch 2 (file 3) ─────────────────────────────────────
    print("\nComplaints (CDC batch 2 – file 3):")
    complaints_cdc2 = generate_complaints_cdc2(complaints_init)
    out = os.path.join(BASE, "Complaints", file_name(3))
    write_parquet(complaints_cdc2, out, schema=COMPLAINTS_CDC_SCHEMA)

    # ── Summary ───────────────────────────────────────────────────────────────
    def cdc_summary(label: str, df: pd.DataFrame):
        i = (df["__rowMarker__"] == 0).sum()
        u = (df["__rowMarker__"] == 1).sum()
        d = (df["__rowMarker__"] == 2).sum()
        print(f"  {label:<35} {len(df):>3} rows  (I={i} U={u} D={d})")

    print("\n── Summary ─────────────────────────────────────────────")
    print(f"  {'Passengers initial':<35} {len(passengers):>3} rows")
    print(f"  {'Flights initial':<35} {len(flights):>3} rows")
    print(f"  {'Complaints initial':<35} {len(complaints_init):>3} rows")
    cdc_summary("Passengers CDC (file 2)", passengers_cdc)
    cdc_summary("Flights CDC (file 2)",    flights_cdc)
    cdc_summary("Complaints CDC (file 2)", complaints_cdc1)
    cdc_summary("Complaints CDC (file 3)", complaints_cdc2)

    print("\n── File layout ─────────────────────────────────────────")
    for root, dirs, files in os.walk(
        os.path.join(os.path.dirname(__file__), "LandingZone")
    ):
        level = root.replace(os.path.dirname(__file__), "").count(os.sep)
        indent = "  " * level
        print(f"{indent}{os.path.basename(root)}/")
        sub = "  " * (level + 1)
        for f in sorted(files):
            size = os.path.getsize(os.path.join(root, f))
            print(f"{sub}{f}  ({size:,} bytes)")

    print("\nDone. Upload the LandingZone/ folder contents to your Fabric mirrored database.")


if __name__ == "__main__":
    main()
