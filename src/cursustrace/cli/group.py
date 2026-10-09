"""Click command group, version and log-level options, and the run/mcp launchers."""

from __future__ import annotations

import click

from cursustrace.config import LOG_LEVELS, load_settings
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
    "--log-level",
    type=click.Choice(LOG_LEVELS, case_sensitive=False),
    default=None,
    help="Logging verbosity (default from config/env).",
)
@click.pass_context
def cli(ctx: click.Context, log_level: str | None) -> None:
    """CursusTrace — job application tracker."""
    try:
        settings = load_settings(log_level=log_level)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    setup_logging(
        settings.log_level,
        retention_days=settings.log_retention_days,
        console=False,
    )
    ctx.obj = settings.log_level
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
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None)
@click.pass_context
def run_command(
    ctx: click.Context,
    host: str | None,
    port: int | None,
    reload: bool | None,
    show: bool | None,
    cv_style: str | None,
    config_path: str | None,
) -> None:
    """Launch the dashboard (host/port/config overrides)."""
    try:
        settings = load_settings(
            host=host,
            port=port,
            reload=reload,
            show=show,
            cv_style_path=cv_style,
            log_level=ctx.obj,
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
