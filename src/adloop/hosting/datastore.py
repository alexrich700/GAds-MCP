"""Supabase-backed PlanStore + AuditSink for server mode (Phase D).

Concrete implementations of the two upstream persistence seams:

  * ``PlanStore`` (``adloop.safety.preview``) — pending two-phase-apply plans,
    keyed ``(tenant, plan_id)`` so one tenant can never read or apply another's
    pending mutation.
  * ``AuditSink`` (``adloop.safety.audit``) — an append-only record of every
    mutation attempt.

Both persist to a shared ``gads`` schema in the Client Brain Supabase Postgres
(see ``migrations/0001_gads_datastore.sql``). Without them the hosted server
falls back to the in-process defaults (``InMemoryPlanStore`` / ``FileAuditSink``),
which lose state across Cloud Run instances and restarts — fine for local dev,
never for production.

Connection notes (Supabase serverless guidance):
  * Connect through the **transaction pooler (port 6543)**, not a direct 5432
    connection — Cloud Run is stateless and spins many short-lived instances.
  * Transaction-pooler mode does **not** support prepared statements, so the
    default pool disables them (``prepare_threshold=None``). Leaving them on
    surfaces intermittent ``prepared statement "..." already exists`` errors.

The DB access is injectable (a ``ConnectionProvider``) exactly like Phase C's
``TokenLookup``: tests pass a fake, and importing this module never opens a
connection or requires ``psycopg`` to be installed.

Env:
  ADLOOP_DATABASE_URL   Supabase transaction-pooler conninfo (port 6543).
  ADLOOP_DB_POOL_MAX    max pooled connections per instance (default 4).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import AbstractContextManager
from typing import Any, Callable

from adloop.safety.audit import set_audit_sink
from adloop.safety.preview import ChangePlan, set_plan_store

log = logging.getLogger("adloop.hosting.datastore")

# Yields a DB connection as a context manager. The connection must expose
# psycopg's ``execute(sql, params) -> cursor`` with ``fetchone()``; rows come
# back as mappings (the default provider sets ``row_factory=dict_row``).
ConnectionProvider = Callable[[], AbstractContextManager[Any]]

_PLAN_COLUMNS = (
    "plan_id",
    "operation",
    "entity_type",
    "entity_id",
    "customer_id",
    "changes",
    "created_at",
    "requires_double_confirm",
    "dry_run_result",
)


def _as_dict(value: Any) -> dict[str, Any]:
    """jsonb comes back parsed from psycopg but as text from a naive fake."""
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _as_iso(value: Any) -> str:
    """timestamptz comes back as datetime from psycopg, str from a fake."""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class SupabasePlanStore:
    """Tenant-scoped ``PlanStore`` backed by ``gads.change_plans``."""

    def __init__(self, connect: ConnectionProvider) -> None:
        self._connect = connect

    def store(self, tenant: str, plan: ChangePlan) -> None:
        # Upsert: confirm_and_apply re-stores the same (tenant, plan_id) after a
        # dry-run pass to persist dry_run_result, so this MUST overwrite.
        sql = """
            insert into gads.change_plans
                (tenant, plan_id, operation, entity_type, entity_id, customer_id,
                 changes, created_at, requires_double_confirm, dry_run_result)
            values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::timestamptz, %s, %s::jsonb)
            on conflict (tenant, plan_id) do update set
                operation = excluded.operation,
                entity_type = excluded.entity_type,
                entity_id = excluded.entity_id,
                customer_id = excluded.customer_id,
                changes = excluded.changes,
                created_at = excluded.created_at,
                requires_double_confirm = excluded.requires_double_confirm,
                dry_run_result = excluded.dry_run_result
        """
        dry_run = None if plan.dry_run_result is None else json.dumps(plan.dry_run_result)
        with self._connect() as conn:
            conn.execute(
                sql,
                (
                    tenant,
                    plan.plan_id,
                    plan.operation,
                    plan.entity_type,
                    plan.entity_id,
                    plan.customer_id,
                    json.dumps(plan.changes),
                    plan.created_at,
                    plan.requires_double_confirm,
                    dry_run,
                ),
            )

    def get(self, tenant: str, plan_id: str) -> ChangePlan | None:
        # Expired plans are treated as absent, so a stale dry-run-approved plan
        # can't be confirmed long after it was drafted (e.g. after the user
        # disconnected + reconnected). TTL is env-tunable (default 24h).
        try:
            ttl_hours = max(1, int(os.environ.get("ADLOOP_PLAN_TTL_HOURS", "24")))
        except ValueError:
            ttl_hours = 24
        sql = f"""
            select {", ".join(_PLAN_COLUMNS)}
            from gads.change_plans
            where tenant = %s and plan_id = %s
              and created_at > now() - make_interval(hours => %s)
        """
        with self._connect() as conn:
            row = conn.execute(sql, (tenant, plan_id, ttl_hours)).fetchone()
        if row is None:
            return None
        return ChangePlan(
            plan_id=row["plan_id"],
            operation=row["operation"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            customer_id=row["customer_id"],
            changes=_as_dict(row["changes"]),
            created_at=_as_iso(row["created_at"]),
            requires_double_confirm=row["requires_double_confirm"],
            dry_run_result=(
                None if row["dry_run_result"] is None else _as_dict(row["dry_run_result"])
            ),
        )

    def remove(self, tenant: str, plan_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "delete from gads.change_plans where tenant = %s and plan_id = %s",
                (tenant, plan_id),
            )


class SupabaseAuditSink:
    """Append-only ``AuditSink`` backed by ``gads.mutation_audit``.

    The DB sink ignores ``log_file`` (that's the file-sink's per-tenant path);
    the tenant is already carried inside ``entry``.
    """

    def __init__(self, connect: ConnectionProvider) -> None:
        self._connect = connect

    def record(self, entry: dict[str, Any], *, log_file: str) -> None:  # noqa: ARG002
        sql = """
            insert into gads.mutation_audit
                (tenant, "timestamp", operation, customer_id, entity_type,
                 entity_id, changes, dry_run, result, error)
            values (%s, %s::timestamptz, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
        """
        with self._connect() as conn:
            conn.execute(
                sql,
                (
                    entry.get("tenant", ""),
                    entry.get("timestamp"),
                    entry.get("operation", ""),
                    entry.get("customer_id", ""),
                    entry.get("entity_type", ""),
                    entry.get("entity_id", ""),
                    json.dumps(entry.get("changes") or {}),
                    entry.get("dry_run", True),
                    entry.get("result", "success"),
                    entry.get("error", ""),
                ),
            )


# --- default psycopg-backed connection provider -----------------------------

_pool: Any = None
_pool_lock = threading.Lock()


def _get_pool(dsn: str) -> Any:
    """Lazily build one process-wide connection pool (psycopg imported here)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg.rows import dict_row
                from psycopg_pool import ConnectionPool

                pool = ConnectionPool(
                    conninfo=dsn,
                    # prepare_threshold=None: transaction-pooler mode rejects
                    # prepared statements. autocommit: each op is a single stmt.
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": None,
                        "row_factory": dict_row,
                    },
                    min_size=0,
                    max_size=int(os.environ.get("ADLOOP_DB_POOL_MAX", "4")),
                    open=False,
                )
                pool.open()
                _pool = pool
    return _pool


def build_connection_provider() -> ConnectionProvider | None:
    """Return a psycopg-pool-backed provider, or None if ``ADLOOP_DATABASE_URL``
    is unset (local dev — falls back to the in-memory / file defaults)."""
    dsn = os.environ.get("ADLOOP_DATABASE_URL", "").strip()
    if not dsn:
        return None

    def provider() -> AbstractContextManager[Any]:
        return _get_pool(dsn).connection()

    return provider


def install_datastore(connect: ConnectionProvider | None = None) -> bool:
    """Install the Supabase-backed plan store + audit sink if a DB is configured.

    Returns True if the persistent datastore was installed, False if the server
    is falling back to the in-process defaults (no ``ADLOOP_DATABASE_URL`` and
    no injected provider — local dev only).
    """
    connect = connect or build_connection_provider()
    if connect is None:
        log.warning(
            "ADLOOP_DATABASE_URL not set — using the in-memory plan store and "
            "file audit sink. Pending plans and audit logs are lost across Cloud "
            "Run instances/restarts. For local dev only; never for production."
        )
        return False

    set_plan_store(SupabasePlanStore(connect))
    set_audit_sink(SupabaseAuditSink(connect))
    log.info("Supabase plan store + audit sink installed.")
    return True
