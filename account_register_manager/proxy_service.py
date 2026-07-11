from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import threading
import time
import uuid
from typing import Any, Mapping
from urllib import request as urllib_request
from urllib.parse import unquote, urlparse

from account_register_manager.config import config


def normalize_proxy_url(value: object) -> str:
    proxy = str(value or "").strip()
    lowered = proxy.lower()
    if lowered.startswith("socks://"):
        return "socks5h://" + proxy[len("socks://") :]
    if lowered.startswith("socks5://"):
        return "socks5h://" + proxy[len("socks5://") :]
    return proxy


def _flaresolverr_proxy_config(value: object) -> dict[str, str]:
    proxy_url = normalize_proxy_url(value)
    if not proxy_url:
        return {}
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme == "socks5h":
        scheme = "socks5"
    if scheme not in {"http", "socks4", "socks5"} or not parsed.hostname:
        raise RuntimeError("FlareSolverr proxy must use http://, socks4:// or socks5://")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("FlareSolverr proxy has an invalid port") from exc
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    proxy: dict[str, str] = {"url": f"{scheme}://{host}{f':{port}' if port else ''}"}
    if parsed.username is not None:
        proxy["username"] = unquote(parsed.username)
        proxy["password"] = unquote(parsed.password or "")
    return proxy


def _normalize_host(value: object) -> str:
    return str(value or "").strip().strip(".").lower()


def _host_from_url(value: object) -> str:
    candidate = str(value or "").strip()
    parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
    return _normalize_host(parsed.hostname)


def _domain_matches(host: str, domain: str) -> bool:
    normalized_host = _normalize_host(host)
    normalized_domain = _normalize_host(str(domain or "").lstrip("."))
    return not normalized_domain or normalized_host == normalized_domain or normalized_host.endswith(f".{normalized_domain}")


def _filter_flaresolverr_cookies(raw: object, target_host: str) -> dict[str, str]:
    if not isinstance(raw, list):
        return {}
    cookies: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        domain = str(item.get("domain") or "").strip()
        if name and _domain_matches(target_host, domain):
            cookies[name] = str(item.get("value") or "")
    return cookies


def _parse_cookie_header(value: object) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(value or "").split(";"):
        name, separator, cookie_value = part.strip().partition("=")
        if separator and name:
            cookies[name.strip()] = cookie_value.strip()
    return cookies


def _merge_cookie_header(existing: object, cookies: Mapping[str, str]) -> str:
    merged = _parse_cookie_header(existing)
    merged.update({str(name): str(value) for name, value in cookies.items() if name})
    return "; ".join(f"{name}={value}" for name, value in merged.items())


def _find_header_key(headers: Mapping[str, object], name: str) -> str | None:
    target = name.lower()
    return next((str(key) for key in headers if str(key).lower() == target), None)


@dataclass(frozen=True)
class ClearanceBundle:
    user_agent: str = ""
    cookies: dict[str, str] = field(default_factory=dict, repr=False)
    target_host: str = ""
    proxy_url: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    def is_valid_for(self, target_host: str, proxy_url: str, now: float | None = None) -> bool:
        if self.target_host and _normalize_host(self.target_host) != _normalize_host(target_host):
            return False
        if normalize_proxy_url(self.proxy_url) != normalize_proxy_url(proxy_url):
            return False
        if self.expires_at is not None and (time.time() if now is None else now) >= self.expires_at:
            return False
        return bool(self.cookies or self.user_agent)


@dataclass(frozen=True)
class ProxyProfile:
    proxy_url: str = ""
    clearance_enabled: bool = False
    clearance_mode: str = "none"
    clearance: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def timeout_sec(self) -> int:
        try:
            return max(1, int(self.clearance.get("timeout_seconds") or 60))
        except (TypeError, ValueError):
            return 60

    @property
    def refresh_interval(self) -> int:
        try:
            return max(60, int(self.clearance.get("refresh_interval_seconds") or 3600))
        except (TypeError, ValueError):
            return 3600


class FlareSolverrClearanceProvider:
    def __init__(self, flaresolverr_url: str) -> None:
        self.flaresolverr_url = str(flaresolverr_url or "").strip().rstrip("/")

    def get_clearance(
        self,
        target_url: str,
        proxy_url: str = "",
        timeout_sec: int = 60,
        *,
        raise_errors: bool = False,
    ) -> ClearanceBundle | None:
        try:
            return self._request_clearance(target_url, proxy_url, timeout_sec)
        except Exception:
            if raise_errors:
                raise
            return None

    def _request_clearance(self, target_url: str, proxy_url: str, timeout_sec: int) -> ClearanceBundle:
        if not self.flaresolverr_url:
            raise RuntimeError("FlareSolverr URL is required")
        parsed_service = urlparse(self.flaresolverr_url)
        if parsed_service.scheme not in {"http", "https"} or not parsed_service.netloc:
            raise RuntimeError("FlareSolverr URL must be an HTTP(S) URL")
        parsed_target = urlparse(str(target_url or "").strip())
        if parsed_target.scheme not in {"http", "https"} or not parsed_target.netloc:
            raise RuntimeError("target URL must be an HTTP(S) URL")

        timeout = max(1, min(300, int(timeout_sec or 60)))
        bundle_proxy_url = normalize_proxy_url(proxy_url)
        proxy = _flaresolverr_proxy_config(bundle_proxy_url)
        payload: dict[str, object] = {
            "cmd": "request.get",
            "url": parsed_target.geturl(),
            "maxTimeout": timeout * 1000,
        }
        session_id = ""
        if proxy.get("username") is not None:
            session_id = f"account-register-{uuid.uuid4().hex}"
            self._post_command(
                {
                    "cmd": "sessions.create",
                    "session": session_id,
                    "proxy": proxy,
                },
                timeout,
            )
            payload["session"] = session_id
        elif proxy:
            payload["proxy"] = {"url": proxy["url"]}

        try:
            data = self._post_command(payload, timeout)
        finally:
            if session_id:
                try:
                    self._post_command({"cmd": "sessions.destroy", "session": session_id}, min(timeout, 15))
                except Exception:
                    pass

        solution = data.get("solution")
        if not isinstance(solution, dict):
            raise RuntimeError("FlareSolverr response has no solution")

        target_host = _host_from_url(target_url)
        cookies = _filter_flaresolverr_cookies(solution.get("cookies"), target_host)
        user_agent = str(solution.get("userAgent") or "").strip()
        if not cookies and not user_agent:
            raise RuntimeError("FlareSolverr solution has no cookies or User-Agent")
        return ClearanceBundle(
            user_agent=user_agent,
            cookies=cookies,
            target_host=target_host,
            proxy_url=bundle_proxy_url,
        )

    def _post_command(self, payload: dict[str, object], timeout: int) -> dict[str, Any]:
        parsed_service = urlparse(self.flaresolverr_url)
        endpoint = (
            self.flaresolverr_url
            if parsed_service.path.rstrip("/").endswith("/v1")
            else f"{self.flaresolverr_url}/v1"
        )
        request = urllib_request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout + 5) as response:
                raw = response.read()
        except Exception as exc:
            raise RuntimeError(f"FlareSolverr request failed: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("FlareSolverr returned invalid JSON") from exc
        if not isinstance(data, dict) or str(data.get("status") or "").lower() != "ok":
            message = str(data.get("message") or data.get("status") or "request failed") if isinstance(data, dict) else "request failed"
            raise RuntimeError(f"FlareSolverr error: {message}")
        return data


class ProxySettings:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._clearance_cache: dict[tuple[str, str, str], ClearanceBundle] = {}
        self._flight_locks: dict[tuple[str, str, str], threading.Lock] = {}

    def build_session_kwargs(
        self,
        proxy: str = "",
        upstream: bool = True,
        impersonate: str = "chrome",
        verify: bool = False,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"impersonate": impersonate, "verify": verify}
        normalized_proxy = normalize_proxy_url(proxy)
        if normalized_proxy:
            kwargs["proxies"] = {"http": normalized_proxy, "https": normalized_proxy}
        return kwargs

    def build_headers(
        self,
        headers: Mapping[str, object] | None = None,
        target_url: str = "",
        proxy: str = "",
        upstream: bool = True,
    ) -> dict[str, str]:
        merged = {str(key): str(value) for key, value in dict(headers or {}).items()}
        profile = self.get_profile(proxy=proxy, upstream=upstream)
        if not profile.clearance_enabled:
            return merged
        target_host = _host_from_url(target_url)
        bundle = self._get_cached_bundle(self._cache_key(profile, target_host))
        if bundle is None or not bundle.is_valid_for(target_host, profile.proxy_url):
            return merged
        if bundle.user_agent:
            key = _find_header_key(merged, "user-agent") or "User-Agent"
            merged[key] = bundle.user_agent
        if bundle.cookies:
            key = _find_header_key(merged, "cookie") or "Cookie"
            merged[key] = _merge_cookie_header(merged.get(key), bundle.cookies)
        return merged

    def get_profile(self, proxy: str = "", upstream: bool = True) -> ProxyProfile:
        flaresolverr_url = config.flaresolverr_url
        enabled = bool(config.flaresolverr_enabled and flaresolverr_url)
        return ProxyProfile(
            proxy_url=normalize_proxy_url(proxy),
            clearance_enabled=enabled,
            clearance_mode="flaresolverr" if enabled else "none",
            clearance={
                "url": flaresolverr_url,
                "timeout_seconds": config.flaresolverr_timeout_seconds,
                "refresh_interval_seconds": config.flaresolverr_refresh_interval_seconds,
            },
        )

    def refresh_clearance(
        self,
        target_url: str,
        proxy: str = "",
        force: bool = False,
        upstream: bool = True,
    ) -> ClearanceBundle | None:
        profile = self.get_profile(proxy=proxy, upstream=upstream)
        if not profile.clearance_enabled:
            return None
        target_host = _host_from_url(target_url)
        key = self._cache_key(profile, target_host)
        cached = self._get_cached_bundle(key)
        if cached is not None and not force and cached.is_valid_for(target_host, profile.proxy_url):
            return cached

        lock = self._get_flight_lock(key)
        with lock:
            cached = self._get_cached_bundle(key)
            if cached is not None and not force and cached.is_valid_for(target_host, profile.proxy_url):
                return cached
            provider = FlareSolverrClearanceProvider(str(profile.clearance.get("url") or ""))
            bundle = provider.get_clearance(target_url, profile.proxy_url, profile.timeout_sec)
            if bundle is None:
                return cached
            bundle = replace(bundle, expires_at=time.time() + profile.refresh_interval)
            with self._lock:
                self._clearance_cache[key] = bundle
            return bundle

    def get_runtime_status(self) -> dict[str, Any]:
        profile = self.get_profile(upstream=True)
        with self._lock:
            cached_hosts = sorted({key[2] for key in self._clearance_cache})
        return {
            "clearance_enabled": profile.clearance_enabled,
            "clearance_mode": profile.clearance_mode,
            "has_clearance_bundle": bool(cached_hosts),
            "cached_clearance_hosts": cached_hosts,
        }

    def clear_cache(self) -> None:
        with self._lock:
            self._clearance_cache.clear()

    def _cache_key(self, profile: ProxyProfile, target_host: str) -> tuple[str, str, str]:
        return (str(profile.clearance.get("url") or "").rstrip("/"), profile.proxy_url, _normalize_host(target_host))

    def _get_cached_bundle(self, key: tuple[str, str, str]) -> ClearanceBundle | None:
        with self._lock:
            return self._clearance_cache.get(key)

    def _get_flight_lock(self, key: tuple[str, str, str]) -> threading.Lock:
        with self._lock:
            return self._flight_locks.setdefault(key, threading.Lock())


def test_flaresolverr(
    flaresolverr_url: str,
    target_url: str = "https://auth.openai.com/",
    proxy: str = "",
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        bundle = FlareSolverrClearanceProvider(flaresolverr_url).get_clearance(
            target_url,
            proxy_url=proxy,
            timeout_sec=timeout_seconds,
            raise_errors=True,
        )
        if bundle is None:
            raise RuntimeError("FlareSolverr returned no clearance bundle")
        return {
            "ok": True,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "target_host": bundle.target_host,
            "cookie_count": len(bundle.cookies),
            "has_cf_clearance": bool(bundle.cookies.get("cf_clearance")),
            "user_agent": bundle.user_agent,
        }
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "error": str(exc),
        }


proxy_settings = ProxySettings()
