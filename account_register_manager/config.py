from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from account_register_manager.cliproxy_upload_service import normalize_upload_targets

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "config.json"


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class Config:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.data = _read_json_object(CONFIG_FILE)

    @property
    def auth_key(self) -> str:
        return str(os.getenv("ACCOUNT_REGISTER_AUTH_KEY") or self.data.get("auth_key") or "").strip()

    @property
    def image_account_concurrency(self) -> int:
        try:
            return max(1, int(self.data.get("image_account_concurrency", 3)))
        except Exception:
            return 3

    @property
    def auto_remove_invalid_accounts(self) -> bool:
        return bool(self.data.get("auto_remove_invalid_accounts", False))

    @property
    def auto_remove_rate_limited_accounts(self) -> bool:
        return bool(self.data.get("auto_remove_rate_limited_accounts", False))

    @property
    def outbound_proxy(self) -> str:
        return str(self.data.get("outbound_proxy") or "").strip()

    @property
    def cpa_secret_key(self) -> str:
        return str(self.data.get("cpa_secret_key") or self.auth_key or "").strip()

    @property
    def refresh_account_interval_minutes(self) -> int:
        try:
            return max(0, int(self.data.get("refresh_account_interval_minutes", 0)))
        except Exception:
            return 0

    @property
    def cliproxy_upload_targets(self) -> list[dict[str, Any]]:
        return normalize_upload_targets(self.data.get("cliproxy_upload_targets"))

    def get_public_settings(self) -> dict[str, Any]:
        return {
            "outbound_proxy": self.outbound_proxy,
            "image_account_concurrency": self.image_account_concurrency,
            "auto_remove_invalid_accounts": self.auto_remove_invalid_accounts,
            "auto_remove_rate_limited_accounts": self.auto_remove_rate_limited_accounts,
            "cpa_secret_key": self.cpa_secret_key,
            "refresh_account_interval_minutes": self.refresh_account_interval_minutes,
            "cliproxy_upload_targets": self.cliproxy_upload_targets,
        }

    def update(self, updates: dict[str, Any]) -> dict[str, Any]:
        next_data = dict(self.data)
        for key in (
            "outbound_proxy",
            "image_account_concurrency",
            "auto_remove_invalid_accounts",
            "auto_remove_rate_limited_accounts",
            "cpa_secret_key",
            "refresh_account_interval_minutes",
            "cliproxy_upload_targets",
        ):
            if key in updates:
                next_data[key] = normalize_upload_targets(updates[key]) if key == "cliproxy_upload_targets" else updates[key]
        self.data = next_data
        CONFIG_FILE.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.get_public_settings()


config = Config()
