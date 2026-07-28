"""Sub-routers for the LV demo application."""

from __future__ import annotations
from typing import Any, Callable

from fastapi import APIRouter

from .applicant import create_applicant_router
from .authority import create_authority_router
from .api_v1 import create_api_v1_router
from .admin import create_admin_router
from .demo_lab import create_demo_lab_router


def create_lupademo_router(db_getter: Callable[[], Any], settings_getter: Callable[[], Any]) -> APIRouter:
    router = APIRouter()
    router.include_router(create_applicant_router(db_getter, settings_getter))
    router.include_router(create_authority_router(db_getter, settings_getter))
    router.include_router(create_api_v1_router(db_getter, settings_getter))
    router.include_router(create_admin_router(db_getter, settings_getter))
    router.include_router(create_demo_lab_router(db_getter, settings_getter))
    return router


__all__ = ["create_lupademo_router"]
