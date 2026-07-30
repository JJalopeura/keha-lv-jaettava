from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined
from fastapi.templating import Jinja2Templates


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


DOMAIN_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(DOMAIN_DIR / "templates"), undefined=StrictUndefined)
templates.env.filters["finnish_datetime"] = finnish_datetime
templates.env.globals["FINDING_CATEGORY_LABELS"] = {
    "missing_required_info": "Puuttuva tieto",
    "contradiction": "Ristiriita",
    "weak_description": "Puutteellinen kuvaus",
    "attachment_scope": "Liitteen käyttötarkoitus",
    "criteria_gap": "Kriteeripuute",
}