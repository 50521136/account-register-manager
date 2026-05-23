from __future__ import annotations

import asyncio
import io
import json
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import zipfile
from datetime import datetime, timezone
from typing import Any, Literal

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from curl_cffi import requests as curl_requests

from account_register_manager.account_service import account_service
from account_register_manager.auth import require_admin
from account_register_manager.config import BASE_DIR, config
from account_register_manager.register_service import register_service


class AccountCreateRequest(BaseModel):
    tokens: list[str] = Field(default_factory=list)
    accounts: list[dict[str, Any]] = Field(default_factory=list)
    refresh: bool = True


class AccountDeleteRequest(BaseModel):
    tokens: list[str] = Field(default_factory=list)


class AccountRefreshRequest(BaseModel):
    access_tokens: list[str] = Field(default_factory=list)


class AccountExportRequest(BaseModel):
    access_tokens: list[str] = Field(default_factory=list)
    format: Literal["json", "zip"] = "json"


class AccountUpdateRequest(BaseModel):
    access_token: str = ""
    type: str | None = None
    status: str | None = None
    quota: int | None = None


class RegisterConfigRequest(BaseModel):
    mail: dict | None = None
    proxy: str | None = None
    total: int | None = None
    threads: int | None = None
    mode: str | None = None
    target_quota: int | None = None
    target_available: int | None = None
    check_interval: int | None = None


class SettingsUpdateRequest(BaseModel):
    outbound_proxy: str | None = None
    image_account_concurrency: int | None = None
    auto_remove_invalid_accounts: bool | None = None
    auto_remove_rate_limited_accounts: bool | None = None
    cpa_secret_key: str | None = None
    refresh_account_interval_minutes: int | None = None
    cliproxy_upload_targets: list[dict[str, Any]] | None = None


class ProxyTestRequest(BaseModel):
    outbound_proxy: str | None = None


def _unique_tokens(tokens: list[str]) -> list[str]:
    return list(dict.fromkeys(str(token or "").strip() for token in tokens if str(token or "").strip()))


def _account_payload_token(item: dict[str, Any]) -> str:
    return str(item.get("access_token") or item.get("accessToken") or "").strip()


def _safe_export_name(value: str, fallback: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return (clean or fallback)[:80]


def _account_zip_bytes(items: list[dict[str, str]]) -> bytes:
    buf = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, item in enumerate(items, start=1):
            raw_name = item.get("email") or item.get("account_id") or f"account-{index:03d}"
            base_name = _safe_export_name(raw_name, f"account-{index:03d}")
            name = base_name
            suffix = 2
            while name in used_names:
                name = f"{base_name}-{suffix}"
                suffix += 1
            used_names.add(name)
            archive.writestr(f"{name}.json", json.dumps(item, ensure_ascii=False, indent=2) + "\n")
    return buf.getvalue()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountRefreshJobService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._job: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None

    def get(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._job) if isinstance(self._job, dict) else None

    def start(self, tokens: list[str]) -> dict[str, Any]:
        with self._lock:
            if self._thread and self._thread.is_alive() and isinstance(self._job, dict):
                return dict(self._job)
            job = {
                "job_id": uuid.uuid4().hex,
                "status": "running",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "total": len(tokens),
                "submitted": 0,
                "completed": 0,
                "refreshed": 0,
                "failed": 0,
                "errors": [],
            }
            self._job = job
            self._thread = threading.Thread(target=self._run, args=(tokens,), name="account-refresh-job", daemon=True)
            self._thread.start()
            return dict(job)

    def _update(self, **updates: Any) -> None:
        with self._lock:
            if not isinstance(self._job, dict):
                return
            self._job.update(updates)
            self._job["updated_at"] = _now_iso()

    def _run(self, tokens: list[str]) -> None:
        refreshed = 0
        errors: list[dict[str, str]] = []
        completed = 0
        max_workers = min(10, max(1, len(tokens)))
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(account_service.fetch_remote_info, token): token for token in tokens}
                self._update(submitted=len(futures), max_workers=max_workers)
                for future in as_completed(futures):
                    token = futures[future]
                    completed += 1
                    try:
                        if future.result() is not None:
                            refreshed += 1
                    except Exception as exc:
                        errors.append({"token": token[:8] + "...", "error": str(exc)})
                    self._update(
                        completed=completed,
                        refreshed=refreshed,
                        failed=len(errors),
                        errors=errors[-20:],
                    )
            self._update(
                status="completed" if not errors else "completed_with_errors",
                completed=len(tokens),
                refreshed=refreshed,
                failed=len(errors),
                errors=errors[-50:],
                finished_at=_now_iso(),
            )
        except Exception as exc:
            errors.append({"error": str(exc)})
            self._update(
                status="failed",
                completed=completed,
                refreshed=refreshed,
                failed=max(len(errors), len(tokens) - completed),
                errors=errors[-50:],
                finished_at=_now_iso(),
            )


account_refresh_jobs = AccountRefreshJobService()


def start_periodic_account_refresh(stop_event: threading.Event) -> threading.Thread:
    def worker() -> None:
        while not stop_event.is_set():
            interval_minutes = int(config.refresh_account_interval_minutes or 0)
            if interval_minutes <= 0:
                stop_event.wait(10)
                continue
            stop_event.wait(interval_minutes * 60)
            if stop_event.is_set():
                break
            tokens = account_service.list_tokens()
            if not tokens:
                continue
            current_job = account_refresh_jobs.get()
            if current_job and current_job.get("status") == "running":
                continue
            account_refresh_jobs.start(tokens)

    thread = threading.Thread(target=worker, name="periodic-account-refresh", daemon=True)
    thread.start()
    return thread


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_event = threading.Event()
        thread = start_periodic_account_refresh(stop_event)
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=1)

    app = FastAPI(title="account-register-manager", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

    @app.get("/")
    async def index():
        return FileResponse(BASE_DIR / "static" / "index.html")

    @app.get("/api/settings")
    async def get_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"settings": config.get_public_settings()}

    @app.post("/api/settings")
    async def update_settings(body: SettingsUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"settings": config.update(body.model_dump(exclude_none=True))}

    @app.post("/api/settings/test-proxy")
    async def test_proxy(body: ProxyTestRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        proxy = str(body.outbound_proxy if body.outbound_proxy is not None else config.outbound_proxy).strip()
        started = datetime.now(timezone.utc)
        session = curl_requests.Session(impersonate="edge101")
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}
        try:
            response = session.get("https://api.ipify.org?format=json", timeout=12)
            elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            if response.status_code != 200:
                return {"ok": False, "status_code": response.status_code, "elapsed_ms": elapsed_ms, "error": response.text[:300]}
            payload = response.json()
            return {"ok": True, "proxy": proxy, "ip": payload.get("ip"), "elapsed_ms": elapsed_ms}
        except Exception as exc:
            elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            return {"ok": False, "proxy": proxy, "elapsed_ms": elapsed_ms, "error": str(exc)}
        finally:
            session.close()

    @app.get("/api/accounts")
    async def get_accounts(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": account_service.list_accounts()}

    @app.post("/api/accounts")
    async def create_accounts(body: AccountCreateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        payloads = [item for item in body.accounts if isinstance(item, dict)]
        tokens = _unique_tokens([*body.tokens, *[_account_payload_token(item) for item in payloads]])
        if not tokens:
            raise HTTPException(status_code=400, detail={"error": "tokens is required"})
        result = account_service.add_account_items(payloads) if payloads else account_service.add_accounts(tokens)
        extra_tokens = [token for token in tokens if token not in {_account_payload_token(item) for item in payloads}]
        if payloads and extra_tokens:
            extra_result = account_service.add_accounts(extra_tokens)
            result["added"] += extra_result["added"]
            result["skipped"] += extra_result["skipped"]
        refresh_result = account_service.refresh_accounts(tokens) if body.refresh else {"refreshed": 0, "errors": [], "items": result.get("items", [])}
        return {**result, "refreshed": refresh_result.get("refreshed", 0), "errors": refresh_result.get("errors", []), "items": refresh_result.get("items", result.get("items", []))}

    @app.delete("/api/accounts")
    async def delete_accounts(body: AccountDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        tokens = _unique_tokens(body.tokens)
        if not tokens:
            raise HTTPException(status_code=400, detail={"error": "tokens is required"})
        return account_service.delete_accounts(tokens)

    @app.post("/api/accounts/refresh")
    async def refresh_accounts(body: AccountRefreshRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        tokens = _unique_tokens(body.access_tokens) or account_service.list_tokens()
        if not tokens:
            raise HTTPException(status_code=400, detail={"error": "access_tokens is required"})
        return account_service.refresh_accounts(tokens)

    @app.post("/api/accounts/refresh/start")
    async def start_refresh_accounts(body: AccountRefreshRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        tokens = _unique_tokens(body.access_tokens) or account_service.list_tokens()
        if not tokens:
            raise HTTPException(status_code=400, detail={"error": "access_tokens is required"})
        return {"refresh_job": account_refresh_jobs.start(tokens)}

    @app.get("/api/accounts/refresh/job")
    async def get_refresh_account_job(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"refresh_job": account_refresh_jobs.get()}

    @app.post("/api/accounts/update")
    async def update_account(body: AccountUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        access_token = str(body.access_token or "").strip()
        if not access_token:
            raise HTTPException(status_code=400, detail={"error": "access_token is required"})
        updates = {key: value for key, value in {"type": body.type, "status": body.status, "quota": body.quota}.items() if value is not None}
        if not updates:
            raise HTTPException(status_code=400, detail={"error": "no updates"})
        account = account_service.update_account(access_token, updates)
        if account is None:
            raise HTTPException(status_code=404, detail={"error": "account not found"})
        return {"item": account, "items": account_service.list_accounts()}

    @app.post("/api/accounts/export")
    async def export_accounts(body: AccountExportRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        items = account_service.build_export_items(_unique_tokens(body.access_tokens))
        if not items:
            raise HTTPException(status_code=400, detail={"error": "no complete accounts to export"})
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        if body.format == "zip":
            return Response(
                _account_zip_bytes(items),
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="accounts-{timestamp}.zip"'},
            )
        payload: dict[str, str] | list[dict[str, str]] = items[0] if len(items) == 1 else items
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="accounts-{timestamp}.json"'},
        )

    @app.get("/api/register")
    async def get_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.get()}

    @app.post("/api/register")
    async def update_register(body: RegisterConfigRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.update(body.model_dump(exclude_none=True))}

    @app.post("/api/register/start")
    async def start_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.start()}

    @app.post("/api/register/stop")
    async def stop_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.stop()}

    @app.post("/api/register/reset")
    async def reset_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.reset()}

    @app.get("/api/register/events")
    async def register_events(token: str = ""):
        require_admin(f"Bearer {token}")

        async def stream():
            last = ""
            while True:
                payload = json.dumps(register_service.get(), ensure_ascii=False)
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    def require_cpa_auth(authorization: str | None) -> None:
        scheme, _, token = str(authorization or "").partition(" ")
        expected = config.cpa_secret_key
        if not expected or scheme.lower() != "bearer" or token.strip() != expected:
            raise HTTPException(status_code=401, detail={"error": "invalid cpa token"})

    @app.get("/v0/management/auth-files")
    async def cpa_list_auth_files(authorization: str | None = Header(default=None)):
        require_cpa_auth(authorization)
        files = []
        for index, item in enumerate(account_service.build_export_items(), start=1):
            email = item.get("email") or item.get("account_id") or f"account-{index:03d}"
            files.append(
                {
                    "name": _safe_export_name(email, f"account-{index:03d}") + ".json",
                    "email": item.get("email") or "",
                    "account": item.get("email") or item.get("account_id") or "",
                    "account_id": item.get("account_id") or "",
                    "type": item.get("type") or "codex",
                    "expired": item.get("expired") or "",
                    "last_refresh": item.get("last_refresh") or "",
                }
            )
        return {"files": files}

    @app.get("/v0/management/auth-files/download")
    async def cpa_download_auth_file(
        name: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        require_cpa_auth(authorization)
        requested = str(name or "").strip()
        for index, item in enumerate(account_service.build_export_items(), start=1):
            email = item.get("email") or item.get("account_id") or f"account-{index:03d}"
            safe_name = _safe_export_name(email, f"account-{index:03d}") + ".json"
            if requested in {safe_name, safe_name.removesuffix(".json"), item.get("email", ""), item.get("account_id", "")}:
                return item
        raise HTTPException(status_code=404, detail={"error": "auth file not found"})

    return app


app = create_app()
