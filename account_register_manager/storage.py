from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JSONStorage:
    def __init__(self, accounts_file: Path):
        self.accounts_file = accounts_file
        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)

    def load_accounts(self) -> list[dict[str, Any]]:
        if not self.accounts_file.exists():
            return []
        try:
            data = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def save_accounts(self, accounts: list[dict[str, Any]]) -> None:
        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
        self.accounts_file.write_text(
            json.dumps(accounts, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

