#!/usr/bin/env python3
"""Install the local Aily-Copilot Obsidian plugin into the configured vault.

Origin: Created by Codex lead agent on 2026-05-23.
"""

from __future__ import annotations

import shutil
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aily.config import SETTINGS


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    source = repo_root / "obsidian-plugin" / "aily-copilot"
    vault = Path(SETTINGS.obsidian_vault_path or SETTINGS.dikiwi_vault_path).expanduser().resolve()
    target = vault / ".obsidian" / "plugins" / "aily-copilot"
    if not source.is_dir():
        raise SystemExit(f"Missing plugin source: {source}")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "main.js", "styles.css", "README.md"):
        shutil.copy2(source / name, target / name)
    enabled_path = vault / ".obsidian" / "community-plugins.json"
    enabled_plugins = _read_enabled_plugins(enabled_path)
    if "aily-copilot" not in enabled_plugins:
        enabled_plugins.append("aily-copilot")
        enabled_path.write_text(json.dumps(enabled_plugins, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)
    return 0


def _read_enabled_plugins(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


if __name__ == "__main__":
    raise SystemExit(main())
