"""Tests for the `adloop init` wizard helpers in `adloop.cli`."""

from __future__ import annotations

import yaml

from adloop.cli import _generate_config_yaml


class TestGenerateConfigYaml:
    """YAML generation in the init wizard must produce parseable output."""

    def _generate(self, **overrides):
        defaults = {
            "project_id": "",
            "credentials_path": "",
            "property_id": "123456789",
            "developer_token": "abc123",
            "customer_id": "123-456-7890",
            "login_customer_id": "987-654-3210",
            "max_daily_budget": 50.0,
            "require_dry_run": True,
        }
        defaults.update(overrides)
        return _generate_config_yaml(**defaults)

    def test_windows_credentials_path_parses(self):
        """Regression for Windows backslash paths breaking YAML parsing.

        Previously the wizard wrote `credentials_path: "c:\\Users\\..."` which
        YAML interpreted as a `\\U` Unicode escape sequence and raised
        ScannerError. Single quotes treat backslashes literally.
        """
        win_path = r"c:\Users\user\.adloop\credentials.json"
        text = self._generate(credentials_path=win_path)
        parsed = yaml.safe_load(text)
        assert parsed["google"]["credentials_path"] == win_path

    def test_posix_credentials_path_parses(self):
        posix_path = "/home/user/.adloop/credentials.json"
        text = self._generate(credentials_path=posix_path)
        parsed = yaml.safe_load(text)
        assert parsed["google"]["credentials_path"] == posix_path

    def test_path_with_embedded_apostrophe_parses(self):
        """YAML single-quoted strings escape `'` by doubling — make sure we do."""
        weird_path = r"c:\Users\o'brien\.adloop\credentials.json"
        text = self._generate(credentials_path=weird_path)
        parsed = yaml.safe_load(text)
        assert parsed["google"]["credentials_path"] == weird_path

    def test_no_credentials_path_comment(self):
        text = self._generate(credentials_path="")
        assert "credentials_path resolved from" in text
        parsed = yaml.safe_load(text)
        assert "credentials_path" not in parsed.get("google", {})

class TestToolsetSnippets:
    """MCP client snippets must carry ADLOOP_TOOLSETS when a subset is chosen."""

    def test_json_snippets_without_toolsets_have_no_env(self):
        import json

        from adloop.cli import _generate_claude_json_snippet, _generate_cursor_snippet

        for snippet in (_generate_cursor_snippet(), _generate_claude_json_snippet()):
            parsed = json.loads(snippet)
            assert "env" not in parsed["mcpServers"]["adloop"]

    def test_json_snippets_with_toolsets_are_valid_and_carry_env(self):
        import json

        from adloop.cli import _generate_claude_json_snippet, _generate_cursor_snippet

        for snippet in (
            _generate_cursor_snippet("ads,ga4"),
            _generate_claude_json_snippet("ads,ga4"),
        ):
            parsed = json.loads(snippet)
            assert parsed["mcpServers"]["adloop"]["env"] == {
                "ADLOOP_TOOLSETS": "ads,ga4"
            }

    def test_claude_code_command_injects_env_before_the_separator(self):
        from adloop.cli import _generate_claude_code_snippet

        plain = _generate_claude_code_snippet()
        assert "ADLOOP_TOOLSETS" not in plain

        cmd = _generate_claude_code_snippet("ads,ga4")
        assert "--env ADLOOP_TOOLSETS=ads,ga4" in cmd
        assert cmd.index("--env") < cmd.index(" -- ")


class TestPromptToolsets:
    def test_default_is_the_full_catalog(self, monkeypatch):
        from adloop import cli

        answers = iter([""])  # Enter on "Expose the full toolset?" [Y/n]
        monkeypatch.setattr("builtins.input", lambda *a: next(answers))
        assert cli._prompt_toolsets() == ""

    def test_invalid_slugs_reprompt_then_normalize(self, monkeypatch):
        from adloop import cli

        answers = iter(["n", "ads, bogus", "ADS, ga4, ads"])
        monkeypatch.setattr("builtins.input", lambda *a: next(answers))
        # Deduped, lowercased, comma-joined — the exact env-var format.
        assert cli._prompt_toolsets() == "ads,ga4"
