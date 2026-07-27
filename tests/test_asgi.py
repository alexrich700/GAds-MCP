"""Tests for the hosted HTTP/ASGI entry point (adloop.asgi)."""

from adloop import runtime


def test_import_is_side_effect_free():
    """Importing the ASGI module must not flip the process into server mode."""
    import adloop.asgi  # noqa: F401

    assert runtime.deployment_mode() == "local"


def test_create_app_enables_server_mode_and_returns_asgi():
    """create_app() builds an ASGI app and switches to server mode."""
    import adloop.asgi as asgi

    prior = runtime.deployment_mode()
    try:
        app = asgi.create_app()
        assert app is not None
        assert callable(app)  # ASGI apps are callables
        assert runtime.deployment_mode() == "server"
    finally:
        runtime.set_deployment_mode(prior)


def test_env_list_parsing(monkeypatch):
    import adloop.asgi as asgi

    monkeypatch.delenv("ADLOOP_ALLOWED_HOSTS", raising=False)
    assert asgi._env_list("ADLOOP_ALLOWED_HOSTS") is None

    monkeypatch.setenv("ADLOOP_ALLOWED_HOSTS", " a.example.com , b.example.com ,")
    assert asgi._env_list("ADLOOP_ALLOWED_HOSTS") == ["a.example.com", "b.example.com"]


def test_transport_kwargs_only_includes_set_env(monkeypatch):
    import adloop.asgi as asgi

    monkeypatch.delenv("ADLOOP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("ADLOOP_ALLOWED_ORIGINS", raising=False)
    assert asgi._transport_kwargs() == {}

    monkeypatch.setenv("ADLOOP_ALLOWED_ORIGINS", "https://claude.ai")
    assert asgi._transport_kwargs() == {"allowed_origins": ["https://claude.ai"]}
