from __future__ import annotations

from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))


def now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def now_beijing_iso() -> str:
    return now_beijing().isoformat()


def parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BEIJING_TZ)
    return parsed
