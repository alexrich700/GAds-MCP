"""Tests for the Phase D hosting shell: Supabase-backed PlanStore + AuditSink.

Uses an in-memory fake connection that mimics just enough of psycopg's
``execute(sql, params).fetchone()`` surface to exercise the SQL round-trips —
no real database or ``psycopg`` install required.
"""

from contextlib import contextmanager

import pytest

from adloop.hosting.datastore import (
    SupabaseAuditSink,
    SupabasePlanStore,
    build_connection_provider,
    install_datastore,
)
from adloop.safety.preview import ChangePlan

# Column order the plan SELECT reads back (see datastore._PLAN_COLUMNS).
_INSERT_TO_ROW = (
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


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDB:
    """Tiny SQL-shaped fake: upsert/select/delete on change_plans, insert audit."""

    def __init__(self):
        self.plans = {}  # (tenant, plan_id) -> row dict
        self.audit = []  # list of param tuples

    @contextmanager
    def connect(self):
        yield self

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        if s.startswith("insert into gads.change_plans"):
            tenant = params[0]
            # params[1:] line up with _INSERT_TO_ROW; jsonb/timestamptz come in
            # as strings here, exercising datastore's str-tolerant reconstruction.
            row = dict(zip(_INSERT_TO_ROW, params[1:]))
            self.plans[(tenant, row["plan_id"])] = row
            return _FakeCursor(None)
        if s.startswith("select") and "from gads.change_plans" in s:
            return _FakeCursor(self.plans.get((params[0], params[1])))
        if s.startswith("delete from gads.change_plans"):
            self.plans.pop((params[0], params[1]), None)
            return _FakeCursor(None)
        if s.startswith("insert into gads.mutation_audit"):
            self.audit.append(params)
            return _FakeCursor(None)
        raise AssertionError(f"unexpected SQL: {s}")


def test_plan_round_trip():
    db = _FakeDB()
    store = SupabasePlanStore(db.connect)
    plan = ChangePlan(
        plan_id="abc123",
        operation="update_budget",
        entity_type="campaign",
        entity_id="c-1",
        customer_id="123",
        changes={"budget": 500},
        requires_double_confirm=True,
    )
    store.store("tenant-A", plan)
    got = store.get("tenant-A", "abc123")
    assert got is not None
    assert got.operation == "update_budget"
    assert got.changes == {"budget": 500}  # jsonb string -> dict
    assert got.requires_double_confirm is True


def test_plan_scoped_by_tenant():
    db = _FakeDB()
    store = SupabasePlanStore(db.connect)
    store.store("tenant-A", ChangePlan(plan_id="p1"))
    # A different tenant cannot see tenant-A's plan, even with the right id.
    assert store.get("tenant-B", "p1") is None


def test_plan_upsert_overwrites_with_dry_run_result():
    db = _FakeDB()
    store = SupabasePlanStore(db.connect)
    store.store("t", ChangePlan(plan_id="p", operation="op"))
    store.store("t", ChangePlan(plan_id="p", operation="op", dry_run_result={"ok": True}))
    got = store.get("t", "p")
    assert got.dry_run_result == {"ok": True}
    assert len(db.plans) == 1  # overwrite, not a second row


def test_plan_remove():
    db = _FakeDB()
    store = SupabasePlanStore(db.connect)
    store.store("t", ChangePlan(plan_id="p"))
    store.remove("t", "p")
    assert store.get("t", "p") is None


def test_audit_record_writes_row():
    db = _FakeDB()
    sink = SupabaseAuditSink(db.connect)
    sink.record(
        {
            "tenant": "t",
            "timestamp": "2026-07-27T00:00:00+00:00",
            "operation": "update_budget",
            "customer_id": "123",
            "changes": {"budget": 500},
            "dry_run": False,
            "result": "success",
        },
        log_file="ignored.log",
    )
    assert len(db.audit) == 1
    assert db.audit[0][0] == "t"  # tenant is first param


def test_build_provider_none_without_env(monkeypatch):
    monkeypatch.delenv("ADLOOP_DATABASE_URL", raising=False)
    assert build_connection_provider() is None


def test_build_provider_returns_callable_with_env(monkeypatch):
    # A DSN is enough to get a provider; the pool is built lazily on first call,
    # so this does not require psycopg or touch a network.
    monkeypatch.setenv("ADLOOP_DATABASE_URL", "postgresql://u:p@host:6543/db")
    assert callable(build_connection_provider())


def test_install_falls_back_without_config(monkeypatch):
    monkeypatch.delenv("ADLOOP_DATABASE_URL", raising=False)
    assert install_datastore() is False


def test_install_sets_global_store_and_sink():
    from adloop.safety import audit, preview

    prior_store = preview.get_plan_store()
    prior_sink = audit.get_audit_sink()
    db = _FakeDB()
    try:
        assert install_datastore(connect=db.connect) is True
        assert isinstance(preview.get_plan_store(), SupabasePlanStore)
        assert isinstance(audit.get_audit_sink(), SupabaseAuditSink)
    finally:
        preview.set_plan_store(prior_store)
        audit.set_audit_sink(prior_sink)
