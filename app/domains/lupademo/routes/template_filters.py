from __future__ import annotations

from datetime import datetime
from typing import Any


def finnish_datetime(value: Any) -> str:
    if value is None or value == "":
        return ""

    if isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value)
        return parsed.strftime("%d.%m.%Y klo %H.%M")

    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text

    if len(text) == 10:
        return parsed.strftime("%d.%m.%Y")
    return parsed.strftime("%d.%m.%Y klo %H.%M")