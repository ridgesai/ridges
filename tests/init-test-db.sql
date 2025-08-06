-- Initialize test database with extensions and basic configuration
-- This script runs automatically when the Docker container starts

-- Create the test database
CREATE DATABASE ridges_test;

-- Connect to the test database
\c ridges_test;

-- Create extensions that might be needed
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Grant privileges to test user
GRANT ALL PRIVILEGES ON DATABASE ridges_test TO test_user;
GRANT ALL ON SCHEMA public TO test_user;

-- Set up basic configuration
ALTER DATABASE ridges_test SET timezone TO 'UTC';

-- Note: The production schema from postgres_schema.sql will be applied
-- by the test setup code to ensure we're testing against the exact
-- production database structure.