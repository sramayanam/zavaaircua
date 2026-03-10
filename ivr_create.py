"""
Lunar Air – IVR Complaint Creator
==================================
Simulates IVR call data and POSTs it to POST /api/ivr/complaint.

Run manually:
    python ivr_create.py                  # runs all 3 mock scenarios
    python ivr_create.py 1                # runs scenario 1 only

Invoked by Logic App:
    The Logic App calls POST /api/ivr/complaint directly with the same
    payload shape defined in IVR_SCENARIOS below.

IVR menu mapping (matches server.js IVR_MENU):
    Category digit:   1=Baggage  2=Flight Ops  3=In-Flight  4=Seating
                      5=Booking  6=Safety       7=Special    8=Customer Svc
    Subcategory digit: 1–4 depending on category (see server.js IVR_MENU)
"""

import json
import sys
import urllib.request
import urllib.error
import os
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE = os.environ.get("ZAVA_AIR_URL", "http://localhost:3000")
IVR_ENDPOINT = f"{API_BASE}/api/ivr/complaint"

# ── Mock IVR call scenarios ───────────────────────────────────────────────────
# Each entry represents what an IVR system would send after a call completes.
# Passenger phone numbers and flight numbers match the seeded database.

IVR_SCENARIOS = [
    {
        # Hana Hassan – Gold tier – LA101 (JFK→SYD, delayed 226 min)
        # Category 1 → Baggage, Subcategory 1 → Lost Baggage
        # Expected severity: High (Lost Baggage rule)
        "call_id": "IVR-2026-0302-001",
        "caller_phone": "+1-991-396-2307",
        "flight_number": "LA101",
        "pnr": "PNR-IVR-1001",
        "ivr_category": "1",
        "ivr_subcategory": "1",
        "call_transcript": (
            "I am a Gold frequent flyer and my checked luggage did not arrive after "
            "flight LA101 from New York to Sydney which was already delayed by almost "
            "four hours. I have been waiting at the baggage carousel for over an hour "
            "with no update from ground staff. My bag contains essential medication and "
            "business documents needed for a meeting tomorrow morning. "
            "Baggage reference tag number LR002241. I need this located and delivered "
            "to my hotel urgently."
        ),
        "call_duration_seconds": 214,
    },
    {
        # Quinton Quiroz – Platinum tier – LA105 (MIA→SEA, delayed 239 min)
        # Category 3 → In-Flight Service, Subcategory 2 → Crew Behavior
        # Expected severity: High (Platinum + In-Flight Service rule)
        "call_id": "IVR-2026-0302-002",
        "caller_phone": "+1-645-261-8433",
        "flight_number": "LA105",
        "pnr": "PNR-IVR-1002",
        "ivr_category": "3",
        "ivr_subcategory": "2",
        "call_transcript": (
            "I am calling to report extremely rude and dismissive behavior from a senior "
            "cabin crew member on flight LA105 from Miami to Seattle. When I politely "
            "asked for assistance rebooking a missed connection at the gate the crew "
            "member raised their voice and told me it was not their problem. Other "
            "passengers nearby witnessed the incident. As a Platinum member I expect a "
            "higher standard of service. I want a formal apology and a review of this "
            "crew member's conduct."
        ),
        "call_duration_seconds": 187,
    },
    {
        # Tariq Tanaka – Gold tier – LA105 (MIA→SEA)
        # Category 7 → Special Assistance, Subcategory 1 → Wheelchair Service Failure
        # Expected severity: Critical (Wheelchair Service Failure rule)
        "call_id": "IVR-2026-0302-003",
        "caller_phone": "+1-514-345-1949",
        "flight_number": "LA105",
        "pnr": "PNR-IVR-1003",
        "ivr_category": "7",
        "ivr_subcategory": "1",
        "call_transcript": (
            "My 82-year-old father who requires a wheelchair was left completely "
            "unassisted at the gate at Miami for over one hour after flight LA105 was "
            "delayed. No ground staff responded to repeated calls for wheelchair "
            "assistance. He was unable to reach the restroom or get water on his own. "
            "He is diabetic and missed his medication window because of this. This is "
            "unacceptable and I am demanding this be escalated to a supervisor immediately."
        ),
        "call_duration_seconds": 301,
    },
]

# ── HTTP helper ───────────────────────────────────────────────────────────────

def post_ivr_complaint(payload: dict) -> dict:
    """POST payload to /api/ivr/complaint and return the parsed JSON response."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        IVR_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"status": resp.status, "body": json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "body": json.loads(e.read())}
    except urllib.error.URLError as e:
        raise SystemExit(f"Cannot reach {IVR_ENDPOINT} – is the server running?\n  {e.reason}")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_scenario(index: int, scenario: dict):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"\n{'─' * 60}")
    print(f"  Scenario {index + 1}  |  {ts}")
    print(f"  Call ID  : {scenario['call_id']}")
    print(f"  Phone    : {scenario['caller_phone']}")
    print(f"  Flight   : {scenario['flight_number']}")
    print(f"  IVR menu : category={scenario['ivr_category']}  subcategory={scenario['ivr_subcategory']}")
    print(f"  Duration : {scenario.get('call_duration_seconds', 'N/A')}s")
    print(f"  Transcript (first 80 chars): {scenario['call_transcript'][:80]}…")
    print()

    result = post_ivr_complaint(scenario)
    status = result["status"]
    body = result["body"]

    if status == 201:
        c = body["complaint"]
        r = body["ivr_resolved"]
        print(f"  ✓ Created complaint #{c['complaint_id']}")
        print(f"    Passenger ID : {r['passenger_id']}")
        print(f"    Flight ID    : {r['flight_id']}")
        print(f"    Category     : {r['category']} → {r['subcategory']}")
        print(f"    Severity     : {r['severity']}")
        print(f"    Assigned to  : {r['assigned_agent']}")
        print(f"    Status       : {c['status']}")
    else:
        print(f"  ✗ HTTP {status}: {body.get('error', body)}")


def main():
    scenarios = IVR_SCENARIOS

    # Optional: single scenario index passed as CLI arg
    if len(sys.argv) > 1:
        try:
            idx = int(sys.argv[1]) - 1
            scenarios = [IVR_SCENARIOS[idx]]
            idx_offset = idx
        except (ValueError, IndexError):
            print(f"Usage: python ivr_create.py [1–{len(IVR_SCENARIOS)}]")
            sys.exit(1)
    else:
        idx_offset = 0

    print("\nLunar Air – IVR Complaint Creator")
    print(f"Endpoint : {IVR_ENDPOINT}")
    print(f"Scenarios: {len(scenarios)}")

    for i, scenario in enumerate(scenarios):
        run_scenario(i + idx_offset, scenario)

    print(f"\n{'─' * 60}")
    print("Done.\n")


if __name__ == "__main__":
    main()
