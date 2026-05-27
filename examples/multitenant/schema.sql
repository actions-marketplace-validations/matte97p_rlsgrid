-- Rich multi-tenant SaaS schema for exercising rlsgrid end to end.
--
-- Covers: enum types, composite primary keys, CHECK constraints, an FK chain
-- (orgs ← projects ← tasks), a table with RLS disabled (unrestricted), a
-- table reachable only by service_role (deny for authenticated), and several
-- CONDITIONAL policies gated by the org claim in the JWT.
--
-- Run against a fresh Postgres 15+ with pgcrypto available.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS auth;

-- Supabase-style helpers, faked for a plain Postgres.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('request.jwt.claims', true)::json->>'sub', '')::uuid
$$;

CREATE OR REPLACE FUNCTION public.current_org() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('request.jwt.claims', true)::json->>'org_id', '')::uuid
$$;

DO $$ BEGIN CREATE ROLE anon NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE service_role NOLOGIN BYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TYPE public.task_status AS ENUM ('open', 'in_progress', 'done', 'archived');

-- Root tenant.
CREATE TABLE public.orgs (
  id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name  text NOT NULL DEFAULT ''
);

-- Composite-PK join table (user ↔ org).
CREATE TABLE public.memberships (
  user_id  uuid NOT NULL,
  org_id   uuid NOT NULL REFERENCES public.orgs(id),
  role     text NOT NULL DEFAULT 'member',
  PRIMARY KEY (user_id, org_id)
);

-- FK to orgs, enum column.
CREATE TABLE public.projects (
  id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id  uuid NOT NULL REFERENCES public.orgs(id),
  name    text NOT NULL DEFAULT '',
  status  public.task_status NOT NULL DEFAULT 'open'
);

-- FK chain + CHECK constraint + enum.
CREATE TABLE public.tasks (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id  uuid NOT NULL REFERENCES public.projects(id),
  org_id      uuid NOT NULL REFERENCES public.orgs(id),
  title       text NOT NULL,
  priority    int  NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
  status      public.task_status NOT NULL DEFAULT 'open'
);

-- RLS intentionally NOT enabled — rlsgrid should flag this as UNRESTRICTED.
CREATE TABLE public.audit_log (
  id      bigserial PRIMARY KEY,
  org_id  uuid,
  event   text NOT NULL DEFAULT ''
);

-- Reachable only by service_role — DENY for authenticated/anon.
CREATE TABLE public.billing (
  id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id  uuid NOT NULL REFERENCES public.orgs(id),
  amount  numeric NOT NULL DEFAULT 0
);

ALTER TABLE public.orgs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.projects    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tasks       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.billing     ENABLE ROW LEVEL SECURITY;
-- audit_log left without RLS on purpose.

-- CONDITIONAL: row visible only for the caller's org.
CREATE POLICY orgs_self ON public.orgs
  FOR SELECT TO authenticated USING (id = public.current_org());

CREATE POLICY memberships_in_org ON public.memberships
  FOR ALL TO authenticated
  USING (org_id = public.current_org())
  WITH CHECK (org_id = public.current_org());

CREATE POLICY projects_in_org ON public.projects
  FOR ALL TO authenticated
  USING (org_id = public.current_org())
  WITH CHECK (org_id = public.current_org());

CREATE POLICY tasks_in_org ON public.tasks
  FOR ALL TO authenticated
  USING (org_id = public.current_org())
  WITH CHECK (org_id = public.current_org());

-- billing: only service_role, via BYPASSRLS. No authenticated policy → DENY.
CREATE POLICY billing_service ON public.billing
  FOR ALL TO service_role USING (true) WITH CHECK (true);

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT SELECT ON public.orgs, public.projects, public.tasks, public.memberships TO anon, authenticated;
GRANT INSERT, UPDATE, DELETE ON public.projects, public.tasks, public.memberships TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated, service_role;
