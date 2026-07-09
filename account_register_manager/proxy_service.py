from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClearanceBundle:
    user_agent: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    target_host: str = ""


@dataclass
class ProxyProfile:
    clearance_enabled: bool = False


class ProxySettings:
    """Small standalone compatibility layer for the upstream proxy service.

    The extracted app only needs normal outbound proxy support. The upstream
    project can optionally refresh Cloudflare clearance through extra services,
    which are intentionally not bundled here, so those calls are no-ops.
    """

    def build_session_kwargs(
        self,
        proxy: str = "",
        upstream: bool = True,
        impersonate: str = "chrome",
        verify: bool = False,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"impersonate": impersonate, "verify": verify}
        proxy = str(proxy or "").strip()
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        return kwargs

    def build_headers(
        self,
        headers: dict[str, str],
        target_url: str = "",
        proxy: str = "",
        upstream: bool = True,
    ) -> dict[str, str]:
        return dict(headers)

    def get_profile(self, proxy: str = "", upstream: bool = True) -> ProxyProfile:
        return ProxyProfile(clearance_enabled=False)

    def refresh_clearance(
        self,
        target_url: str,
        proxy: str = "",
        force: bool = False,
        upstream: bool = True,
    ) -> ClearanceBundle | None:
        return None


proxy_settings = ProxySettings()
