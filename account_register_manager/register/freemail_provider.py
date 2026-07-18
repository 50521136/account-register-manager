from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from account_register_manager.register.mail_provider import (
    BaseMailProvider,
    _create_session,
    _extract_content,
    _next_domain,
    _parse_received_at,
    _primary_secret,
)


class FreeMailProvider(BaseMailProvider):
    name = "freemail"

    def __init__(self, entry: dict[str, Any], conf: dict[str, Any]) -> None:
        super().__init__(conf, str(entry.get("provider_ref") or ""))
        api_base = str(entry.get("api_base") or "").strip().rstrip("/")
        for suffix in ("/api/generate", "/api"):
            if api_base.endswith(suffix):
                api_base = api_base[: -len(suffix)].rstrip("/")
                break
        if not api_base:
            raise RuntimeError("FreeMail 需要配置 API Base")
        self.api_base = api_base
        self.admin_token = _primary_secret(entry.get("admin_token") or entry.get("api_key"))
        if not self.admin_token:
            raise RuntimeError("FreeMail 需要配置 Admin Token")
        self.domains = [str(item).strip() for item in (entry.get("domain") or []) if str(item).strip()]
        self.domain_index = self._bounded_int(entry.get("domain_index"), 0, 0, 10000)
        self.random_length = self._bounded_int(entry.get("random_length"), 10, 1, 64)
        self.message_limit = self._bounded_int(entry.get("message_limit"), 20, 1, 50)
        self.session = _create_session(conf)
        self.session.headers.update(
            {
                "User-Agent": conf["user_agent"],
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.admin_token}",
            }
        )

    @staticmethod
    def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
        try:
            return min(maximum, max(minimum, int(value)))
        except (TypeError, ValueError):
            return default

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        response = self.session.request(
            method.upper(),
            f"{self.api_base}{path}",
            params=params,
            json=payload,
            timeout=self.conf["request_timeout"],
            verify=False,
        )
        if response.status_code not in expected:
            raise RuntimeError(
                f"FreeMail 请求失败: {method.upper()} {path}, HTTP {response.status_code}, "
                f"body={response.text[:300]}"
            )
        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"FreeMail {method.upper()} {path} 返回的不是 JSON") from exc
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"FreeMail 请求失败: {data.get('error')}")
        return data

    def _selected_domain_index(self) -> int:
        if not self.domains:
            return self.domain_index
        data = self._request("GET", "/api/domains")
        available = [str(item).strip() for item in data if str(item).strip()] if isinstance(data, list) else []
        selected = _next_domain(self.domains)
        try:
            return available.index(selected)
        except ValueError as exc:
            raise RuntimeError(f"FreeMail 域名不可用: {selected}") from exc

    def create_mailbox(self, username: str | None = None) -> dict[str, Any]:
        domain_index = self._selected_domain_index()
        if username:
            data = self._request(
                "POST",
                "/api/create",
                payload={"local": username, "domainIndex": domain_index},
                expected=(200, 201),
            )
        else:
            data = self._request(
                "GET",
                "/api/generate",
                params={"length": self.random_length, "domainIndex": domain_index},
            )
        if not isinstance(data, dict):
            raise RuntimeError("FreeMail 创建邮箱返回结构不是对象")
        address = str(data.get("email") or data.get("address") or "").strip()
        if not address:
            raise RuntimeError("FreeMail 创建邮箱未返回 email")
        return {
            "provider": self.name,
            "provider_ref": self.provider_ref,
            "address": address,
            "expires": data.get("expires"),
        }

    def fetch_latest_message(self, mailbox: dict[str, Any]) -> dict[str, Any] | None:
        address = str(mailbox.get("address") or "").strip()
        data = self._request(
            "GET",
            "/api/emails",
            params={"mailbox": address, "limit": self.message_limit},
        )
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            raw_items = data.get("list") or data.get("emails") or []
        else:
            raw_items = []
        messages = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        if not messages:
            return None
        item = max(
            messages,
            key=lambda value: (
                (
                    _parse_received_at(value.get("received_at") or value.get("created_at") or value.get("timestamp"))
                    or datetime.fromtimestamp(0, tz=timezone.utc)
                ).timestamp(),
                str(value.get("id") or ""),
            ),
        )
        message_id = str(item.get("id") or "").strip()
        detail = item
        if message_id:
            detail_data = self._request("GET", f"/api/email/{quote(message_id, safe='')}")
            if isinstance(detail_data, dict):
                detail = {**item, **detail_data}
        text_content, html_content = _extract_content(detail)
        verification_code = str(detail.get("verification_code") or item.get("verification_code") or "").strip()
        if verification_code and verification_code not in text_content and verification_code not in html_content:
            text_content = f"Verification code: {verification_code}\n{text_content}".strip()
        return {
            "provider": self.name,
            "mailbox": address,
            "message_id": message_id,
            "subject": str(detail.get("subject") or ""),
            "sender": str(detail.get("sender") or detail.get("from") or ""),
            "text_content": text_content or str(detail.get("preview") or ""),
            "html_content": html_content,
            "received_at": _parse_received_at(
                detail.get("received_at") or detail.get("created_at") or detail.get("timestamp")
            ),
            "raw": detail,
        }

    def close(self) -> None:
        self.session.close()
