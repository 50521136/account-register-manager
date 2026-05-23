from __future__ import annotations

import compileall
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGET = PROJECT_ROOT / "account_register_manager" / "register"


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
    text = text.replace(
        "from services.account_service import account_service",
        "from account_register_manager.account_service import account_service",
    )
    text = text.replace(
        "from services.register import mail_provider",
        "from account_register_manager.register import mail_provider",
    )
    if "from account_register_manager.config import DATA_DIR" not in text:
        text = text.replace(
            "from account_register_manager.account_service import account_service",
            "from account_register_manager.account_service import account_service\n"
            "from account_register_manager.config import DATA_DIR",
        )
    text = text.replace(
        'register_config_file = base_dir.parents[1] / "data" / "register.json"',
        'register_config_file = DATA_DIR / "register.json"',
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_mail_provider(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from services.config import DATA_DIR",
        "from account_register_manager.config import DATA_DIR",
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    upstream_root = find_upstream_root()
    TARGET.mkdir(parents=True, exist_ok=True)
    shutil.copy2(upstream_root / "services" / "register" / "openai_register.py", TARGET / "openai_register.py")
    shutil.copy2(upstream_root / "services" / "register" / "mail_provider.py", TARGET / "mail_provider.py")
    patch_openai_register(TARGET / "openai_register.py")
    patch_mail_provider(TARGET / "mail_provider.py")
    ok = compileall.compile_dir(str(PROJECT_ROOT / "account_register_manager"), quiet=1)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
