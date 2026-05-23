from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from threading import Condition, RLock
from typing import Any

from account_register_manager.config import DATA_DIR, config
from account_register_manager.openai_backend_api import InvalidAccessTokenError, OpenAIBackendAPI
from account_register_manager.storage import JSONStorage

EXPORT_TIMEZONE = timezone(timedelta(hours=8))


def _clean_string(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = str(token or "").split(".")
    if len(parts) < 2:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
    except Exception:
        return {}


def _format_timestamp(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(EXPORT_TIMEZONE).isoformat(timespec="seconds")


class AccountService:
    def __init__(self, storage: JSONStorage):
        self.storage = storage
        self._lock = RLock()
        self._image_slot_condition = Condition(self._lock)
        self._index = 0
        self._image_inflight: dict[str, int] = {}
        self._accounts = self._load_accounts()

    def _load_accounts(self) -> dict[str, dict[str, Any]]:
        return {
            normalized["access_token"]: normalized
            for item in self.storage.load_accounts()
            if (normalized := self._normalize_account(item)) is not None
        }

    def _save_accounts(self) -> None:
        self.storage.save_accounts(list(self._accounts.values()))

    def _normalize_account(self, item: dict[str, Any]) -> dict[str, Any] | None:
        access_token = _clean_string(item.get("access_token") or item.get("accessToken"))
        if not access_token:
            return None
        normalized = dict(item)
        normalized.pop("accessToken", None)
        normalized["access_token"] = access_token
        normalized["type"] = normalized.get("type") or "free"
        normalized["status"] = normalized.get("status") or "正常"
        normalized["quota"] = max(0, int(normalized.get("quota") if normalized.get("quota") is not None else 0))
        normalized["image_quota_unknown"] = bool(normalized.get("image_quota_unknown"))
        normalized["email"] = normalized.get("email") or None
        normalized["user_id"] = normalized.get("user_id") or None
        normalized["limits_progress"] = normalized.get("limits_progress") if isinstance(normalized.get("limits_progress"), list) else []
        normalized["default_model_slug"] = normalized.get("default_model_slug") or None
        normalized["restore_at"] = normalized.get("restore_at") or None
        normalized["success"] = int(normalized.get("success") or 0)
        normalized["fail"] = int(normalized.get("fail") or 0)
        normalized["last_used_at"] = normalized.get("last_used_at")
        return normalized

    @staticmethod
    def _is_image_account_available(account: dict[str, Any]) -> bool:
        if account.get("status") in {"禁用", "限流", "异常"}:
            return False
        if bool(account.get("image_quota_unknown")):
            return True
        return int(account.get("quota") or 0) > 0

    def list_tokens(self) -> list[str]:
        with self._lock:
            return list(self._accounts)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._accounts.values()]

    def get_account(self, access_token: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._accounts.get(access_token)
            return dict(item) if item else None

    def add_accounts(self, tokens: list[str]) -> dict[str, Any]:
        return self.add_account_items([{"access_token": token} for token in tokens])

    def add_account_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            added = 0
            skipped = 0
            for item in items:
                normalized = self._normalize_account(item)
                if normalized is None:
                    continue
                token = normalized["access_token"]
                current = self._accounts.get(token)
                added += 1 if current is None else 0
                skipped += 0 if current is None else 1
                self._accounts[token] = self._normalize_account({**(current or {}), **normalized}) or normalized
            self._save_accounts()
            return {"added": added, "skipped": skipped, "items": self.list_accounts()}

    def delete_accounts(self, tokens: list[str]) -> dict[str, Any]:
        target_set = {token for token in tokens if token}
        with self._lock:
            removed = sum(self._accounts.pop(token, None) is not None for token in target_set)
            for token in target_set:
                self._image_inflight.pop(token, None)
            self._index = self._index % len(self._accounts) if self._accounts else 0
            self._save_accounts()
            return {"removed": removed, "items": self.list_accounts()}

    def update_account(self, access_token: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            current = self._accounts.get(access_token)
            if current is None:
                return None
            account = self._normalize_account({**current, **updates, "access_token": access_token})
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                self._accounts.pop(access_token, None)
                self._save_accounts()
                return None
            self._accounts[access_token] = account
            self._save_accounts()
            return dict(account)

    def remove_invalid_token(self, access_token: str) -> bool:
        if config.auto_remove_invalid_accounts:
            return bool(self.delete_accounts([access_token])["removed"])
        self.update_account(access_token, {"status": "异常", "quota": 0})
        return False

    def fetch_remote_info(self, access_token: str) -> dict[str, Any] | None:
        if not access_token:
            raise ValueError("access_token is required")
        try:
            result = OpenAIBackendAPI(access_token).get_user_info()
        except InvalidAccessTokenError:
            self.remove_invalid_token(access_token)
            raise
        return self.update_account(access_token, result)

    def refresh_accounts(self, access_tokens: list[str]) -> dict[str, Any]:
        access_tokens = list(dict.fromkeys(token for token in access_tokens if token))
        if not access_tokens:
            return {"refreshed": 0, "errors": [], "items": self.list_accounts()}
        refreshed = 0
        errors: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=min(10, len(access_tokens))) as executor:
            futures = {executor.submit(self.fetch_remote_info, token): token for token in access_tokens}
            for future in as_completed(futures):
                try:
                    if future.result() is not None:
                        refreshed += 1
                except Exception as exc:
                    token = futures[future]
                    errors.append({"token": token[:8] + "...", "error": str(exc)})
        return {"refreshed": refreshed, "errors": errors, "items": self.list_accounts()}

    def build_export_items(self, access_tokens: list[str] | None = None) -> list[dict[str, str]]:
        requested = [token for token in dict.fromkeys(access_tokens or []) if token]
        with self._lock:
            accounts = [dict(self._accounts[token]) for token in requested if token in self._accounts] if requested else [dict(item) for item in self._accounts.values()]
        out: list[dict[str, str]] = []
        for account in accounts:
            access_token = _clean_string(account.get("access_token"))
            id_token = _clean_string(account.get("id_token"))
            refresh_token = _clean_string(account.get("refresh_token"))
            if not access_token or not refresh_token or not id_token:
                continue
            access_claims = _decode_jwt_payload(access_token)
            id_claims = _decode_jwt_payload(id_token)
            access_auth = access_claims.get("https://api.openai.com/auth") if isinstance(access_claims.get("https://api.openai.com/auth"), dict) else {}
            profile = access_claims.get("https://api.openai.com/profile") if isinstance(access_claims.get("https://api.openai.com/profile"), dict) else {}
            out.append(
                {
                    "type": _clean_string(account.get("export_type")) or "codex",
                    "email": _clean_string(account.get("email")) or _clean_string(profile.get("email")) or _clean_string(id_claims.get("email")),
                    "expired": _clean_string(account.get("expired")) or _format_timestamp(access_claims.get("exp")),
                    "id_token": id_token,
                    "account_id": _clean_string(account.get("account_id")) or _clean_string(access_auth.get("chatgpt_account_id")),
                    "access_token": access_token,
                    "last_refresh": _clean_string(account.get("last_refresh")) or _format_timestamp(access_claims.get("iat")),
                    "refresh_token": refresh_token,
                }
            )
        return out


account_service = AccountService(JSONStorage(DATA_DIR / "accounts.json"))
