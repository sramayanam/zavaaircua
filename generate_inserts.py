"""
Helper script to generate SQL INSERT statements from generate_data.py's
exact seed-42 data. Output goes to sql/03_inserts.sql.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'samples'))
from generate_data import generate_passengers, generate_flights, generate_complaints_initial
import pandas as pd

def sql_val(v):
    """Convert a Python/Pandas value to a SQL literal."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 'NULL'
    if isinstance(v, pd.Timestamp):
        if v is pd.NaT:
            return 'NULL'
        return f"'{v.isoformat()}'"
    if isinstance(v, (int,)):
        return str(v)
    if isinstance(v, float):
        if pd.isna(v):
            return 'NULL'
        return str(v)
    # String – escape single quotes
    s = str(v).replace("'", "''")
    return f"'{s}'"

def emit_inserts(df, table, columns):
    lines = []
    for _, row in df.iterrows():
        vals = ', '.join(sql_val(row[c]) for c in columns)
        cols = ', '.join(columns)
        lines.append(f"INSERT INTO custcomplaints.{table} ({cols}) VALUES ({vals});")
    return '\n'.join(lines)

# ── Generate data ──────────────────────────────────────────────────────────
passengers = generate_passengers(25)
flights = generate_flights(20)
complaints = generate_complaints_initial(flights, n=40)

# ── Passengers ──────────────────────────────────────────────────────────────
p_cols = ['passenger_id', 'first_name', 'last_name', 'email', 'phone',
          'country', 'frequent_flyer_tier', 'total_flights', 'member_since']

# ── Flights ─────────────────────────────────────────────────────────────────
f_cols = ['flight_id', 'flight_number', 'origin_code', 'origin_city',
          'destination_code', 'destination_city', 'scheduled_departure',
          'actual_departure', 'scheduled_arrival', 'actual_arrival',
          'aircraft_type', 'flight_status', 'delay_minutes']

# ── Complaints ──────────────────────────────────────────────────────────────
c_cols = ['complaint_id', 'passenger_id', 'flight_id', 'flight_number',
          'complaint_date', 'category', 'subcategory', 'description',
          'severity', 'status', 'assigned_agent', 'resolution_notes',
          'resolution_date', 'satisfaction_score']

header = """-- ============================================================================
-- Lunar Air Customer Complaints – Test Data (seed 42)
-- ============================================================================
-- Generated from samples/generate_data.py with random.seed(42)
-- 25 passengers + 20 flights + 40 complaints
-- ============================================================================

SET search_path TO custcomplaints, public;

BEGIN;

-- ── Passengers (25 rows) ────────────────────────────────────────────────────

"""

mid1 = """

-- ── Flights (20 rows) ───────────────────────────────────────────────────────

"""

mid2 = """

-- ── Complaints (40 rows) ────────────────────────────────────────────────────

"""

footer = """

COMMIT;
"""

sql = (header
       + emit_inserts(passengers, 'passengers', p_cols)
       + mid1
       + emit_inserts(flights, 'flights', f_cols)
       + mid2
       + emit_inserts(complaints, 'complaints', c_cols)
       + footer)

out_path = os.path.join(os.path.dirname(__file__), 'sql', '03_inserts.sql')
with open(out_path, 'w') as f:
    f.write(sql)

print(f"Wrote {out_path}")
print(f"  Passengers: {len(passengers)} rows")
print(f"  Flights:    {len(flights)} rows")
print(f"  Complaints: {len(complaints)} rows")
