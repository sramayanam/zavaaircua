/**
 * Zava Air Complaints – Express REST API
 *
 * Endpoints:
 *   GET    /api/complaints        List all complaints (joined with passenger + flight)
 *   GET    /api/complaints/:id    Single complaint detail
 *   POST   /api/complaints        Create a new complaint
 *   PUT    /api/complaints/:id    Update a complaint (status, resolution, etc.)
 *   GET    /api/passengers        Dropdown data – passengers
 *   GET    /api/flights           Dropdown data – flights
 *   GET    /api/categories        Category → subcategory hierarchy
 */
const express = require('express');
const cors = require('cors');
const path = require('path');
require('dotenv').config();

const pool = require('./db');

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// ── Category / subcategory hierarchy (static, matches generate_data.py) ─────

const CATEGORIES = {
  'Baggage': ['Lost Baggage', 'Damaged Baggage', 'Delayed Baggage', 'Overweight Fee Dispute'],
  'Flight Operations': ['Flight Delay', 'Flight Cancellation', 'Overbooking', 'Gate Change'],
  'In-Flight Service': ['Food Quality', 'Crew Behavior', 'Entertainment System Failure', 'Cabin Temperature'],
  'Seating': ['Seat Malfunction', 'Seat Assignment Error', 'Insufficient Legroom', 'Neighbour Issue'],
  'Booking & Refunds': ['Refund Delay', 'Incorrect Charge', 'Website/App Error', 'Cancellation Policy'],
  'Safety': ['Safety Protocol Concern', 'Maintenance Issue Observed', 'Turbulence Handling'],
  'Special Assistance': ['Wheelchair Service Failure', 'Medical Assistance Delay', 'Unaccompanied Minor'],
  'Customer Service': ['Staff Attitude'],
};

const AGENTS = [
  'Luna Vasquez', 'Neil Armstrong II', 'Selene Park', 'Orion Bailey',
  'Cassidy Moon', 'Atlas Rivera', 'Nova Singh', 'Cosmo Lee',
];

const ACTIVE_CASE_STATUSES = ['Open', 'Under Review', 'Escalated'];

function parsePassengerName(fullName) {
  const cleaned = String(fullName || '').trim().replace(/\s+/g, ' ');
  if (!cleaned) return { firstName: '', lastName: '' };
  const parts = cleaned.split(' ');
  if (parts.length === 1) return { firstName: parts[0], lastName: 'Unknown' };
  return {
    firstName: parts[0],
    lastName: parts.slice(1).join(' '),
  };
}

function namesMatch(expectedName, dbFirstName, dbLastName) {
  const normalizedExpected = String(expectedName || '').trim().toLowerCase().replace(/\s+/g, ' ');
  const normalizedDb = `${dbFirstName || ''} ${dbLastName || ''}`.trim().toLowerCase().replace(/\s+/g, ' ');
  return normalizedExpected.length > 0 && normalizedExpected === normalizedDb;
}

async function getNextId(tableName, idColumn) {
  const { rows } = await pool.query(
    `SELECT COALESCE(MAX(${idColumn}), 0) + 1 AS next_id FROM ${tableName}`
  );
  return rows[0].next_id;
}

async function findOrCreatePassenger(passengerName, passengerEmail, passengerPhone) {
  const { firstName, lastName } = parsePassengerName(passengerName);

  if (!firstName) {
    return { error: 'passenger_name is required' };
  }
  if (!passengerEmail && !passengerPhone) {
    return { error: 'Either passenger_email or passenger_phone is required' };
  }

  const { rows: existingRows } = await pool.query(
    `SELECT *
       FROM passengers
      WHERE lower(first_name) = lower($1)
        AND lower(last_name) = lower($2)
        AND (
          ($3::text IS NOT NULL AND lower(email) = lower($3))
          OR ($4::text IS NOT NULL AND phone = $4)
        )
      LIMIT 1`,
    [firstName, lastName, passengerEmail || null, passengerPhone || null]
  );

  if (existingRows.length > 0) {
    const existing = existingRows[0];
    if ((!existing.email && passengerEmail) || (!existing.phone && passengerPhone)) {
      await pool.query(
        `UPDATE passengers
            SET email = COALESCE(email, $1),
                phone = COALESCE(phone, $2)
          WHERE passenger_id = $3`,
        [passengerEmail || null, passengerPhone || null, existing.passenger_id]
      );
    }
    return { passenger: existing, isNewPassenger: false };
  }

  const nextPassengerId = await getNextId('passengers', 'passenger_id');
  const { rows } = await pool.query(
    `INSERT INTO passengers
      (passenger_id, first_name, last_name, email, phone, country, frequent_flyer_tier, total_flights, member_since)
     VALUES ($1, $2, $3, $4, $5, 'Unknown', 'None', 0, NOW())
     RETURNING *`,
    [nextPassengerId, firstName, lastName, passengerEmail || null, passengerPhone || null]
  );

  return { passenger: rows[0], isNewPassenger: true };
}

async function findOrCreateCase(passengerId, flightId, flightNumber, pnr) {
  const { rows: activeCaseRows } = await pool.query(
    `SELECT *
       FROM cases
      WHERE passenger_id = $1
        AND flight_id = $2
        AND pnr = $3
        AND case_status = ANY($4::text[])
      LIMIT 1`,
    [passengerId, flightId, pnr, ACTIVE_CASE_STATUSES]
  );

  if (activeCaseRows.length > 0) {
    return { caseRow: activeCaseRows[0], isExistingCase: true };
  }

  const { rows: anyCaseRows } = await pool.query(
    `SELECT *
       FROM cases
      WHERE passenger_id = $1
        AND flight_id = $2
        AND pnr = $3
      LIMIT 1`,
    [passengerId, flightId, pnr]
  );

  if (anyCaseRows.length > 0) {
    const reopened = await pool.query(
      `UPDATE cases
          SET case_status = 'Open',
              last_updated_at = NOW(),
              closed_at = NULL
        WHERE case_id = $1
        RETURNING *`,
      [anyCaseRows[0].case_id]
    );
    return { caseRow: reopened.rows[0], isExistingCase: true };
  }

  const nextCaseId = await getNextId('cases', 'case_id');
  const { rows: newCaseRows } = await pool.query(
    `INSERT INTO cases
      (case_id, passenger_id, flight_id, flight_number, pnr, case_status, opened_at, last_updated_at)
     VALUES ($1, $2, $3, $4, $5, 'Open', NOW(), NOW())
     RETURNING *`,
    [nextCaseId, passengerId, flightId, flightNumber, pnr]
  );

  return { caseRow: newCaseRows[0], isExistingCase: false };
}

// ── IVR Menu: digit → category / subcategory names ──────────────────────────
// Mirrors the keypad prompts played to the caller.
// Category digit 1–8, subcategory digit 1–4 (varies per category).

const IVR_MENU = {
  '1': { name: 'Baggage',           subs: { '1': 'Lost Baggage', '2': 'Damaged Baggage', '3': 'Delayed Baggage', '4': 'Overweight Fee Dispute' } },
  '2': { name: 'Flight Operations', subs: { '1': 'Flight Delay', '2': 'Flight Cancellation', '3': 'Overbooking', '4': 'Gate Change' } },
  '3': { name: 'In-Flight Service', subs: { '1': 'Food Quality', '2': 'Crew Behavior', '3': 'Entertainment System Failure', '4': 'Cabin Temperature' } },
  '4': { name: 'Seating',           subs: { '1': 'Seat Malfunction', '2': 'Seat Assignment Error', '3': 'Insufficient Legroom', '4': 'Neighbour Issue' } },
  '5': { name: 'Booking & Refunds', subs: { '1': 'Refund Delay', '2': 'Incorrect Charge', '3': 'Website/App Error', '4': 'Cancellation Policy' } },
  '6': { name: 'Safety',            subs: { '1': 'Safety Protocol Concern', '2': 'Maintenance Issue Observed', '3': 'Turbulence Handling' } },
  '7': { name: 'Special Assistance',subs: { '1': 'Wheelchair Service Failure', '2': 'Medical Assistance Delay', '3': 'Unaccompanied Minor' } },
  '8': { name: 'Customer Service',  subs: { '1': 'Staff Attitude' } },
};

// Infer severity from category + subcategory + frequent-flyer tier.
function inferSeverity(category, subcategory, ffTier) {
  if (category === 'Safety') return 'Critical';
  if (['Wheelchair Service Failure', 'Medical Assistance Delay'].includes(subcategory)) return 'Critical';
  if (subcategory === 'Unaccompanied Minor') return 'High';
  if (['Platinum', 'Gold'].includes(ffTier) && ['Special Assistance', 'In-Flight Service'].includes(category)) return 'High';
  if (category === 'Flight Operations') return 'High';
  if (subcategory === 'Lost Baggage') return 'High';
  return 'Medium';
}

// ── GET /api/complaints ─────────────────────────────────────────────────────

app.get('/api/complaints', async (req, res) => {
  try {
    const { status, severity, category, date_from, date_to } = req.query;
    let query = `
      SELECT c.*,
           cs.case_status,
             p.first_name || ' ' || p.last_name AS passenger_name,
             p.frequent_flyer_tier,
             f.origin_code || ' → ' || f.destination_code AS route
        FROM complaints c
         JOIN cases cs   ON c.case_id      = cs.case_id
        JOIN passengers p ON c.passenger_id = p.passenger_id
        JOIN flights f    ON c.flight_id    = f.flight_id
       WHERE 1=1
    `;
    const params = [];
    if (status) {
      params.push(status);
      query += ` AND c.status = $${params.length}`;
    }
    if (severity) {
      params.push(severity);
      query += ` AND c.severity = $${params.length}`;
    }
    if (category) {
      params.push(category);
      query += ` AND c.category = $${params.length}`;
    }
    if (date_from) {
      params.push(date_from);
      query += ` AND c.complaint_date::date >= $${params.length}`;
    }
    if (date_to) {
      params.push(date_to);
      query += ` AND c.complaint_date::date <= $${params.length}`;
    }
    query += ' ORDER BY c.complaint_date DESC';

    const { rows } = await pool.query(query, params);
    res.json(rows);
  } catch (err) {
    console.error('GET /api/complaints error:', err);
    res.status(500).json({ error: 'Failed to fetch complaints' });
  }
});

// ── GET /api/complaints/:id ─────────────────────────────────────────────────

app.get('/api/complaints/:id', async (req, res) => {
  try {
    const { rows } = await pool.query(`
      SELECT c.*,
             cs.case_status,
             p.first_name || ' ' || p.last_name AS passenger_name,
             p.first_name AS passenger_first_name,
             p.last_name AS passenger_last_name,
             p.email AS passenger_email,
             p.phone AS passenger_phone,
             p.frequent_flyer_tier,
             p.country AS passenger_country,
             f.origin_code, f.origin_city,
             f.destination_code, f.destination_city,
             f.scheduled_departure, f.actual_departure,
             f.aircraft_type, f.flight_status, f.delay_minutes
        FROM complaints c
        JOIN cases cs   ON c.case_id      = cs.case_id
        JOIN passengers p ON c.passenger_id = p.passenger_id
        JOIN flights f    ON c.flight_id    = f.flight_id
       WHERE c.complaint_id = $1
    `, [req.params.id]);

    if (rows.length === 0) return res.status(404).json({ error: 'Complaint not found' });
    res.json(rows[0]);
  } catch (err) {
    console.error('GET /api/complaints/:id error:', err);
    res.status(500).json({ error: 'Failed to fetch complaint' });
  }
});

// ── POST /api/complaints ────────────────────────────────────────────────────

app.post('/api/complaints', async (req, res) => {
  try {
    const {
      passenger_name,
      passenger_email,
      passenger_phone,
      flight_id,
      pnr,
      category,
      subcategory,
      description,
      severity,
      assigned_agent,
    } = req.body;

    if (!flight_id || !category || !subcategory || !description || !severity || !pnr) {
      return res.status(400).json({ error: 'Missing required fields for complaint creation' });
    }

    const { rows: flightRows } = await pool.query(
      `SELECT flight_id, flight_number
         FROM flights
        WHERE flight_id = $1
        LIMIT 1`,
      [flight_id]
    );
    if (flightRows.length === 0) {
      return res.status(404).json({ error: `No flight found for flight_id: ${flight_id}` });
    }
    const flight = flightRows[0];

    const passengerResult = await findOrCreatePassenger(passenger_name, passenger_email, passenger_phone);
    if (passengerResult.error) {
      return res.status(400).json({ error: passengerResult.error });
    }
    const passenger = passengerResult.passenger;

    const caseResult = await findOrCreateCase(passenger.passenger_id, flight.flight_id, flight.flight_number, String(pnr).trim());

    const nextId = await getNextId('complaints', 'complaint_id');

    const { rows } = await pool.query(`
      INSERT INTO complaints
        (complaint_id, case_id, passenger_id, flight_id, flight_number, pnr, complaint_date,
         category, subcategory, description, severity, status, assigned_agent)
      VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9, $10, 'Open', $11)
      RETURNING *
    `, [
      nextId,
      caseResult.caseRow.case_id,
      passenger.passenger_id,
      flight.flight_id,
      flight.flight_number,
      String(pnr).trim(),
      category,
      subcategory,
      description,
      severity,
      assigned_agent,
    ]);

    await pool.query(
      `UPDATE cases
          SET last_updated_at = NOW()
        WHERE case_id = $1`,
      [caseResult.caseRow.case_id]
    );

    res.status(201).json({
      ...rows[0],
      case_context: {
        case_id: caseResult.caseRow.case_id,
        is_existing_case: caseResult.isExistingCase,
        case_status: caseResult.caseRow.case_status,
      },
      passenger_context: {
        passenger_id: passenger.passenger_id,
        is_new_passenger: passengerResult.isNewPassenger,
      },
    });
  } catch (err) {
    console.error('POST /api/complaints error:', err);
    res.status(500).json({ error: 'Failed to create complaint' });
  }
});

// ── PUT /api/complaints/:id ─────────────────────────────────────────────────

app.put('/api/complaints/:id', async (req, res) => {
  try {
    const {
      passenger_name,
      passenger_email,
      passenger_phone,
      flight_id,
      flight_number,
      pnr,
      status,
      severity,
      assigned_agent,
      resolution_notes,
      satisfaction_score,
    } = req.body;

    const { rows: currentRows } = await pool.query(
      `SELECT c.*, p.first_name, p.last_name, p.email, p.phone, f.flight_number AS db_flight_number
         FROM complaints c
         JOIN passengers p ON c.passenger_id = p.passenger_id
         JOIN flights f ON c.flight_id = f.flight_id
        WHERE c.complaint_id = $1`,
      [req.params.id]
    );

    if (currentRows.length === 0) return res.status(404).json({ error: 'Complaint not found' });

    const current = currentRows[0];

    if (!namesMatch(passenger_name, current.first_name, current.last_name)) {
      return res.status(409).json({ error: 'Passenger identity mismatch: name does not match complaint record' });
    }
    if (!passenger_email && !passenger_phone) {
      return res.status(400).json({ error: 'Provide passenger_email or passenger_phone to validate identity' });
    }
    if (passenger_email && (!current.email || current.email.toLowerCase() !== String(passenger_email).toLowerCase())) {
      return res.status(409).json({ error: 'Passenger identity mismatch: email does not match complaint record' });
    }
    if (passenger_phone && current.phone !== passenger_phone) {
      return res.status(409).json({ error: 'Passenger identity mismatch: phone does not match complaint record' });
    }
    if (flight_id !== undefined && Number(flight_id) !== Number(current.flight_id)) {
      return res.status(409).json({ error: 'Flight mismatch: flight_id does not match complaint record' });
    }
    if (flight_number && String(flight_number).trim() !== current.db_flight_number) {
      return res.status(409).json({ error: 'Flight mismatch: flight_number does not match complaint record' });
    }
    if (!pnr || String(pnr).trim() !== current.pnr) {
      return res.status(409).json({ error: 'Ticket mismatch: pnr does not match complaint record' });
    }

    // Build dynamic SET clause
    const sets = [];
    const params = [];
    if (status !== undefined) {
      params.push(status);
      sets.push(`status = $${params.length}`);
      // Auto-set resolution_date when resolving/closing
      if (['Resolved', 'Closed'].includes(status)) {
        sets.push(`resolution_date = NOW()`);
      }
    }
    if (severity !== undefined) {
      params.push(severity);
      sets.push(`severity = $${params.length}`);
    }
    if (assigned_agent !== undefined) {
      params.push(assigned_agent);
      sets.push(`assigned_agent = $${params.length}`);
    }
    if (resolution_notes !== undefined) {
      params.push(resolution_notes);
      sets.push(`resolution_notes = $${params.length}`);
    }
    if (satisfaction_score !== undefined) {
      params.push(satisfaction_score);
      sets.push(`satisfaction_score = $${params.length}`);
    }

    if (sets.length === 0) return res.status(400).json({ error: 'No fields to update' });

    params.push(req.params.id);
    const { rows } = await pool.query(
      `UPDATE complaints SET ${sets.join(', ')} WHERE complaint_id = $${params.length} RETURNING *`,
      params
    );

    if (rows.length === 0) return res.status(404).json({ error: 'Complaint not found' });

    if (status !== undefined) {
      const statusUpdateQuery = ['Resolved', 'Closed'].includes(status)
        ? `UPDATE cases
              SET case_status = $1,
                  last_updated_at = NOW(),
                  closed_at = NOW()
            WHERE case_id = $2`
        : `UPDATE cases
              SET case_status = $1,
                  last_updated_at = NOW(),
                  closed_at = NULL
            WHERE case_id = $2`;
      await pool.query(statusUpdateQuery, [status, current.case_id]);
    } else {
      await pool.query(
        `UPDATE cases
            SET last_updated_at = NOW()
          WHERE case_id = $1`,
        [current.case_id]
      );
    }

    res.json(rows[0]);
  } catch (err) {
    console.error('PUT /api/complaints/:id error:', err);
    res.status(500).json({ error: 'Failed to update complaint' });
  }
});

// ── GET /api/passengers (dropdown data) ─────────────────────────────────────

app.get('/api/passengers', async (_req, res) => {
  try {
    const { rows } = await pool.query(`
      SELECT passenger_id, first_name, last_name,
             first_name || ' ' || last_name AS full_name,
             frequent_flyer_tier, country
        FROM passengers
       ORDER BY last_name, first_name
    `);
    res.json(rows);
  } catch (err) {
    console.error('GET /api/passengers error:', err);
    res.status(500).json({ error: 'Failed to fetch passengers' });
  }
});

// ── GET /api/flights (dropdown data) ─────────────────────────────────────────

app.get('/api/flights', async (_req, res) => {
  try {
    const { rows } = await pool.query(`
      SELECT flight_id, flight_number,
             origin_code || ' → ' || destination_code AS route,
             origin_city, destination_city,
             flight_status, scheduled_departure
        FROM flights
       ORDER BY flight_number
    `);
    res.json(rows);
  } catch (err) {
    console.error('GET /api/flights error:', err);
    res.status(500).json({ error: 'Failed to fetch flights' });
  }
});

// ── POST /api/ivr/complaint ─────────────────────────────────────────────────
//
// Called by the Logic App (or manually) with IVR-collected data.
//
// IVR payload shape:
//   call_id               – IVR session identifier (e.g. "IVR-2026-0302-001")
//   caller_phone          – Caller's phone number → used to look up passenger
//   flight_number         – Flight number the call is about (e.g. "LA101")
//   ivr_category          – Digit pressed on the category menu  (1–8)
//   ivr_subcategory       – Digit pressed on the subcategory menu (1–4)
//   call_transcript       – Speech-to-text transcription of the caller's complaint
//   call_duration_seconds – Optional: total call length in seconds

app.post('/api/ivr/complaint', async (req, res) => {
  try {
    const {
      call_id,
      caller_phone,
      flight_number,
      ivr_category,
      ivr_subcategory,
      pnr,
      call_transcript,
      call_duration_seconds,
    } = req.body;

    // Validate required IVR fields
    const missing = ['caller_phone', 'flight_number', 'pnr', 'ivr_category', 'ivr_subcategory', 'call_transcript']
      .filter(f => !req.body[f]);
    if (missing.length) {
      return res.status(400).json({ error: 'Missing required IVR fields', missing });
    }

    // Resolve category / subcategory from IVR menu digits
    const menuEntry = IVR_MENU[String(ivr_category)];
    if (!menuEntry) {
      return res.status(400).json({ error: `Invalid ivr_category: ${ivr_category}. Valid range: 1–8` });
    }
    const subcategoryName = menuEntry.subs[String(ivr_subcategory)];
    if (!subcategoryName) {
      return res.status(400).json({ error: `Invalid ivr_subcategory: ${ivr_subcategory} for category ${ivr_category}` });
    }

    // Resolve passenger by phone number
    const pRes = await pool.query(
      'SELECT passenger_id, frequent_flyer_tier FROM passengers WHERE phone = $1 LIMIT 1',
      [caller_phone]
    );
    if (pRes.rows.length === 0) {
      return res.status(404).json({ error: `No passenger found for phone: ${caller_phone}` });
    }
    const { passenger_id, frequent_flyer_tier } = pRes.rows[0];

    // Resolve flight by flight number
    const fRes = await pool.query(
      'SELECT flight_id FROM flights WHERE flight_number = $1 LIMIT 1',
      [flight_number]
    );
    if (fRes.rows.length === 0) {
      return res.status(404).json({ error: `No flight found for flight number: ${flight_number}` });
    }
    const { flight_id } = fRes.rows[0];

    // Infer severity from category, subcategory, and frequent-flyer tier
    const severity = inferSeverity(menuEntry.name, subcategoryName, frequent_flyer_tier);

    // Round-robin agent assignment based on current complaint count
    const countRes = await pool.query('SELECT COALESCE(MAX(complaint_id), 0) AS last_id FROM complaints');
    const agentIndex = parseInt(countRes.rows[0].last_id, 10) % AGENTS.length;
    const assigned_agent = AGENTS[agentIndex];

    const caseResult = await findOrCreateCase(passenger_id, flight_id, flight_number, String(pnr).trim());

    const nextId = await getNextId('complaints', 'complaint_id');

    // Prefix description with IVR call metadata so agents see the source
    const callMeta = call_id
      ? `[IVR ${call_id}${call_duration_seconds ? ` · ${call_duration_seconds}s` : ''}] `
      : '[IVR] ';
    const description = callMeta + call_transcript;

    const { rows } = await pool.query(`
      INSERT INTO complaints
        (complaint_id, case_id, passenger_id, flight_id, flight_number, pnr, complaint_date,
         category, subcategory, description, severity, status, assigned_agent)
      VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9, $10, 'Open', $11)
      RETURNING *
    `, [
      nextId,
      caseResult.caseRow.case_id,
      passenger_id,
      flight_id,
      flight_number,
      String(pnr).trim(),
      menuEntry.name,
      subcategoryName,
      description,
      severity,
      assigned_agent,
    ]);

    await pool.query(
      `UPDATE cases
          SET last_updated_at = NOW()
        WHERE case_id = $1`,
      [caseResult.caseRow.case_id]
    );

    res.status(201).json({
      complaint: rows[0],
      ivr_resolved: {
        passenger_id,
        flight_id,
        category: menuEntry.name,
        subcategory: subcategoryName,
        severity,
        assigned_agent,
        pnr,
        case_id: caseResult.caseRow.case_id,
        is_existing_case: caseResult.isExistingCase,
      },
    });
  } catch (err) {
    console.error('POST /api/ivr/complaint error:', err);
    res.status(500).json({ error: 'Failed to create IVR complaint' });
  }
});

// ── GET /api/categories ─────────────────────────────────────────────────────

app.get('/api/categories', (_req, res) => {
  res.json(CATEGORIES);
});

// ── GET /api/agents ─────────────────────────────────────────────────────────

app.get('/api/agents', (_req, res) => {
  res.json(AGENTS);
});

// ── Fallback: serve index.html for SPA ──────────────────────────────────────

app.get('*', (_req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// ── Start server ────────────────────────────────────────────────────────────

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Zava Air Complaints API running on http://localhost:${PORT}`);
});
