-- ============================================================================
-- Lunar Air – Today's Data  (2026-03-09)
-- 5 new flights · 10 new cases · 15 new complaints
-- All complaints dated today so date-filter testing works immediately.
-- Passengers reuse existing IDs 1-15 (no new passengers needed).
-- ============================================================================

SET search_path TO custcomplaints, public;

BEGIN;

-- ── Today's Flights (flight_id 11-15) ───────────────────────────────────────
-- ZA110 JFK→LAX: 35-min delay due to late inbound aircraft
-- ZA221 LAX→DEN: on time
-- ZA332 ORD→JFK: on time
-- ZA443 BOS→MIA: 110-min mechanical delay (longest today)
-- ZA554 SFO→SEA: on time

INSERT INTO custcomplaints.flights
  (flight_id, flight_number, origin_code, origin_city, destination_code, destination_city,
   scheduled_departure, actual_departure, scheduled_arrival, actual_arrival,
   aircraft_type, flight_status, delay_minutes)
VALUES
(11, 'ZA110',  'JFK', 'New York',       'LAX', 'Los Angeles',
  '2026-03-09T08:00:00+00:00', '2026-03-09T08:35:00+00:00',
  '2026-03-09T14:00:00+00:00', '2026-03-09T14:35:00+00:00',
  'Crescent 787-9',   'Delayed',    35),

(12, 'ZA221',  'LAX', 'Los Angeles',    'DEN', 'Denver',
  '2026-03-09T10:30:00+00:00', '2026-03-09T10:30:00+00:00',
  '2026-03-09T13:45:00+00:00', '2026-03-09T13:45:00+00:00',
  'Selene A320',      'On Time',     0),

(13, 'ZA332',  'ORD', 'Chicago',        'JFK', 'New York',
  '2026-03-09T11:00:00+00:00', '2026-03-09T11:00:00+00:00',
  '2026-03-09T14:15:00+00:00', '2026-03-09T14:15:00+00:00',
  'Zava Cruiser 737', 'On Time',     0),

(14, 'ZA443',  'BOS', 'Boston',         'MIA', 'Miami',
  '2026-03-09T07:00:00+00:00', '2026-03-09T08:50:00+00:00',
  '2026-03-09T10:30:00+00:00', '2026-03-09T12:20:00+00:00',
  'Tycho 777X',       'Delayed',   110),

(15, 'ZA554',  'SFO', 'San Francisco',  'SEA', 'Seattle',
  '2026-03-09T09:15:00+00:00', '2026-03-09T09:15:00+00:00',
  '2026-03-09T11:00:00+00:00', '2026-03-09T11:00:00+00:00',
  'Moonbeam A350',    'On Time',     0);

-- ── Today's Cases (case_id 16-25) ────────────────────────────────────────────
-- Reuses existing passengers so all FK references resolve.

INSERT INTO custcomplaints.cases
  (case_id, passenger_id, flight_id, flight_number, pnr, case_status, opened_at, last_updated_at, closed_at)
VALUES
(16,  3, 11, 'ZA110',  'PNR-T001', 'Open',         '2026-03-09T09:10:00+00:00', '2026-03-09T09:10:00+00:00', NULL),
(17,  7, 11, 'ZA110',  'PNR-T002', 'Open',         '2026-03-09T09:45:00+00:00', '2026-03-09T09:45:00+00:00', NULL),
(18, 13, 12, 'ZA221',  'PNR-T003', 'Open',         '2026-03-09T11:05:00+00:00', '2026-03-09T11:05:00+00:00', NULL),
(19,  4, 14, 'ZA443',  'PNR-T004', 'Open',         '2026-03-09T09:20:00+00:00', '2026-03-09T09:20:00+00:00', NULL),
(20, 11, 14, 'ZA443',  'PNR-T005', 'Open',         '2026-03-09T09:35:00+00:00', '2026-03-09T09:35:00+00:00', NULL),
(21,  2, 13, 'ZA332',  'PNR-T006', 'Open',         '2026-03-09T11:30:00+00:00', '2026-03-09T11:30:00+00:00', NULL),
(22,  8, 11, 'ZA110',  'PNR-T007', 'Open',         '2026-03-09T10:15:00+00:00', '2026-03-09T10:15:00+00:00', NULL),
(23, 14, 14, 'ZA443',  'PNR-T008', 'Open',         '2026-03-09T08:55:00+00:00', '2026-03-09T08:55:00+00:00', NULL),
(24,  5, 15, 'ZA554',  'PNR-T009', 'Open',         '2026-03-09T11:45:00+00:00', '2026-03-09T11:45:00+00:00', NULL),
(25, 10, 12, 'ZA221',  'PNR-T010', 'Under Review', '2026-03-09T10:50:00+00:00', '2026-03-09T12:00:00+00:00', NULL);

-- ── Today's Complaints (complaint_id 21-35) ──────────────────────────────────

INSERT INTO custcomplaints.complaints (
    complaint_id, case_id, passenger_id, flight_id, flight_number, pnr,
    complaint_date, category, subcategory, description,
    severity, status, assigned_agent, resolution_notes, resolution_date, satisfaction_score
) VALUES

-- Case 16 – Mia Lopez / ZA110 (delayed 35 min)
(21, 16, 3, 11, 'ZA110', 'PNR-T001',
  '2026-03-09T09:10:00+00:00',
  'Baggage', 'Lost Baggage',
  'Checked my bag at JFK for ZA110 but it did not arrive in Los Angeles. Ground staff could not locate it in the baggage system after 45 minutes of waiting.',
  'High', 'Open', 'Cosmo Lee', NULL, NULL, NULL),

(22, 16, 3, 11, 'ZA110', 'PNR-T001',
  '2026-03-09T09:50:00+00:00',
  'Customer Service', 'Staff Attitude',
  'Baggage desk agent at LAX was unhelpful and dismissive when I tried to file a lost bag report. She refused to give me a reference number.',
  'Medium', 'Open', 'Cosmo Lee', NULL, NULL, NULL),

-- Case 17 – Gabriel Flores / ZA110 (delayed 35 min)
(23, 17, 7, 11, 'ZA110', 'PNR-T002',
  '2026-03-09T09:45:00+00:00',
  'Flight Operations', 'Excessive Delay',
  'ZA110 was delayed 35 minutes at JFK with zero gate announcements. I missed a connecting bus reservation as a direct result of the delay.',
  'High', 'Open', 'Selene Park', NULL, NULL, NULL),

-- Case 18 – Fatima Müller / ZA221 (on time)
(24, 18, 13, 12, 'ZA221', 'PNR-T003',
  '2026-03-09T11:05:00+00:00',
  'Seating', 'Seat Malfunction',
  'Seat 14C on ZA221 had a broken tray table that could not be locked in the upright position. Crew said they were aware but no alternative seat was offered.',
  'Medium', 'Open', 'Selene Park', NULL, NULL, NULL),

-- Case 19 – Liam Kim / ZA443 (110-min delay)
(25, 19, 4, 14, 'ZA443', 'PNR-T004',
  '2026-03-09T09:20:00+00:00',
  'Flight Operations', 'Excessive Delay',
  'ZA443 from Boston to Miami was delayed 110 minutes due to a reported mechanical issue. No updates were provided at the gate for the first 75 minutes.',
  'High', 'Open', 'Orion Bailey', NULL, NULL, NULL),

(26, 19, 4, 14, 'ZA443', 'PNR-T004',
  '2026-03-09T10:10:00+00:00',
  'Customer Service', 'No Compensation Info',
  'After the 110-minute delay on ZA443 I asked both gate staff and a supervisor about compensation entitlements. Neither could provide any information or direct me to the correct process.',
  'Medium', 'Open', 'Orion Bailey', NULL, NULL, NULL),

-- Case 20 – Chloe Kim / ZA443
(27, 20, 11, 14, 'ZA443', 'PNR-T005',
  '2026-03-09T09:35:00+00:00',
  'Special Assistance', 'Wheelchair Service Failure',
  'I booked wheelchair assistance for ZA443 at the time of booking three weeks ago. No wheelchair was present at the gate at BOS or on arrival at MIA, causing me significant difficulty.',
  'Critical', 'Open', 'Orion Bailey', NULL, NULL, NULL),

(28, 20, 11, 14, 'ZA443', 'PNR-T005',
  '2026-03-09T10:30:00+00:00',
  'Baggage', 'Delayed Baggage',
  'My checked bags only arrived 80 minutes after landing at MIA due to the delay on ZA443 and no priority tagging on my mobility-assistance booking.',
  'High', 'Open', 'Orion Bailey', NULL, NULL, NULL),

-- Case 21 – Noah Patel / ZA332 (on time)
(29, 21, 2, 13, 'ZA332', 'PNR-T006',
  '2026-03-09T11:30:00+00:00',
  'In-Flight Service', 'Crew Behaviour',
  'A cabin crew member on ZA332 was openly hostile when I politely asked for an extra blanket. Other passengers in nearby rows witnessed the interaction.',
  'Critical', 'Open', 'Jordan Bell', NULL, NULL, NULL),

(30, 21, 2, 13, 'ZA332', 'PNR-T006',
  '2026-03-09T12:00:00+00:00',
  'Safety', 'Unsafe Conditions',
  'Overhead bin panel above row 7 of ZA332 was visibly cracked and rattling throughout the flight. Crew were informed but no safety check was performed and the flight continued.',
  'Critical', 'Open', 'Jordan Bell', NULL, NULL, NULL),

-- Case 22 – Priya Hassan / ZA110
(31, 22, 8, 11, 'ZA110', 'PNR-T007',
  '2026-03-09T10:15:00+00:00',
  'In-Flight Service', 'Food Quality',
  'The vegetarian meal I pre-ordered for ZA110 was not loaded onto the flight. I was offered a standard meal containing meat as the only alternative.',
  'Medium', 'Open', 'Cosmo Lee', NULL, NULL, NULL),

-- Case 23 – Diego Nakamura / ZA443
(32, 23, 14, 14, 'ZA443', 'PNR-T008',
  '2026-03-09T08:55:00+00:00',
  'Booking', 'Refund Delay',
  'I cancelled a seat upgrade on ZA443 fourteen days ago and the refund has not appeared on my credit card statement. Three emails to support have gone unanswered.',
  'Medium', 'Under Review', 'Cosmo Lee', NULL, NULL, NULL),

-- Case 24 – Emma Brown / ZA554 (on time)
(33, 24, 5, 15, 'ZA554', 'PNR-T009',
  '2026-03-09T11:45:00+00:00',
  'Seating', 'Upgrade Not Applied',
  'My confirmed complimentary Platinum upgrade to row 1 on ZA554 was not applied at boarding. I was told the seat had been given to another passenger. No explanation or alternative offered.',
  'High', 'Open', 'Selene Park', NULL, NULL, NULL),

-- Case 25 – Marcus Jensen / ZA221
(34, 25, 10, 12, 'ZA221', 'PNR-T010',
  '2026-03-09T10:50:00+00:00',
  'In-Flight Service', 'Entertainment System',
  'Seatback screen at seat 24B on ZA221 failed to power on for the entire LAX-DEN flight. Crew attempted a reset twice without success. No compensation was offered.',
  'Low', 'Open', 'Selene Park', NULL, NULL, NULL),

(35, 25, 10, 12, 'ZA221', 'PNR-T010',
  '2026-03-09T11:20:00+00:00',
  'Baggage', 'Damaged Baggage',
  'My hard-shell carry-on was gate-checked on ZA221 due to a full overhead bin and arrived with a broken handle and scuff marks inconsistent with normal handling.',
  'Medium', 'Under Review', 'Selene Park', NULL, NULL, NULL);

COMMIT;