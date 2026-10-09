"""Compatibility entrypoint: the dashboard now lives in cursustrace.web."""

from __future__ import annotations

from cursustrace.web.run import run

__all__ = ["run"]

if __name__ in {"__main__", "__mp_main__"}:
    run()
