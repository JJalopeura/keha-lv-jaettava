"""Guard test: every HTTP route in the app must be documented in the route inventory.

Keeps `docs/route-inventory.md` from drifting. FastAPI exposes the full route
table via `app.routes`; this test parses the documented paths out of the
inventory markdown and asserts they match.

Stdlib unittest on purpose: no pytest dependency.
    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root

os.environ["KEHA_LV_FORCE_MOCK_LLM"] = "true"

from fastapi import FastAPI  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from app.domains.lupademo.routes import create_lupademo_router  # noqa: E402
from app.infra.config import load_settings  # noqa: E402
from app.infra.db import DB  # noqa: E402

# Build an isolated app so this test never touches the shared `app.main` state
# that test_smoke.py relies on (which asserts exact attachment counts).
os.environ["KEHA_LV_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="keha-routes-"), "test.db")
os.environ["KEHA_LV_UPLOADS_DIR"] = tempfile.mkdtemp(prefix="keha-routes-uploads-")
_settings = load_settings()
Path(_settings.uploads_dir).mkdir(parents=True, exist_ok=True)
_db = DB(_settings.db_path)
app = FastAPI(title="KEHA LV AI-demo (route inventory test)")
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parents[1] / "app" / "domains" / "lupademo" / "static")),
    name="static",
)
app.include_router(create_lupademo_router(lambda: _db, lambda: _settings))

# Framework / infra routes that are intentionally NOT part of the business
# route inventory (they are not defined in routes/*.py).
EXCLUDED_PATHS = {
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/static",
    "/healthz",
}

INVENTORY = Path(__file__).resolve().parents[1] / "docs" / "route-inventory.md"


def _app_routes() -> set[tuple[str, str]]:
    """Return {(method, path)} for every business route registered on the app."""
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or path in EXCLUDED_PATHS:
            continue
        methods = getattr(route, "methods", None) or []
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            routes.add((method.upper(), path))
    return routes


def _documented_routes() -> set[tuple[str, str]]:
    """Return {(method, path)} parsed from the route-inventory markdown tables."""
    text = INVENTORY.read_text(encoding="utf-8")
    routes: set[tuple[str, str]] = set()
    # Each table row looks like: | GET | `/path` | handler | ... |
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        method = cells[0].upper()
        path = cells[1].strip("`")
        if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
            continue
        if not path.startswith("/"):
            continue
        routes.add((method, path))
    return routes


class RouteInventory(unittest.TestCase):
    def test_every_app_route_is_documented(self) -> None:
        app_routes = _app_routes()
        documented = _documented_routes()

        missing = sorted(app_routes - documented)
        self.assertEqual(
            missing,
            [],
            "Routes exist in the app but are missing from docs/route-inventory.md:\n"
            + "\n".join(f"  {m} {p}" for m, p in missing),
        )

    def test_every_documented_route_exists_in_app(self) -> None:
        app_routes = _app_routes()
        documented = _documented_routes()

        stale = sorted(documented - app_routes)
        self.assertEqual(
            stale,
            [],
            "Routes are documented in docs/route-inventory.md but no longer exist in the app:\n"
            + "\n".join(f"  {m} {p}" for m, p in stale),
        )

    def test_route_count_is_reasonable(self) -> None:
        """Sanity check so a broken parser doesn't silently pass with zero routes."""
        self.assertGreater(len(_app_routes()), 50, "Expected a substantial route table")


if __name__ == "__main__":
    unittest.main()
