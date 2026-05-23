from __future__ import annotations

import json
import re
import uuid
from typing import Any

import requests


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _safe_file_name(value: str, fallback: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    name = (clean or fallback)[:80]
    return name if name.endswith(".json") else f"{name}.json"


def _timeout(value: object) -> int:
    try:
        return max(3, int(value or 30))
    except Exception:
        return 30


def normalize_upload_targets(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        target = {
            "id": _clean(item.get("id")) or _new_id(),
            "name": _clean(item.get("name")),
            "base_url": _clean(item.get("base_url")).rstrip("/"),
            "secret_key": _clean(item.get("secret_key")),
            "enabled": bool(item.get("enabled", True)),
            "timeout": _timeout(item.get("timeout")),
        }
        if target["base_url"] or target["secret_key"] or target["name"]:
            out.append(target)
    return out


def sanitize_upload_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(target) for target in normalize_upload_targets(targets)]


def upload_account_to_target(target: dict[str, Any], account: dict[str, Any]) -> tuple[bool, str]:
    normalized = normalize_upload_targets([target])
    target = normalized[0] if normalized else {}
    if not target or not target.get("enabled"):
        return False, "target disabled"
    if not target.get("base_url") or not target.get("secret_key"):
        return False, "missing base_url or secret_key"

    email = _clean(account.get("email"))
    account_id = _clean(account.get("account_id"))
    access_token = _clean(account.get("access_token"))
    name = _safe_file_name(email or account_id or access_token[:16], "account")
    url = f"{target['base_url']}/v0/management/auth-files"
    headers = {
        "Authorization": f"Bearer {target['secret_key']}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    response = requests.post(
        url,
        headers=headers,
        params={"name": name},
        data=json.dumps(account, ensure_ascii=False).encode("utf-8"),
        timeout=int(target.get("timeout") or 30),
    )
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}: {response.text[:200]}"
    return True, name


def upload_account_to_targets(targets: list[dict[str, Any]], account: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for target in normalize_upload_targets(targets):
        if not target.get("enabled"):
            continue
        label = target.get("name") or target.get("base_url") or target.get("id")
        try:
            ok, message = upload_account_to_target(target, account)
        except Exception as exc:
            ok, message = False, str(exc)
        results.append({"target": label, "ok": ok, "message": message})
    return results
