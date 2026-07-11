from __future__ import annotations

import compileall
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET = PROJECT_ROOT / "account_register_manager" / "register"
UTILS_DIR = TARGET / "utils"

# OpenAI register worker imports a couple of helpers from the upstream
# ``utils/`` package. ``openai_register.py`` itself does NOT reference any
# ``utils`` symbol, but its runtime needs (PKCE generation, sentinel PoW
# token) come from there. We vendor the minimal subset under
# ``account_register_manager/register/utils/`` and rewrite the imports to
# use the vendored copy.
VENDORED_UTILS = ("pkce.py", "sentinel.py", "turnstile.py")


def find_upstream_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "services" / "register" / "openai_register.py").is_file():
            return parent
    env_file = PROJECT_ROOT / ".upstream-chatgpt2api"
    if env_file.is_file():
        candidate = Path(env_file.read_text(encoding="utf-8").strip())
        if (candidate / "services" / "register" / "openai_register.py").is_file():
            return candidate
    raise SystemExit(
        "Cannot find upstream chatgpt2api checkout. "
        "Run this script inside the extracted repo under chatgpt2api, or write an upstream path to .upstream-chatgpt2api."
    )


def patch_openai_register(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace("from datetime import datetime, timezone", "from datetime import datetime")
    text = text.replace(
        "from services.account_service import account_service",
        "from account_register_manager.account_service import account_service",
    )
    text = text.replace(
        "from services.json_file import read_json_object",
        "from account_register_manager.json_file import read_json_object",
    )
    text = text.replace(
        "from services.register import mail_provider",
        "from account_register_manager.register import mail_provider",
    )
    if "from account_register_manager.config import DATA_DIR" not in text:
        if "from utils.timezone import TIME_FORMAT, beijing_now_str" in text:
            text = text.replace(
                "from utils.timezone import TIME_FORMAT, beijing_now_str",
                "from account_register_manager.config import DATA_DIR\n"
                "from account_register_manager.time_utils import now_beijing_iso",
            )
        else:
            text = text.replace(
                "from account_register_manager.account_service import account_service",
                "from account_register_manager.account_service import account_service\n"
                "from account_register_manager.config import DATA_DIR",
            )
    text = text.replace(
        "from services.proxy_service import ClearanceBundle, proxy_settings",
        "from account_register_manager.proxy_service import ClearanceBundle, proxy_settings",
    )
    if "from account_register_manager.time_utils import now_beijing_iso" not in text:
        text = text.replace(
            "from account_register_manager.register import mail_provider",
            "from account_register_manager.register import mail_provider\n"
            "from account_register_manager.time_utils import now_beijing_iso",
        )
    # Rewrite the upstream ``utils`` imports to the vendored copy living
    # next to this module (``account_register_manager/register/utils``).
    text = text.replace(
        "from utils.pkce import generate_pkce as _generate_pkce",
        "from .utils.pkce import generate_pkce as _generate_pkce",
    )
    text = text.replace(
        "from utils.sentinel import (",
        "from .utils.sentinel import (",
    )
    text = text.replace(
        "from utils.sentinel import SentinelTokenGenerator, build_sentinel_token as _build_sentinel_token_tuple",
        "from .utils.sentinel import SentinelTokenGenerator, build_sentinel_token as _build_sentinel_token_tuple",
    )
    text = text.replace(
        'register_config_file = base_dir.parents[1] / "data" / "register.json"',
        'register_config_file = DATA_DIR / "register.json"',
    )
    text = text.replace(
        'print(f"{prefix}{datetime.now().strftime(\'%H:%M:%S\')} {text}{suffix}")',
        'print(f"{prefix}{now_beijing_iso()[11:19]} {text}{suffix}")',
    )
    text = text.replace(
        'print(f"{prefix}{beijing_now_str(TIME_FORMAT)} {text}{suffix}")',
        'print(f"{prefix}{now_beijing_iso()[11:19]} {text}{suffix}")',
    )
    text = text.replace(
        '"created_at": datetime.now(timezone.utc).isoformat(),',
        '"created_at": now_beijing_iso(),',
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_mail_provider(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from services.config import DATA_DIR",
        "from account_register_manager.config import DATA_DIR",
    )
    text = text.replace(
        "from services.json_file import read_json_file, write_json_file",
        "from account_register_manager.json_file import read_json_file, write_json_file",
    )
    text = text.replace(
        "from services.proxy_service import proxy_settings",
        "from account_register_manager.proxy_service import proxy_settings",
    )
    if 'if entry["type"] == "freemail":' not in text:
        text = text.replace(
            '    if entry["type"] == "tempmail_lol":\n'
            '        return TempMailLolProvider(entry, conf)',
            '    if entry["type"] == "freemail":\n'
            '        from account_register_manager.register.freemail_provider import FreeMailProvider\n'
            '\n'
            '        return FreeMailProvider(entry, conf)\n'
            '    if entry["type"] == "tempmail_lol":\n'
            '        return TempMailLolProvider(entry, conf)',
        )
    text = text.replace(
        '    for item in mail_config["providers"]:\n'
        '        idx = len(result) + 1\n'
        '        t = item.get("type", "")\n'
        '        cnt = counters.get(t, 0) + 1\n'
        '        counters[t] = cnt\n'
        '        label = f"DDG-{cnt}" if t == "ddg_mail" else f"{t}#{idx}"\n'
        '        result.append({**item, "provider_ref": f"{item[\'type\']}#{idx}", "label": label})',
        '    for item in mail_config.get("providers") or []:\n'
        '        if not isinstance(item, dict):\n'
        '            continue\n'
        '        idx = len(result) + 1\n'
        '        t = str(item.get("type") or "").strip()\n'
        '        if not t:\n'
        '            continue\n'
        '        cnt = counters.get(t, 0) + 1\n'
        '        counters[t] = cnt\n'
        '        label = f"DDG-{cnt}" if t == "ddg_mail" else f"{t}#{idx}"\n'
        '        result.append({**item, "type": t, "provider_ref": f"{t}#{idx}", "label": label})',
    )
    text = text.replace(
        '    for item in mail_config["providers"]:\n'
        '        idx = len(result) + 1\n'
        '        t = item.get("type", "")\n'
        '        cnt = counters.get(t, 0) + 1\n'
        '        counters[t] = cnt\n'
        '        label = f"DDG-{cnt}" if t == "ddg_mail" else f"{t}#{idx}"\n'
        '        stable_id = str(item.get("id") or item.get("provider_id") or "").strip()\n'
        '        provider_ref = f"{item[\'type\']}:{stable_id}" if stable_id else f"{item[\'type\']}#{idx}"\n'
        '        result.append({**item, "provider_ref": provider_ref, "label": label})',
        '    for item in mail_config.get("providers") or []:\n'
        '        if not isinstance(item, dict):\n'
        '            continue\n'
        '        idx = len(result) + 1\n'
        '        t = str(item.get("type") or "").strip()\n'
        '        if not t:\n'
        '            continue\n'
        '        cnt = counters.get(t, 0) + 1\n'
        '        counters[t] = cnt\n'
        '        label = f"DDG-{cnt}" if t == "ddg_mail" else f"{t}#{idx}"\n'
        '        stable_id = str(item.get("id") or item.get("provider_id") or "").strip()\n'
        '        provider_ref = f"{t}:{stable_id}" if stable_id else f"{t}#{idx}"\n'
        '        result.append({**item, "type": t, "provider_ref": provider_ref, "label": label})',
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_sentinel(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from utils.turnstile import solve_turnstile_token",
        "from .turnstile import solve_turnstile_token",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def sync_vendored_utils(upstream_root: Path) -> None:
    """Copy the small subset of ``utils/`` modules that the register worker
    needs into ``account_register_manager/register/utils/`` so the package
    is fully self-contained (no dependency on the upstream checkout)."""
    UTILS_DIR.mkdir(parents=True, exist_ok=True)
    # Always keep the package marker file in sync.
    upstream_init = upstream_root / "utils" / "__init__.py"
    if upstream_init.is_file():
        shutil.copy2(upstream_init, UTILS_DIR / "__init__.py")
    else:
        # Upstream ships an empty __init__.py; recreate it if missing.
        (UTILS_DIR / "__init__.py").write_text("", encoding="utf-8")
    for name in VENDORED_UTILS:
        src = upstream_root / "utils" / name
        dst = UTILS_DIR / name
        if not src.is_file():
            raise SystemExit(f"upstream file missing: {src}")
        shutil.copy2(src, dst)


def main() -> None:
    upstream_root = find_upstream_root()
    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copy2(upstream_root / "services" / "register" / "openai_register.py", TARGET / "openai_register.py")
    shutil.copy2(upstream_root / "services" / "register" / "mail_provider.py", TARGET / "mail_provider.py")
    sync_vendored_utils(upstream_root)
    patch_openai_register(TARGET / "openai_register.py")
    patch_mail_provider(TARGET / "mail_provider.py")
    patch_sentinel(UTILS_DIR / "sentinel.py")
    ok = compileall.compile_dir(str(PROJECT_ROOT / "account_register_manager"), quiet=1)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
