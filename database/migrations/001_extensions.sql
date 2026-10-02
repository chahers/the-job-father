-- ---------------------------------------------------------------------------
-- 001_extensions.sql - extensions shared by every agent in this repository.
--
--   Applied by:  python -m jobfather_crawler db-init   (from web-crawler/)
--
-- NOTE ON PRIVILEGES
--   `CREATE EXTENSION vector` requires a SUPERUSER the first time, because
--   pgvector's vector.control is not marked `trusted`.  This machine's local
--   PostgreSQL 16.10 cluster already has pgvector 0.8.6 on disk
--   (%LOCALAPPDATA%\pgsql16\pgsql\lib\vector.dll), and
--   database/scripts/pg_setup.ps1 installs it as the `postgres` superuser.
--   After that, this statement is a no-op and runs fine as the `jobfather`
--   app role - which is why db-init can stay unprivileged.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS vector;