-- Phase D: Supabase-backed PlanStore + AuditSink for the hosted AdLoop server.
--
-- Lives in a dedicated `gads` schema in the shared Client Brain Supabase
-- Postgres (staging = Motivent Staging; prod = Client Brain). The hosted
-- server connects as the pooled Postgres role over the transaction pooler
-- (port 6543), so it reaches these tables directly — NOT through PostgREST.
-- The `gads` schema is intentionally NOT added to Supabase's exposed schemas,
-- so it is never reachable via the auto REST/GraphQL API.
--
-- Apply once per environment (staging first, then prod):
--   psql "$ADLOOP_DATABASE_URL" -f migrations/0001_gads_datastore.sql

create schema if not exists gads;

-- Pending two-phase-apply plans, keyed (tenant, plan_id). A plan is upserted:
-- confirm_and_apply re-stores it after a dry-run pass to persist
-- dry_run_result before a real write is allowed.
create table if not exists gads.change_plans (
    tenant                  text        not null,
    plan_id                 text        not null,
    operation               text        not null default '',
    entity_type             text        not null default '',
    entity_id               text        not null default '',
    customer_id             text        not null default '',
    changes                 jsonb       not null default '{}'::jsonb,
    created_at              timestamptz not null default now(),
    requires_double_confirm boolean     not null default false,
    dry_run_result          jsonb,
    primary key (tenant, plan_id)
);

-- Append-only audit of every mutation attempt (dry-run and real).
create table if not exists gads.mutation_audit (
    id          bigint generated always as identity primary key,
    tenant      text        not null,
    "timestamp" timestamptz not null default now(),
    operation   text        not null,
    customer_id text        not null default '',
    entity_type text        not null default '',
    entity_id   text        not null default '',
    changes     jsonb       not null default '{}'::jsonb,
    dry_run     boolean     not null default true,
    result      text        not null default 'success',
    error       text        not null default ''
);

create index if not exists mutation_audit_tenant_ts
    on gads.mutation_audit (tenant, "timestamp" desc);

-- Defense in depth: enable RLS with no policies. The server connects as the
-- table owner / pooled Postgres role, which bypasses RLS; any future
-- app-role/PostgREST access is denied by default until an explicit policy is
-- added.
alter table gads.change_plans   enable row level security;
alter table gads.mutation_audit enable row level security;
