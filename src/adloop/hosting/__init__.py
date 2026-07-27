"""AdLoop hosting shell — the pieces that turn the embeddable ``mcp`` server
into a hosted, multi-tenant service (Supabase auth, per-tenant config, and,
in later phases, the Supabase-backed credentials provider / plan store /
audit sink).

Everything here plugs into seams the merged upstream already provides
(``set_deployment_mode``, ``use_runtime``, ``set_credentials_provider``,
``set_plan_store``, ``set_audit_sink``). Importing this package is
side-effect-free; wiring happens from :mod:`adloop.asgi`.
"""
