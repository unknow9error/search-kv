#!/bin/sh
set -eu

# Only runs for a new PostgreSQL volume. psql quotes passwords as SQL literals.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv meken_app_password MEKEN_APP_DB_PASSWORD
\getenv meken_migrator_password MEKEN_MIGRATOR_DB_PASSWORD
CREATE ROLE meken_app LOGIN PASSWORD :'meken_app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE meken_migrator LOGIN PASSWORD :'meken_migrator_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER DATABASE meken OWNER TO meken_migrator;
REVOKE ALL ON DATABASE meken FROM PUBLIC;
GRANT CONNECT ON DATABASE meken TO meken_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO meken_migrator;
GRANT USAGE ON SCHEMA public TO meken_app;
ALTER DEFAULT PRIVILEGES FOR ROLE meken_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO meken_app;
ALTER DEFAULT PRIVILEGES FOR ROLE meken_migrator IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO meken_app;
SQL
