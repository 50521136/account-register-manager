from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from account_register_manager.cliproxy_upload_service import normalize_upload_targets

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "config.json"


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(maximum, max(minimum, int(value)))
    except (TypeError, ValueError):
        return default


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
    def outbound_proxy_source(self) -> str:
        value = str(self.data.get("outbound_proxy_source") or "custom").strip().lower()
        return value if value in {"custom", "pool"} else "custom"

    @property
    def outbound_proxy_id(self) -> str:
        return str(self.data.get("outbound_proxy_id") or "").strip()

    @property
    def outbound_proxy_pool_mode(self) -> str:
        value = str(self.data.get("outbound_proxy_pool_mode") or "selected").strip().lower()
        return value if value in {"selected", "random", "round_robin"} else "selected"

    @property
    def outbound_proxy(self) -> str:
        return self.resolve_outbound_proxy()

    def resolve_outbound_proxy(self) -> str:
        custom = str(self.data.get("outbound_proxy") or "").strip()
        if self.outbound_proxy_source != "pool":
            return custom
        from account_register_manager.proxy_pool_service import proxy_pool_service

        try:
            return proxy_pool_service.pick(
                mode=self.outbound_proxy_pool_mode,
                proxy_id=self.outbound_proxy_id,
            )
        except RuntimeError:
            return ""

    @property
    def flaresolverr_enabled(self) -> bool:
        environment = os.getenv("ACCOUNT_REGISTER_FLARESOLVERR_ENABLED")
        return _bool_value(environment, _bool_value(self.data.get("flaresolverr_enabled"), False))

    @property
    def flaresolverr_url(self) -> str:
        value = os.getenv("ACCOUNT_REGISTER_FLARESOLVERR_URL") or self.data.get("flaresolverr_url") or ""
        return str(value).strip().rstrip("/")

    @property
    def flaresolverr_timeout_seconds(self) -> int:
        value = os.getenv("ACCOUNT_REGISTER_FLARESOLVERR_TIMEOUT_SECONDS") or self.data.get(
            "flaresolverr_timeout_seconds"
        )
        return _bounded_int(value, 60, 1, 300)

    @property
    def flaresolverr_refresh_interval_seconds(self) -> int:
        value = os.getenv("ACCOUNT_REGISTER_FLARESOLVERR_REFRESH_INTERVAL_SECONDS") or self.data.get(
            "flaresolverr_refresh_interval_seconds"
        )
        return _bounded_int(value, 3600, 60, 86400)

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
            "outbound_proxy": str(self.data.get("outbound_proxy") or "").strip(),
            "outbound_proxy_source": self.outbound_proxy_source,
            "outbound_proxy_id": self.outbound_proxy_id,
            "outbound_proxy_pool_mode": self.outbound_proxy_pool_mode,
            "flaresolverr_enabled": self.flaresolverr_enabled,
            "flaresolverr_url": self.flaresolverr_url,
            "flaresolverr_timeout_seconds": self.flaresolverr_timeout_seconds,
            "flaresolverr_refresh_interval_seconds": self.flaresolverr_refresh_interval_seconds,
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
            "outbound_proxy_source",
            "outbound_proxy_id",
            "outbound_proxy_pool_mode",
            "flaresolverr_enabled",
            "flaresolverr_url",
            "flaresolverr_timeout_seconds",
            "flaresolverr_refresh_interval_seconds",
            "image_account_concurrency",
            "auto_remove_invalid_accounts",
            "auto_remove_rate_limited_accounts",
            "cpa_secret_key",
            "refresh_account_interval_minutes",
            "cliproxy_upload_targets",
        ):
            if key in updates:
                value = updates[key]
                if key == "cliproxy_upload_targets":
                    value = normalize_upload_targets(value)
                elif key == "outbound_proxy_source":
                    value = str(value or "custom").strip().lower()
                    if value not in {"custom", "pool"}:
                        value = "custom"
                elif key == "outbound_proxy_pool_mode":
                    value = str(value or "selected").strip().lower()
                    if value not in {"selected", "random", "round_robin"}:
                        value = "selected"
                elif key == "outbound_proxy_id":
                    value = str(value or "").strip()
                elif key == "outbound_proxy":
                    value = str(value or "").strip()
                next_data[key] = value
        self.data = next_data
        CONFIG_FILE.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.get_public_settings()


config = Config()
