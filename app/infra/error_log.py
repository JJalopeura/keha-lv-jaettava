"""Append unhandled request errors to a file on the durable /home volume.

App Service's platform logs only show container lifecycle, and this deployment has no
Log Analytics, so an intermittent HTTP 500 otherwise leaves no traceback behind. The
middleware writes the traceback to `<db dir>/errors.log` and re-raises, so the response
is exactly what Starlette would have produced.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from starlette.types import ASGIApp, Receive, Scope, Send


class RequestErrorLogMiddleware:
    def __init__(self, app: ASGIApp, log_path: str) -> None:
        self.app = app
        self.log_path = Path(log_path)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception:
            self._write(scope)
            raise

    MAX_BYTES = 1_000_000

    def _write(self, scope: Scope) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > self.MAX_BYTES:
                # One rollover is enough for a demo box; the point is that the file
                # cannot grow without bound between deploys.
                self.log_path.replace(self.log_path.with_suffix(self.log_path.suffix + ".1"))
            entry = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "method": scope.get("method"),
                "path": scope.get("path"),
                # No query string: it can carry applicant-entered values.
                "traceback": traceback.format_exc(),
            }
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            # Logging must never be the reason a request fails.
            pass
