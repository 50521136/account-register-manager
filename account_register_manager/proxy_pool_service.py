from __future__ import annotations

import threading
import uuid
from typing import Any
from urllib.parse import unquote, urlparse

from account_register_manager.config import DATA_DIR
from account_register_manager.json_file import read_json_object, write_json_file
from account_register_manager.proxy_service import normalize_proxy_url
from account_register_manager.time_utils import now_beijing_iso

PROXY_POOL_FILE = DATA_DIR / "proxy_pool.json"

_SUPPORTED_SCHEMES = {"http", "https", "socks", "socks4", "socks5", "socks5h"}


def mask_proxy_url(value: object) -> str:
    proxy = str(value or "").strip()
    if not proxy:
        return ""
    try:
        parsed = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    except Exception:
        return proxy
    if not parsed.hostname:
        return proxy
    scheme = (parsed.scheme or "http").lower()
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname and not parsed.hostname.startswith("[") else parsed.hostname
    port = f":{parsed.port}" if parsed.port else ""
    if parsed.username is not None:
        user = unquote(parsed.username)
        return f"{scheme}://{user}:***@{host}{port}"
    return f"{scheme}://{host}{port}"


def parse_proxy_line(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or raw.startswith("#"):
        return ""
    # strip surrounding quotes
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()
    if not raw:
        return ""

    candidate = raw
    lowered = candidate.lower()
    if "://" not in candidate:
        # host:port or user:pass@host:port without scheme -> default http
        candidate = f"http://{candidate}"
        lowered = candidate.lower()

    scheme = lowered.split("://", 1)[0]
    if scheme not in _SUPPORTED_SCHEMES:
        raise ValueError(f"不支持的代理协议: {scheme}")

    normalized = normalize_proxy_url(candidate)
    parsed = urlparse(normalized)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"代理地址无效: {raw}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"代理端口无效: {raw}") from exc
    if port is not None and not (1 <= int(port) <= 65535):
        raise ValueError(f"代理端口超出范围: {raw}")
    return normalized


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    url = str(item.get("url") or "").strip()
    return {
        "id": str(item.get("id") or ""),
        "url": url,
        "display": mask_proxy_url(url),
        "enabled": bool(item.get("enabled", True)),
        "note": str(item.get("note") or ""),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "last_ok_at": item.get("last_ok_at"),
        "last_error": str(item.get("last_error") or ""),
        "success_count": int(item.get("success_count") or 0),
        "fail_count": int(item.get("fail_count") or 0),
    }


class ProxyPoolService:
    def __init__(self, store_file=PROXY_POOL_FILE) -> None:
        self._store_file = store_file
        self._lock = threading.RLock()
        self._rr_index = 0
        self._items = self._load()

    def _load(self) -> list[dict[str, Any]]:
        data = read_json_object(self._store_file, name="proxy_pool")
        raw_items = data.get("proxies") if isinstance(data, dict) else None
        if not isinstance(raw_items, list):
            return []
        items: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                url = parse_proxy_line(item.get("url"))
            except Exception:
                url = str(item.get("url") or "").strip()
            if not url:
                continue
            items.append(
                {
                    "id": str(item.get("id") or uuid.uuid4().hex),
                    "url": url,
                    "enabled": bool(item.get("enabled", True)),
                    "note": str(item.get("note") or ""),
                    "created_at": str(item.get("created_at") or now_beijing_iso()),
                    "updated_at": str(item.get("updated_at") or ""),
                    "last_ok_at": item.get("last_ok_at"),
                    "last_error": str(item.get("last_error") or ""),
                    "success_count": int(item.get("success_count") or 0),
                    "fail_count": int(item.get("fail_count") or 0),
                }
            )
        return items

    def _save(self) -> None:
        write_json_file(self._store_file, {"proxies": self._items})

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [_public_item(item) for item in self._items]

    def count(self, *, enabled_only: bool = False) -> int:
        with self._lock:
            if enabled_only:
                return sum(1 for item in self._items if item.get("enabled", True))
            return len(self._items)

    def get(self, proxy_id: str) -> dict[str, Any] | None:
        proxy_id = str(proxy_id or "").strip()
        if not proxy_id:
            return None
        with self._lock:
            for item in self._items:
                if item.get("id") == proxy_id:
                    return _public_item(item)
        return None

    def get_url(self, proxy_id: str) -> str:
        proxy_id = str(proxy_id or "").strip()
        if not proxy_id:
            return ""
        with self._lock:
            for item in self._items:
                if item.get("id") == proxy_id and item.get("enabled", True):
                    return str(item.get("url") or "").strip()
        return ""

    def add_batch(self, text: str = "", proxies: list[str] | None = None, *, replace: bool = False) -> dict[str, Any]:
        lines: list[str] = []
        if text:
            lines.extend(str(text).splitlines())
        if proxies:
            lines.extend(str(item) for item in proxies)

        added: list[dict[str, Any]] = []
        skipped_duplicates = 0
        errors: list[str] = []
        now = now_beijing_iso()

        with self._lock:
            if replace:
                self._items = []
                self._rr_index = 0

            existing_urls = {str(item.get("url") or "").strip().lower() for item in self._items}
            for index, line in enumerate(lines, start=1):
                raw = str(line or "").strip()
                if not raw or raw.startswith("#"):
                    continue
                try:
                    url = parse_proxy_line(raw)
                except Exception as exc:
                    errors.append(f"第{index}行: {exc}")
                    continue
                key = url.lower()
                if key in existing_urls:
                    skipped_duplicates += 1
                    continue
                item = {
                    "id": uuid.uuid4().hex,
                    "url": url,
                    "enabled": True,
                    "note": "",
                    "created_at": now,
                    "updated_at": now,
                    "last_ok_at": None,
                    "last_error": "",
                    "success_count": 0,
                    "fail_count": 0,
                }
                self._items.append(item)
                existing_urls.add(key)
                added.append(_public_item(item))
            self._save()
            total = len(self._items)

        return {
            "added": len(added),
            "skipped_duplicates": skipped_duplicates,
            "errors": errors,
            "total": total,
            "proxies": added,
        }

    def delete(self, ids: list[str]) -> dict[str, Any]:
        wanted = {str(item or "").strip() for item in ids if str(item or "").strip()}
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.get("id") not in wanted]
            deleted = before - len(self._items)
            if deleted:
                self._save()
            return {"deleted": deleted, "total": len(self._items)}

    def clear(self) -> dict[str, Any]:
        with self._lock:
            count = len(self._items)
            self._items = []
            self._rr_index = 0
            self._save()
            return {"deleted": count, "total": 0}

    def set_enabled(self, proxy_id: str, enabled: bool) -> dict[str, Any] | None:
        proxy_id = str(proxy_id or "").strip()
        with self._lock:
            for item in self._items:
                if item.get("id") == proxy_id:
                    item["enabled"] = bool(enabled)
                    item["updated_at"] = now_beijing_iso()
                    self._save()
                    return _public_item(item)
        return None

    def update_item(self, proxy_id: str, *, note: str | None = None, enabled: bool | None = None, url: str | None = None) -> dict[str, Any] | None:
        proxy_id = str(proxy_id or "").strip()
        with self._lock:
            for item in self._items:
                if item.get("id") != proxy_id:
                    continue
                if url is not None:
                    item["url"] = parse_proxy_line(url)
                if note is not None:
                    item["note"] = str(note or "")
                if enabled is not None:
                    item["enabled"] = bool(enabled)
                item["updated_at"] = now_beijing_iso()
                self._save()
                return _public_item(item)
        return None

    def mark_result(self, proxy_id: str = "", proxy_url: str = "", *, ok: bool, error: str = "") -> None:
        proxy_id = str(proxy_id or "").strip()
        proxy_url = str(proxy_url or "").strip()
        with self._lock:
            target = None
            for item in self._items:
                if proxy_id and item.get("id") == proxy_id:
                    target = item
                    break
                if proxy_url and str(item.get("url") or "").strip() == proxy_url:
                    target = item
                    break
            if not target:
                return
            if ok:
                target["success_count"] = int(target.get("success_count") or 0) + 1
                target["last_ok_at"] = now_beijing_iso()
                target["last_error"] = ""
            else:
                target["fail_count"] = int(target.get("fail_count") or 0) + 1
                target["last_error"] = str(error or "")[:300]
            target["updated_at"] = now_beijing_iso()
            self._save()

    def _enabled_items(self) -> list[dict[str, Any]]:
        return [item for item in self._items if item.get("enabled", True) and str(item.get("url") or "").strip()]

    def pick(self, mode: str = "random", proxy_id: str = "") -> str:
        """Pick a proxy URL from the pool.

        mode:
          - selected: use proxy_id
          - random: random enabled proxy
          - round_robin: rotate enabled proxies
        """
        mode = str(mode or "random").strip().lower()
        proxy_id = str(proxy_id or "").strip()
        with self._lock:
            if mode == "selected":
                if not proxy_id:
                    raise RuntimeError("未选择代理池中的代理")
                for item in self._items:
                    if item.get("id") == proxy_id:
                        if not item.get("enabled", True):
                            raise RuntimeError("所选代理已禁用")
                        url = str(item.get("url") or "").strip()
                        if not url:
                            raise RuntimeError("所选代理地址为空")
                        return url
                raise RuntimeError("所选代理不存在，请重新选择")

            enabled = self._enabled_items()
            if not enabled:
                raise RuntimeError("代理池为空或没有启用的代理")
            if mode == "round_robin":
                index = self._rr_index % len(enabled)
                self._rr_index = (self._rr_index + 1) % max(1, len(enabled))
                return str(enabled[index].get("url") or "").strip()
            # default random
            import random

            return str(random.choice(enabled).get("url") or "").strip()

    def resolve(
        self,
        *,
        source: str = "custom",
        custom_proxy: str = "",
        mode: str = "selected",
        proxy_id: str = "",
        allow_empty: bool = True,
    ) -> str:
        source = str(source or "custom").strip().lower()
        if source != "pool":
            return str(custom_proxy or "").strip()
        try:
            return self.pick(mode=mode or "selected", proxy_id=proxy_id)
        except RuntimeError:
            if allow_empty:
                return ""
            raise


proxy_pool_service = ProxyPoolService()
