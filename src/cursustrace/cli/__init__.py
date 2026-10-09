"""Command-line interface; the console script targets ``cursustrace.cli:cli``."""

from __future__ import annotations

from cursustrace.cli import discover_cmd as discover_cmd
from cursustrace.cli import ingest_cmds as ingest_cmds
from cursustrace.cli import manage as manage
from cursustrace.cli import query as query
from cursustrace.cli.group import cli

__all__ = ["cli"]
