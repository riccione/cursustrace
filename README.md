# CursusTrace — Job Application Tracker

[![CI](https://github.com/riccione/cursustrace/actions/workflows/ci.yml/badge.svg)](https://github.com/riccione/cursustrace/actions/workflows/ci.yml)

Track job applications by scanning a listing URL and saving its details to a local
SQLite database.

Curated job board lists by region live in [`job-sources/`](job-sources/).

## Setup

Requires [uv](https://docs.astral.sh/uv/). Install dependencies:

```sh
uv sync
```

PDF export uses [WeasyPrint](https://weasyprint.org/), which relies on the
Pango, Cairo, and GDK-Pixbuf system libraries.

## Run

Install and launch the app:

```sh
uv sync
uv run cursustrace
```

The [NiceGUI](https://nicegui.io/) server starts on
[http://localhost:8080](http://localhost:8080). Use `cursustrace run` to pass
options explicitly, e.g. `uv run cursustrace run --host 127.0.0.1 --port 8090`.

## Configuration

Settings are resolved with the precedence **CLI flags > environment variables >
`cursustrace.toml` > defaults**:

- **`cursustrace.toml`** in the working directory (or a path via `--config`):

  ```toml
  [cursustrace]
  host = "127.0.0.1"
  port = 8090
  reload = false
  show = false
  cv_style = "styles/cv.css"
  log_level = "error"
  log_retention_days = 7
  backup_dir = "backups"
  backup_keep = 14
  backup_on_start = true
  ```

- **Environment variables:** `CURSUS_HOST`, `CURSUS_PORT`, `CURSUS_RELOAD`,
  `CURSUS_SHOW`, `CURSUS_CV_STYLE`, `CURSUS_LOG_LEVEL`, `CURSUS_LOG_RETENTION_DAYS`,
  `CURSUS_BACKUP_DIR`, `CURSUS_BACKUP_KEEP`, `CURSUS_BACKUP_ON_START`.
- **`cursustrace run` flags:** `--host`, `--port`, `--reload/--no-reload`,
  `--show/--no-show`, `--css PATH`, `--config PATH`.
- **Group flag (all commands):** `--log-level`, e.g.
  `cursustrace --log-level debug scan URL`.

`cv_style` / `--css` points at the PDF stylesheet used by **📄 Export to PDF**
(see below). On start the dashboard prints which source it resolved from:
`Config: using config file cursustrace.toml` or `Config: no config file found,
using hardcoded default values`.

### Logging

Errors are written to a daily file under `logs/cursustrace-YYYY-MM-DD.log`
(appended across runs). The default level is `error`; set `log_level`
(one of `debug`, `info`, `warning`, `error`, `critical`) via `cursustrace.toml`,
`CURSUS_LOG_LEVEL`, or the group-level `--log-level` flag. Files older than
`log_retention_days` (default `7`, `0` disables pruning) are deleted on startup.

### Backups

Every dashboard start writes a consistent copy of the SQLite database into
`backup_dir` (default `backups/`) as `cursustrace-YYYYMMDD-HHMMSS.db`, using
SQLite's online backup API. Only the newest `backup_keep` copies are retained
(default `14`, `0` keeps everything), and only files matching our
`cursustrace-*.db` pattern are ever pruned. Set `backup_on_start = false` (or
`CURSUS_BACKUP_ON_START=0`) to skip the startup copy; a failed backup is logged
and never stops the app. The outcome is printed to the shell on start, e.g.
`Backup completed successfully to backups/cursustrace-20260930-153012.db`, or
`Backup skipped: backup_on_start is disabled` / `Backup failed: <error>`.

Use `cursustrace backup` for an on-demand copy — it honours the same settings,
with `--dir` and `--keep` as overrides. Schedule it from cron if you also run
the app as a service:

```sh
# every day at 03:15
15 3 * * * cd /path/to/cursustrace && uv run cursustrace backup --json
```

**Restore:** stop the app, copy the chosen backup over `data/cursustrace.db`,
delete leftover `data/cursustrace.db-wal` / `data/cursustrace.db-shm` files,
then start the app again.

Paste one or more job listing URLs (one per line), click **Scan & Save
Positions**, then use the **Unapplied**, **Applied**, **Interview**, and
**Rejected** tabs to manage each position through the pipeline. Toggle
**Applied**, **Interview**, or **Rejected** on a card to move it between tabs.
Select **View full details** on a card to open the complete scraped description
and metadata. The **Search company** box matches fuzzy company names across
**all** pipeline stages at once (each result shows its status, e.g.
`[Applied]`).

When scraping fails (or for a listing you track by hand), use **➕ Add Manually**
to enter a position's URL, title, company, and description, and edit those fields
from the position's detail page.

Use the **👤 Profile & CV Editor** tab to set your contact details and edit
your CV in Markdown as four sections — Summary, Work History, Education, and
Skills — with a live preview combining them in export order. Your profile and CV
are stored only in the SQLite database, and **📄 Export to PDF** downloads a
formatted document combining your contact header with the sections in that
order. When you keep more than one CV (e.g. per role or seniority), a dropdown
at the top of the tab switches between named profiles: **➕** adds a new
profile, and **Delete profile** removes the selected one after you type
`DELETE` to confirm. The selected profile persists across restarts, and PDF
export always uses it.

The **📊 Statistics** tab shows how many positions you have in total and in each
pipeline stage (unapplied, applied, interview, rejected), with charts for the
status distribution, applications per month, and a response funnel from added
to applied, responded, and interview.

## Customizing the CV PDF style

The PDF stylesheet lives at [`styles/cv.css`](styles/cv.css). Edit it to change
the fonts, margins, colours, spacing, or page footer — the file is read on every
export, so changes apply immediately without restarting the app.

## Command-line ingestion (for AI agents)

The `cursustrace` command also offers headless subcommands for adding positions
without the UI (duplicates are skipped, never overwritten). Pass `--json` for
machine-readable output.

```sh
# add one position from explicit fields
uv run cursustrace add --url https://example.com/job/1 \
  --title "Backend Engineer" --company Acme --description "Build APIs" \
  --location Remote --status applied --tag remote --json

# scrape and add one or more URLs (repeatable --tag labels the whole batch)
uv run cursustrace scan https://example.com/job/1 https://example.com/job/2 --tag qa --json

# discover candidate listing links from a source page (URL or job-sources site name)
uv run cursustrace discover RemoteOK --json
uv run cursustrace discover https://example.com/jobs --add --tag qa --json

# batch import a JSON array (structured items and/or URL-only items) from a file or stdin
uv run cursustrace import jobs.json --json
echo '[{"url": "https://example.com/job/1"}]' | uv run cursustrace import --json

# list stored positions (repeatable --tag filters match any tag)
uv run cursustrace list --status applied --json
uv run cursustrace list --tag remote --tag startup --json

# sort (newest, oldest, company, company_desc, status) and paginate
uv run cursustrace list --sort company --limit 50 --json
uv run cursustrace list --sort status --limit 50 --offset 50 --json

# export positions as import-compatible JSON (stdout by default) or CSV
uv run cursustrace export
uv run cursustrace export jobs.json
uv run cursustrace export --format csv --status applied
uv run cursustrace export --min-salary 60000 --salary-currency EUR
uv run cursustrace export --tag remote --format csv

# manage tags (labels): counts, creation, rename, delete
uv run cursustrace tags list --json
uv run cursustrace tags add remote referral --json
uv run cursustrace tags rename remote wfh
uv run cursustrace tags rm wfh

# position counts per pipeline stage
uv run cursustrace stats --json

# write a database backup (retention settings are documented under Backups)
uv run cursustrace backup --json
uv run cursustrace backup --dir /mnt/backup --keep 30

# show the version
uv run cursustrace -V

# delete positions by id, URL, a URL list file, or the list filters;
# preview + confirmation prompt unless --yes (--json requires --yes)
uv run cursustrace delete 42 43 --yes
uv run cursustrace delete --url https://example.com/job/1 --yes
uv run cursustrace delete --url-file urls.txt --yes --json
uv run cursustrace delete --status rejected --tag spam --yes

# delete ALL job positions at once (or everything with --all); --yes skips the prompt
uv run cursustrace clear --yes
uv run cursustrace clear --all --yes
```

Each JSON item may be structured (`url`, `title`, `company`, `location`,
`description`, `status`, `salary_*`, `tags`) or URL-only (the URL is scraped to
fill the missing fields). Scraped positions resolve `location` from the page's
Open Graph meta, then its JSON-LD `JobPosting` address, then a `📍Location:`
line in the description. `export` writes that same shape, so
`cursustrace export jobs.json` can be edited and fed back into `import`: status,
stage comments, and tags survive the round trip (unknown tags are created),
timestamps are re-stamped on import. The CSV variant is a flat spreadsheet view
with tags joined by `;` — `import` reads JSON only.

### Discovering positions with an AI agent

`discover` fetches one source page (a search or board page), extracts candidate
listing links, and checks each against the database. It never talks to an LLM —
the reasoning is done by an AI agent (or you) on top of its JSON output:

```sh
# by site name from job-sources/ or by URL; --limit caps candidates (default 100)
uv run cursustrace discover RemoteOK --json
uv run cursustrace discover https://example.com/jobs --limit 50 --json

# or scrape and add every new link in one step (no agent needed)
uv run cursustrace discover https://example.com/jobs --add --json
```

The JSON payload reports `source` (input, resolved URL, job-sources status),
`extracted` / `new_count` / `known_count`, `known_by_status` (so already-applied
positions are visible), and one `candidates` entry per link — `known: false`
for new ones, plus `status`, `id`, `title` for known ones. With `--add` it also
returns `added`, `skipped`, `errors` and `similar`, and exits non-zero when any
scrape failed; a failed fetch prints an `error` key. Candidates that are already
in the database are skipped whatever their stage, so discovery never re-adds an
applied position.

A ready-to-paste workflow for an AI agent:

> Discover positions for me from `<source>`. Run
> `cursustrace discover <source> --json`, review the candidates with
> `known: false` and keep only the ones matching my profile, then run
> `cursustrace scan <chosen urls> --json` and summarize what was added, what
> was already tracked, and what you skipped and why.

## MCP server

CursusTrace ships a [Model Context Protocol](https://modelcontextprotocol.io)
server so AI agents can drive the tracker over structured tool calls instead of
shelling out to the CLI:

```sh
uv run cursustrace mcp   # stdio transport; blocks until the client disconnects
```

It exposes 12 tools: `list_positions`, `get_position`, `get_stats`,
`list_tags`, `add_position`, `scan`, `discover`, `update_status`, `set_tags`,
`delete_positions`, `rename_tag`, and `delete_tag`. Tool failures come back
with `is_error` set and the real reason, adding never overwrites (known URLs
report as duplicates), and `delete_positions` is preview-first: call it with
`confirm=false` to see what matches, then repeat with `confirm=true` to
delete. The database path is relative to the server's working directory, so
the MCP client's cwd decides which tracker it talks to.

### opencode

This repository registers the server for [opencode](https://opencode.ai) in
`opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "cursustrace": {
      "type": "local",
      "command": ["uv", "run", "cursustrace", "mcp"],
      "enabled": true
    }
  }
}
```

Restart opencode after editing it — config is not hot-reloaded. Any other MCP
client works the same way: point it at `uv run cursustrace mcp` over stdio.

## Development

```sh
uv run pytest          # tests
uv run mypy src tests  # type checking
uv run ruff check src tests
uv run ruff format src tests
```

Install the git hooks (ruff check `--fix`, ruff format, pytest) with:

```sh
uv run pre-commit install
uv run pre-commit run --all-files   # run the hooks across the repo
```

The database lives at `data/cursustrace.db` and is created on first run.

## Contributing

Contributions are welcome as pull requests against `main`.

### Commit messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/):
`<type>(<scope>): <summary>` — for example, `feat(app): add history timeline`.
Use the imperative mood, lower case, no trailing period. The scope is optional.

Types in use:

- `feat` — a new feature
- `fix` — a bug fix
- `refactor` — a change that neither fixes a bug nor adds a feature
- `perf` — a performance improvement
- `docs` — documentation only
- `style` — formatting or whitespace, no behaviour change
- `chore` — maintenance, tooling, dependencies
- `ci` — CI/CD workflows
- `build` — build system or packaging

### Pull requests

Every pull request should explain:

- **Why** — the purpose and the problem it solves.
- **What changed** — a short summary of the changes.
- **Technical details** — brief notes on the approach, key files, and trade-offs.

Before requesting review, confirm:

- [ ] `uv run ruff check src tests`
- [ ] `uv run ruff format src tests`
- [ ] `uv run mypy src tests`
- [ ] `uv run pytest`

## License

Licensed under the [Apache License 2.0](LICENSE).
