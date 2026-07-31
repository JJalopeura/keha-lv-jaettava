"""Sub-routers for the LV demo application."""

from __future__ import annotations
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter
from jinja2 import StrictUndefined
from fastapi.templating import Jinja2Templates

from .applicant import create_applicant_router
from .authority import create_authority_router
from .api_v1 import create_api_v1_router
from .admin import create_admin_router
from .demo_lab import create_demo_lab_router
from .common import RouteContext, FINDING_CATEGORY_LABELS, finnish_datetime


def create_lupademo_router(db_getter: Callable[[], Any], settings_getter: Callable[[], Any]) -> APIRouter:
    _domain_dir = Path(__file__).resolve().parent.parent
    _templates = Jinja2Templates(
        directory=str(_domain_dir / "templates"), undefined=StrictUndefined,
    )
    _templates.env.filters["finnish_datetime"] = finnish_datetime
    _templates.env.globals["FINDING_CATEGORY_LABELS"] = FINDING_CATEGORY_LABELS
    ctx = RouteContext(
        templates=_templates,
        db_getter=db_getter,
        settings_getter=settings_getter,
    )
    router = APIRouter()
    router.include_router(create_applicant_router(ctx))
    router.include_router(create_authority_router(ctx))
    router.include_router(create_api_v1_router(ctx))
    router.include_router(create_admin_router(ctx))
    router.include_router(create_demo_lab_router(ctx))
    return router


__all__ = ["create_lupademo_router"]
