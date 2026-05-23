from __future__ import annotations

import uuid
from typing import Any

from curl_cffi import requests

from account_register_manager.config import config


class InvalidAccessTokenError(RuntimeError):
    pass


class OpenAIBackendAPI:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.base_url = "https://chatgpt.com"
        self.session = requests.Session(impersonate="edge101")
        if config.outbound_proxy:
            self.session.proxies = {"http": config.outbound_proxy, "https": config.outbound_proxy}
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
                "Origin": self.base_url,
                "Referer": self.base_url + "/",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "OAI-Device-Id": str(uuid.uuid4()),
                "OAI-Session-Id": str(uuid.uuid4()),
            }
        )

    def _headers(self, path: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(self.session.headers)
        headers["X-OpenAI-Target-Path"] = path
        headers["X-OpenAI-Target-Route"] = path
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _extract_quota_and_restore_at(limits_progress: list[Any]) -> tuple[int, str | None, bool]:
        for item in limits_progress:
            if isinstance(item, dict) and item.get("feature_name") == "image_gen":
                return int(item.get("remaining") or 0), str(item.get("reset_after") or "") or None, False
        return 0, None, True

    def _get_json(self, path: str) -> dict[str, Any]:
        response = self.session.get(self.base_url + path, headers=self._headers(path), timeout=20)
        if response.status_code == 401:
            raise InvalidAccessTokenError(f"{path} failed: HTTP 401")
        if response.status_code != 200:
            raise RuntimeError(f"{path} failed: HTTP {response.status_code}")
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            self.base_url + path,
            headers=self._headers(path, {"Content-Type": "application/json"}),
            json=payload,
            timeout=20,
        )
        if response.status_code == 401:
            raise InvalidAccessTokenError(f"{path} failed: HTTP 401")
        if response.status_code != 200:
            raise RuntimeError(f"{path} failed: HTTP {response.status_code}")
        data = response.json()
        return data if isinstance(data, dict) else {}

    def get_user_info(self) -> dict[str, Any]:
        me_payload = self._get_json("/backend-api/me")
        init_payload = self._post_json(
            "/backend-api/conversation/init",
            {"gizmo_id": None, "requested_default_model": None, "conversation_id": None, "timezone_offset_min": -480},
        )
        account_payload = self._get_json("/backend-api/accounts/check/v4-2023-04-27?timezone_offset_min=-480")
        default_account = ((account_payload.get("accounts") or {}).get("default") or {}).get("account") or {}
        plan_type = str(default_account.get("plan_type") or "free")
        limits_progress = init_payload.get("limits_progress")
        limits_progress = limits_progress if isinstance(limits_progress, list) else []
        quota, restore_at, image_quota_unknown = self._extract_quota_and_restore_at(limits_progress)
        return {
            "email": me_payload.get("email"),
            "user_id": me_payload.get("id"),
            "type": plan_type,
            "quota": quota,
            "image_quota_unknown": image_quota_unknown,
            "limits_progress": limits_progress,
            "default_model_slug": init_payload.get("default_model_slug"),
            "restore_at": restore_at,
            "status": "正常" if image_quota_unknown and plan_type.lower() != "free" else ("限流" if quota == 0 else "正常"),
        }
