"""Click command group, version and log-level options, and the run/mcp launchers."""

from __future__ import annotations

import click

from cursustrace import db
from cursustrace.config import LOG_LEVELS, Settings, load_settings
from cursustrace.errors import ConfigError
from cursustrace.logsetup import setup_logging


@click.group(invoke_without_command=True)
@click.version_option(
    None,
    "-V",
    "--version",
    package_name="cursustrace",
    prog_name="cursustrace",
    message="cursustrace %(version)s",
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False),
    default=None,
    help="SQLite database file (default from config/env).",
)
@click.option(
    "--log-level",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    default=None,
    help="Logging verbosity (default from config/env).",
)
@click.pass_context
def cli(ctx: click.Context, db_path: str | None, log_level: str | None) -> None:
    """CursusTrace: job application tracker."""
    try:
        settings = load_settings(db_path=db_path, log_level=log_level)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    setup_logging(
        settings.log_level,
        retention_days=settings.log_retention_days,
        console=False,
    )
    # Everything below this point reads the database through this global, so a
    # demo run can never reach the personal database.
    db.DEFAULT_DB_PATH = settings.db_path
    ctx.obj = settings
    if ctx.invoked_subcommand is None:
        from cursustrace.app import run

        run(settings)


@cli.command()
@click.option("--host", default=None, help="Host to bind (default from config/env).")
@click.option("--port", type=int, default=None, help="Port to bind.")
@click.option("--reload/--no-reload", "reload", default=None, help="Auto-reload on file changes.")
@click.option("--show/--no-show", "show", default=None, help="Open a browser tab.")
@click.option(
    "--css",
    "cv_style",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to the CV stylesheet.",
)
@click.option(
    "--multi-user/--no-multi-user",
    "multi_user",
    default=None,
    help="Enable login-gated multi-user mode (default from config/env).",
)
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None)
@click.pass_context
def run_command(
    ctx: click.Context,
    host: str | None,
    port: int | None,
    reload: bool | None,
    show: bool | None,
    cv_style: str | None,
    multi_user: bool | None,
    config_path: str | None,
) -> None:
    """Launch the dashboard (host/port/config overrides)."""
    group: Settings = ctx.obj
    try:
        settings = load_settings(
            host=host,
            port=port,
            reload=reload,
            show=show,
            cv_style_path=cv_style,
            multi_user=multi_user,
            db_path=group.db_path,
            log_level=group.log_level,
            config_path=config_path,
        )
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    from cursustrace.app import run

    run(settings)


@cli.command(name="mcp")
def mcp_command() -> None:
    """Run the MCP server over stdio for AI agent access."""
    from cursustrace.mcp_server import main as mcp_main

    mcp_main()
