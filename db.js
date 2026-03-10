/**
 * PostgreSQL connection pool for Azure Flexible Server.
 * Uses pg.Pool with SSL required (mandatory for Azure PG Flex).
 */
const { Pool } = require('pg');
require('dotenv').config();

const schema = process.env.DB_SCHEMA || 'custcomplaints';

const pool = new Pool({
  host: process.env.DB_HOST,
  port: parseInt(process.env.DB_PORT, 10) || 5432,
  database: process.env.DB_NAME,
  user: process.env.DB_USER,
  password: process.env.DB_PASSWORD,
  ssl: process.env.DB_SSL === 'true' ? { rejectUnauthorized: false } : false,
  max: 10,
  idleTimeoutMillis: 30000,
  connectionTimeoutMillis: 10000,
  // Set search_path as a startup parameter to avoid the pg@9 deprecation
  // (calling client.query() inside pool.on('connect') is deprecated)
  options: `-c search_path=${schema},public`,
});

pool.on('error', (err) => {
  console.error('Unexpected PostgreSQL pool error:', err);
});

module.exports = pool;
