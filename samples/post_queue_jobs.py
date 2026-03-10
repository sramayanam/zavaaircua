#!/usr/bin/env python3
"""
post_queue_jobs.py
Fire 6 CUA complaint jobs via Azure Storage Queue (cua-agent-jobs).
Each job maps to a real ZA flight and passenger in the seed data.

Usage:
    python samples/post_queue_jobs.py
    python samples/post_queue_jobs.py --dry-run
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone

# ── Azure config ─────────────────────────────────────────────────────────────
SUBSCRIPTION  = "32e739cb-7b23-4259-a180-e1e0e69b974d"
ACCOUNT       = "aaaorgcuastore"
QUEUE         = "cua-agent-jobs"

# ── Jobs ─────────────────────────────────────────────────────────────────────
JOBS = [
    {
        "job_id":             "q-baggage-ava-001",
        "id":                 "q_baggage_ava_001",
        "name":               "Queue Job – Lost Baggage",
        "scenario_description": "Ava Carter's checked bag was lost on ZA505 MIA→JFK. Tests Baggage / High severity path.",
        "passenger_name":     "Ava Carter",
        "passenger_email":    "ava.carter@example.com",
        "passenger_phone":    "+1-555-0101",
        "flight_number":      "ZA505",
        "pnr":                "PNR-Q-5001",
        "category":           "Baggage",
        "subcategory":        "Lost Baggage",
        "severity":           "High",
        "agent":              "Orion Bailey",
        "complaint_description":
            "My checked bag was lost on flight ZA505 from Miami to New York. "
            "I filed a missing baggage report at the airport 48 hours ago but have received "
            "no update. The bag contains essential medication I urgently need.",
    },
    {
        "job_id":             "q-crew-noah-001",
        "id":                 "q_crew_noah_001",
        "name":               "Queue Job – Crew Behaviour",
        "scenario_description": "Noah Patel (Gold) experienced rude crew on ZA707 JFK→LHR. Tests In-Flight Service / Medium severity path.",
        "passenger_name":     "Noah Patel",
        "passenger_email":    "noah.patel@example.com",
        "passenger_phone":    "+1-555-0102",
        "flight_number":      "ZA707",
        "pnr":                "PNR-Q-7001",
        "category":           "In-Flight Service",
        "subcategory":        "Crew Behaviour",
        "severity":           "Medium",
        "agent":              "Selene Park",
        "complaint_description":
            "A crew member on ZA707 from New York to London repeatedly ignored my call button "
            "and made a dismissive remark when I politely requested a blanket during the 7-hour overnight flight.",
    },
    {
        "job_id":             "q-refund-amara-001",
        "id":                 "q_refund_amara_001",
        "name":               "Queue Job – Refund Delay",
        "scenario_description": "Amara Adeyemi is waiting on a $280 refund for ZA808 LAX→NRT. Tests Booking & Refunds / High severity path.",
        "passenger_name":     "Amara Adeyemi",
        "passenger_email":    "amara.adeyemi@example.com",
        "passenger_phone":    "+1-555-0106",
        "flight_number":      "ZA808",
        "pnr":                "PNR-Q-8001",
        "category":           "Booking & Refunds",
        "subcategory":        "Refund Delay",
        "severity":           "High",
        "agent":              "Cosmo Lee",
        "complaint_description":
            "I cancelled my seat upgrade on ZA808 from Los Angeles to Tokyo 3 weeks ago. "
            "The refund of $280 has still not appeared on my credit card and customer support has not responded.",
    },
    {
        "job_id":             "q-delay-marcus-001",
        "id":                 "q_delay_marcus_001",
        "name":               "Queue Job – Excessive Delay",
        "scenario_description": "Marcus Jensen missed a connection due to ZA606 DEN→LAX 95-min delay. Tests Flight Operations / High severity path.",
        "passenger_name":     "Marcus Jensen",
        "passenger_email":    "marcus.jensen@example.com",
        "passenger_phone":    "+1-555-0110",
        "flight_number":      "ZA606",
        "pnr":                "PNR-Q-6001",
        "category":           "Flight Operations",
        "subcategory":        "Excessive Delay",
        "severity":           "High",
        "agent":              "Orion Bailey",
        "complaint_description":
            "ZA606 from Denver to Los Angeles was delayed 95 minutes with no communication from the gate. "
            "I missed my connecting flight and had to purchase a new ticket for $450 out of pocket.",
    },
    {
        "job_id":             "q-seat-chloe-001",
        "id":                 "q_seat_chloe_001",
        "name":               "Queue Job – Seat Malfunction",
        "scenario_description": "Chloe Kim's tray table was broken on ZA909 ORD→DEN. Tests Seating / Medium severity path.",
        "passenger_name":     "Chloe Kim",
        "passenger_email":    "chloe.kim@example.com",
        "passenger_phone":    "+1-555-0111",
        "flight_number":      "ZA909",
        "pnr":                "PNR-Q-9001",
        "category":           "Seating",
        "subcategory":        "Seat Malfunction",
        "severity":           "Medium",
        "agent":              "Selene Park",
        "complaint_description":
            "The tray table on seat 18C on ZA909 from Chicago to Denver was completely broken — "
            "it would not lock in the upright position. Crew attempted to tape it but it kept falling onto my laptop.",
    },
    {
        "job_id":             "q-wheelchair-elif-001",
        "id":                 "q_wheelchair_elif_001",
        "name":               "Queue Job – Wheelchair Service Failure",
        "scenario_description": "Elif Okafor was left without wheelchair assistance on ZA303 SEA→ORD. Tests Special Assistance / Critical severity path.",
        "passenger_name":     "Elif Okafor",
        "passenger_email":    "elif.okafor@example.com",
        "passenger_phone":    "+1-555-0115",
        "flight_number":      "ZA303",
        "pnr":                "PNR-Q-3001",
        "category":           "Special Assistance",
        "subcategory":        "Wheelchair Service Failure",
        "severity":           "Critical",
        "agent":              "Cosmo Lee",
        "complaint_description":
            "I pre-booked wheelchair assistance for flight ZA303 from Seattle to Chicago. "
            "No wheelchair was available at the gate. I was forced to walk the full jetway unaided "
            "despite a recent hip surgery, causing significant pain. This is the second time this has happened.",
    },
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def post_job(job: dict, dry_run: bool) -> bool:
    payload = {**job, "enqueued_at": datetime.now(timezone.utc).isoformat()}
    content = json.dumps(payload)

    cmd = [
        "az", "storage", "message", "put",
        "--queue-name",   QUEUE,
        "--account-name", ACCOUNT,
        "--content",      content,
        "--subscription", SUBSCRIPTION,
        "--auth-mode",    "login",
    ]

    job_id = job["job_id"]
    if dry_run:
        print(f"[DRY-RUN] {job_id}")
        print(f"  payload: {json.dumps(payload, indent=2)}")
        return True

    print(f"Posting {job_id} … ", end="", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("OK")
        return True
    else:
        print("FAILED")
        print(f"  stderr: {result.stderr.strip()}", file=sys.stderr)
        return False


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Post CUA queue jobs for ZavaAir complaints")
    parser.add_argument("--dry-run", action="store_true", help="Print payloads without posting")
    args = parser.parse_args()

    total = len(JOBS)
    ok    = 0
    for job in JOBS:
        if post_job(job, args.dry_run):
            ok += 1

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}{ok}/{total} jobs {'would be ' if args.dry_run else ''}posted successfully.")
    sys.exit(0 if ok == total else 1)


if __name__ == "__main__":
    main()
