"""resolved_inbox_path precedence: explicit INBOX_PATH > vault inbox > default."""

from pathlib import Path

from aily.config import Settings


def test_explicit_inbox_path_wins_over_vault():
    # The cloud-drop model: INBOX_PATH points at a synced folder and must win
    # even when a vault is configured.
    s = Settings(_env_file=None, obsidian_vault_path="/tmp/v", inbox_path="/tmp/drop")
    assert s.resolved_inbox_path == Path("/tmp/drop")


def test_vault_inbox_used_when_no_explicit_override():
    s = Settings(_env_file=None, obsidian_vault_path="/tmp/v")
    assert s.resolved_inbox_path == Path("/tmp/v/00-Chaos/_inbox")


def test_default_inbox_when_no_vault_no_override():
    s = Settings(_env_file=None, obsidian_vault_path="", dikiwi_vault_path="")
    assert s.resolved_inbox_path == (Path.home() / "Aily" / "Inbox")
