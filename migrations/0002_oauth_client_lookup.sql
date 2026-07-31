-- Connector redirect_uri lookup for the hosted server's client pinning.
--
-- Supabase uses dynamic client registration, so every user who adds this
-- connector gets their own client_id — 30 of them on prod, one per user. A
-- static ADLOOP_EXPECTED_CLIENT_ID therefore rejects everyone but the single
-- user named in the env var. The redirect_uri IS stable across all of those
-- registrations, so that is what the server pins on instead.
--
-- SECURITY DEFINER so the server's DB role never needs read access to the auth
-- schema; it can resolve one client_id at a time and learn nothing else.
--
-- The canonical tracked copy of this lives in the ClientBrain repo as
-- supabase/migrations/*_client_brain_gads_oauth_client_lookup.sql — this file
-- is kept in step for local dev and for reading alongside the server code.

create schema if not exists gads;

-- plpgsql, not sql: a plpgsql body resolves its object names at runtime, so this
-- file still applies cleanly to an environment where auth.oauth_clients is not
-- present (the PG17 release rehearsal). A `language sql` body would be parsed at
-- creation time and fail there.
create or replace function gads.oauth_client_redirect_uris(p_client_id uuid)
returns text
language plpgsql
security definer
stable
set search_path = pg_catalog, auth
as $$
declare
  v_uris text;
begin
  if p_client_id is null then
    return null;
  end if;
  select redirect_uris into v_uris from auth.oauth_clients where id = p_client_id;
  return v_uris;
end;
$$;

revoke all on function gads.oauth_client_redirect_uris(uuid)
  from public, anon, authenticated;
grant execute on function gads.oauth_client_redirect_uris(uuid)
  to service_role, postgres;

-- The least-privilege server role, when it exists (see the ClientBrain repo's
-- adloop_server role). Guarded so this file still applies to a project that
-- has not created it yet.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'adloop_server') then
    execute 'grant execute on function gads.oauth_client_redirect_uris(uuid) to adloop_server';
  end if;
end
$$;

comment on function gads.oauth_client_redirect_uris(uuid) is
  'Returns the registered redirect_uris for one Supabase OAuth client id (NULL if unknown). Lets the hosted MCP server pin connectors on a value that is stable across dynamic registration, without granting it the auth schema.';
