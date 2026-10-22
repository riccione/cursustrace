# CursusTrace

[![CI](https://github.com/riccione/cursustrace/actions/workflows/ci.yml/badge.svg)](https://github.com/riccione/cursustrace/actions/workflows/ci.yml)

**Stop losing track of job applications. Scan a listing in seconds, follow every application through a visual pipeline, and let your own AI agent do the busywork — all from one local dashboard.**

<!-- Add a dashboard screenshot here -->

## Features

- **Smart scraping** — A four-step fallback chain (static HTML → board APIs → headless render) pulls the title, company, location, description and deadline out of a listing URL, even from JavaScript-heavy boards.
- **Visual pipeline** — Tabbed stages `Unapplied` → `Applied` → `Interview` → `Offer`, with a linear progress tracker on every card.
- **Smart alerts** — Positions that go quiet or approach a deadline are flagged automatically and rise to the top of their tab.
- **CV management** — Keep several named CV profiles, tag each application with the one you sent, and export a formatted PDF.
- **AI-agent ready** — A Model Context Protocol server exposes 13 tools, so agents like opencode can add, track and tidy positions for you.
- **Local-first** — Everything lives in one SQLite file on your machine: no account, no cloud.

## Quick start

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run cursustrace
```

Then the [NiceGUI](https://nicegui.io/) server starts on
[http://localhost:8080](http://localhost:8080) — the dashboard is ready.

`cursustrace run` takes the options explicitly, e.g. `uv run cursustrace run --host 127.0.0.1 --port 8090`.

<details>
<summary><b>System dependencies (needed only for PDF export)</b></summary>

PDF export uses [WeasyPrint](https://weasyprint.org/), which relies on the Pango, Cairo, and GDK-Pixbuf system libraries. Everything else — scanning, the dashboard, the CLI and the MCP server — works without them.

</details>

## The Dashboard

- **The Pipeline:** Positions move through tabbed stages: `Unapplied` → `Applied` → `Interview` → `Offer`. Rejected and Outdated roles are tucked away in a "More" menu to keep your view clean.
- **Board View:** Switch the list into a Kanban board and drag a card from one stage column to the next; the archived columns appear only when you ask for them.
- **Job Cards:** Every position is a distinct, modern card showing the job title, company, status badge, and a linear progress tracker.
- **Smart Alerts:** The app automatically flags positions that need attention (e.g., 21 days without a response, or an upcoming deadline). Flagged positions rise to the top of their tab.
- **Advanced Filtering:** A single row provides fuzzy company search, salary range filters, tag filtering, and sorting.
- **CV Tagging:** Associate specific CV profiles with specific applications (saved per position) so you always know which resume you sent.
- **Discover:** The `🔍 Discover` tab lets you search curated job boards, extract candidate links, and add them in bulk.

<details>
<summary><b>Dashboard in depth</b></summary>

### The pipeline

The pipeline is the main view: positions land in the `Unapplied` tab and move
through `Applied`, `Interview`, `Offer`, `Rejected`, and `Outdated` by ticking
the matching stage checkbox on a card. The tab strip shows the four active
stages — `Unapplied`, `Applied`, `Interview` and `Offer` — each with the number
of positions it holds; the archival `Rejected` and `Outdated` stages live behind
the `More` menu next to the tabs.

### Adding positions

Adding a position is a secondary action behind the `➕ Add Job` button above
the tabs: it opens a panel where you can paste one or more job listing URLs
(one per line) and click `Scan & Save Positions`, or switch to `➕ Add Manually`
to type the details yourself.

When scraping fails (or for a listing you track by hand), use `➕ Add Manually`
in the `➕ Add Job` panel to enter a position's URL, title, company, description,
and an optional application deadline; the same fields can be edited later from
the position's detail page. The add and edit forms always open on a clean slate:
a new position starts empty, and an edit starts from the saved values, so a
cancelled change never leaks into the next opening.

### Discovering positions

Prefer to start from a board rather than a specific URL? The `🔍 Discover` tab
searches a job source for listing links and lets you pick which to add: enter a
full URL or a board name from `job-sources/` (with an optional keyword query and
an `Only live` filter), click `Discover`, then tick the candidates you want and
press `Add selected`. Listings you already track appear with their status and
cannot be re-added.

Curated job board lists by region live in [`job-sources/`](job-sources/).

### Filtering the list

Narrowing the list takes one row: a `Search company` field next to a
`Filters` button, which slides out a panel on the left holding the salary
range, the tag filter, the sort order and the rows per page. The button
carries a badge with the number of active filters, and the pagination sits
below the list. The `Search company` box matches fuzzy company names across all
pipeline stages at once, and each result carries a status badge naming its
stage.

### Board view

The `Board` switch above the list turns the pipeline into columns:
`Unapplied`, `Applied`, `Interview` and `Offer`, with `Rejected` and `Outdated`
behind the `Archived` button so parked positions stay out of the way. Drag a card
into another column to change its stage — the same change the stage checkboxes
make, with the dates and the history entry. Every card also carries a small
move menu, which is how you change a stage with a keyboard or on a touch screen,
where dragging is not available. Columns scroll on their own, and a column
holding more than 50 positions offers a `Show all` link.

### Job cards

Every position is a card: a soft, bordered surface with generous padding that
lifts off the page. The top line carries the job title, the company and a
pill-shaped status badge; below it a linear tracker shows how far the
application got — `Unapplied` → `Applied` → `Interview` → `Offer` — with the
finished stages filled in, the current stage ringed, and the future ones left
empty. A rejected or outdated position replaces the tracker with a single
line (`❌ Application rejected after 14 days`) because the pipeline ended
there. The tag, `CV:` and attention chips sit under the tracker, so they stay
visible without opening the card, and clicking a tag chip only filters the
list — it never opens the card. On narrow screens the tracker keeps its dots
and drops the stage labels. Clicking the card opens the position details.

### Position details

Select `View full details` on a card to open the complete scraped description
and metadata. The detail view has the same stage checkboxes, plus a
`Your profile` section: the `CV used for this application` dropdown records
which profile you applied with (saved per position), and that profile's
name, email, phone, LinkedIn, and GitHub fields show below, each with an
in-field copy button for pasting into application forms. The dashboard card
displays the saved choice as a `CV:` chip.

Cards and the detail page show the deadline with a relative countdown (e.g.
`2026-12-31 — in 5 days`) when one is set; scrape it from the listing's
`validThrough` date or type it as an ISO date like `2026-12-31`.

Each dashboard and detail page ends with a `↑ Go up` button that scrolls back
to the top. Every page also carries a footer at the bottom of the page (below
the content) showing the app name and version, e.g. `CursusTrace v0.1.0`.

### Smart alerts

The dashboard flags positions that need attention with a badge on the
`⏳ Unapplied` and `✅ Applied` tabs, each counting the flagged positions of
that stage. A position qualifies when:

- it is applied with no response for `stale_applied_days` (default `21`);
- it is unapplied and untouched for `stale_unapplied_days` (default `10`);
- its deadline falls within `deadline_warning_days` (default `7`), and
  already-overdue deadlines count too.

Only unapplied and applied positions can qualify: once an employer responds
the staleness clocks stop, and a deadline no longer matters after the pipeline
moves on. Flagged positions are listed first in their tab and carry a
`⚠ <reason>` chip next to their title (an imminent deadline wins over
staleness), and the detail page repeats the reason. Everything clears as soon
as positions move on. Turn the highlights off under the settings drawer's
Notifications section; the choice persists.

The badges only count positions you can still act on. Once a stale
position has also been silent past `outdated_after_days` (default `30`), the
next refresh parks it in the `Outdated` tab. Parking happens once per
position: tick `Applied`, `Interview`, or `Rejected` and it stays back in the
pipeline no matter how old its dates look.

### CV profiles

Use the `👤 Profile & CV Editor` tab to set your contact details and edit
your CV in Markdown as four sections: Summary, Work History, Education, and
Skills. A live preview combines them in export order. Your profile and CV are
stored only in the SQLite database, and `📄 Export to PDF` downloads a
formatted document combining your contact header with the sections in that
order. When you keep more than one CV (e.g. per role or seniority), a dropdown
at the top of the tab switches between named profiles: `➕` adds a new
profile, copying your name and contact details from the profile you are on,
and — via a checked-by-default checkbox — its Summary, Work History, Education
and Skills sections (uncheck it to start those sections empty). `Delete
profile` removes the selected one
after you type `DELETE` to confirm. The selected profile persists across restarts, and PDF
export always uses it.

### Statistics

The `📊 Statistics` tab shows how many positions you have in total and in each
pipeline stage (unapplied, applied, interview, offer, rejected, outdated),
with charts for the status distribution, applications per month, and a
response funnel from added to applied, responded, interview, and offer.

</details>

## CLI Usage

CursusTrace is fully scriptable via a powerful CLI. You can add, list, delete, export, and discover jobs without opening the UI.

**[View the full CLI Documentation here](docs/CLI.md)**

## Advanced Features

### Scraping

Scans extract a listing through a fallback chain; each step runs only while
the description so far is too thin (under 600 characters, or just the page
title repeated):

1. Static HTML: trafilatura and page text plus the page's metadata,
   including job descriptions embedded in inline scripts (Zoho Recruit and
   similar boards hide theirs inside JavaScript-escaped strings).
2. Known-board APIs: when the URL, or the page's canonical link,
   points at a Greenhouse, Lever, or Ashby listing, its public JSON API
   supplies the real title, company, location, description, and deadline.
3. Browser render: with `render = "auto"` (the default), pages that
   are still JavaScript shells are rendered once with
   [lightpanda](https://lightpanda.io) and the final DOM is re-extracted.
   Install `lightpanda` on your `PATH` or point `CURSUS_LIGHTPANDA` at
   the binary; each render is capped at 20 seconds. Set
   `render = "never"` (or `CURSUS_RENDER=never`) to skip this tier on
   hosts where no browser may run.
4. Headless Chrome/Chromium: if the lightpanda render still leaves too
   little text, the page is rendered again in headless Chrome or
   Chromium (`--dump-dom`), which handles sites whose JavaScript
   lightpanda cannot execute. Put the binary on your `PATH` or point
   `CURSUS_CHROMIUM` at it; the render waits up to 15 seconds and is
   capped at 45 seconds, so expect roughly half a minute per page.

If every step comes up empty the scan fails with a
`JavaScript-rendered or blocked` error and the position is left for
manual entry; partial junk is never stored. Render problems (missing
binary, timeout) are logged at `log_level = "warning"` and simply fall
through to that error.

### MCP server

CursusTrace ships a [Model Context Protocol](https://modelcontextprotocol.io)
server so AI agents can drive the tracker over structured tool calls instead of
shelling out to the CLI. Point your agent at it and it can list, scrape, add,
tag, update and delete positions for you — every action goes through the same
tools, so an agent never edits the database behind the app's back.

```sh
uv run cursustrace mcp   # stdio transport; blocks until the client disconnects
```

It exposes 13 tools: `list_positions`, `get_position`, `get_stats`,
`list_tags`, `scrape_position`, `add_position`, `scan`, `discover`,
`update_status`, `set_tags`, `delete_positions`, `rename_tag`, and
`delete_tag`. Tool failures come back with `is_error` set and the real
reason, adding never overwrites (known URLs report as duplicates), and
`delete_positions` is preview-first: call it with `confirm=false` to see
what matches, then repeat with `confirm=true` to delete. The database path
is relative to the server's working directory, so the MCP client's cwd
decides which tracker it talks to.

Adds are applicability-gated for agents: `scrape_position` returns the
scraped listing with `applicability.flags`, and `add_position`, `scan`
and `discover(add=true)` answer `status=flagged` (or a `flagged` list)
instead of ingesting when the location or description looks tied to a
specific country or work authorization. Read the listing, then retry
with `force=true` if it fits a Serbia/remote-EU applicant. Deadlines
flow through the same way: `scrape_position` returns the listing's
`deadline` (ISO date from `validThrough`), `add_position` accepts one
via its `deadline` argument (scraped when omitted), and every stored
position reports it back from `get_position` and `list_positions`.

#### opencode

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

Restart opencode after editing it; config is not hot-reloaded. Any other MCP
client works the same way: point it at `uv run cursustrace mcp` over stdio.

### Multi-user mode

Off by default. Enable it with `multi_user = true` in `cursustrace.toml`,
`CURSUS_MULTI_USER=1`, or `cursustrace run --multi-user`. The dashboard then
requires a login on every page except `/login` and `/register`, and each
account sees only its own positions, tags, CV profiles, and preferences.

On the first multi-user start a default admin account is seeded:
`admin@admin.dev` / `admin`. Sign in and change the password immediately from
the header's **Change password** button — the seeded password is public.

Registration is open: anyone who can reach `/register` can create an account.
The server binds to `0.0.0.0` by default, so it listens on every network
interface — keep it local with `host = "127.0.0.1"` in `cursustrace.toml`, or
put your own authentication (VPN, reverse proxy with basic auth, …) in front of
it before exposing it beyond localhost.

What is isolated per account:

- Positions (jobs), tags, CV profiles, and dashboard settings
  (dark mode, notification toggles, selected profile).
- History events follow their position, so they inherit the isolation.

What is shared or global:

- The danger-zone **Clear Database** button deletes *every* account's
  positions — treat it as an operator tool.
- The CLI (`cursustrace scan`, `stats`, `discover`, `add`, …) and the MCP
  server operate on the whole database without a user context.

On the first multi-user start, any pre-existing rows (positions, profiles,
tags created before authentication existed) are assigned to the seeded admin
account, so upgrading a single-user database loses nothing.

Login sessions live in server-side files under `.nicegui/` and are keyed by a
secret stored in `data/.storage-secret` (created automatically, permissions
`0600`). Deleting that file signs everyone out; session cookies themselves are
destroyed whenever the app restarts.

## Configuration

Settings resolve in this order: command-line flags, then environment variables,
then `cursustrace.toml`, then the built-in defaults.

<details>
<summary><b>Click to view full configuration options (TOML & Env Vars)</b></summary>

- `cursustrace.toml` in the working directory (or a path via `--config`):

  ```toml
  [cursustrace]
  db_path = "data/cursustrace.db"
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
  stale_applied_days = 21
  stale_unapplied_days = 10
  deadline_warning_days = 7
  outdated_after_days = 30
  render = "auto"
  multi_user = false
  ```

- Environment variables: `CURSUS_DB_PATH`, `CURSUS_HOST`, `CURSUS_PORT`, `CURSUS_RELOAD`,
  `CURSUS_SHOW`, `CURSUS_CV_STYLE`, `CURSUS_LOG_LEVEL`, `CURSUS_LOG_RETENTION_DAYS`,
  `CURSUS_BACKUP_DIR`, `CURSUS_BACKUP_KEEP`, `CURSUS_BACKUP_ON_START`,
  `CURSUS_STALE_APPLIED_DAYS`, `CURSUS_STALE_UNAPPLIED_DAYS`,
  `CURSUS_DEADLINE_WARNING_DAYS`, `CURSUS_OUTDATED_AFTER_DAYS`, `CURSUS_RENDER`,
  `CURSUS_MULTI_USER`.
- `cursustrace run` flags: `--host`, `--port`, `--reload/--no-reload`,
  `--show/--no-show`, `--css PATH`, `--config PATH`, `--multi-user/--no-multi-user`.
- Group flag (all commands): `--db PATH` picks the SQLite file, e.g.
  `cursustrace --db data/demo.db list`. It applies to every command, the
  dashboard and the MCP server.
- Group flag (all commands): `--log-level`, e.g.
  `cursustrace --log-level debug scan URL`.

`db_path` defaults to `data/cursustrace.db`. Point it elsewhere to experiment
without touching your real tracker: the path is resolved once at startup and
every part of the app uses it, so a demo run never reaches the personal file.

</details>

`cv_style` / `--css` points at the PDF stylesheet used by the
`📄 Export to PDF` button in the profile tab. On start the dashboard prints
which source it resolved from: `Config: using config file cursustrace.toml`
or `Config: no config file found, using hardcoded default values`.

### Logging

<details>
<summary><b>Click to view the logging details</b></summary>

Errors are written to a daily file under `logs/cursustrace-YYYY-MM-DD.log`
(appended across runs). The default level is `error`; set `log_level`
(one of `debug`, `info`, `warning`, `error`, `critical`) via `cursustrace.toml`,
`CURSUS_LOG_LEVEL`, or the group-level `--log-level` flag. Files older than
`log_retention_days` (default `7`, `0` disables pruning) are deleted on startup.

</details>

### Backups

<details>
<summary><b>Click to view the backup details</b></summary>

Every dashboard start writes a consistent copy of the SQLite database into
`backup_dir` (default `backups/`) as `cursustrace-YYYYMMDD-HHMMSS.db`, using
SQLite's online backup API. Only the newest `backup_keep` copies are retained
(default `14`, `0` keeps everything), and only files matching the
`cursustrace-*.db` pattern are ever pruned. Set `backup_on_start = false` (or
`CURSUS_BACKUP_ON_START=0`) to skip the startup copy; a failed backup is logged
and never stops the app. The outcome is printed to the shell on start, e.g.
`Backup completed successfully to backups/cursustrace-20260930-153012.db`, or
`Backup skipped: backup_on_start is disabled` / `Backup failed: <error>`.

Use `cursustrace backup` for an on-demand copy; it honours the same settings,
with `--dir` and `--keep` as overrides. Schedule it from cron if you also run
the app as a service:

```sh
# every day at 03:15
15 3 * * * cd /path/to/cursustrace && uv run cursustrace backup --json
```

To restore a backup, stop the app, copy the chosen backup over
`data/cursustrace.db`, delete leftover `data/cursustrace.db-wal` /
`data/cursustrace.db-shm` files, then start the app again.

</details>

## Customizing the CV PDF style

The PDF stylesheet lives at [`styles/cv.css`](styles/cv.css). Edit it to change
the fonts, margins, colours, spacing, or page footer. The file is read on every
export, so changes apply immediately without restarting the app.

## Customizing the webapp CSS

Global UI styles live at [`styles/webapp.css`](styles/webapp.css): font sizes,
Quasar overrides, markdown overflow, the header tab rules, and the color
system. Unlike the CV stylesheet, it is read once at startup, so restart the
app after editing.

### Color system

The palette is defined once as CSS variables and overridden for the dark
theme, so both themes stay in sync:

| Variable | Purpose |
|---|---|
| `--bg-base`, `--bg-surface`, `--bg-header` | page, card and header backgrounds |
| `--text-primary`, `--text-secondary` | body and muted text |
| `--border-color` | dividers, card and row outlines |
| `--primary`, `--primary-hover`, `--primary-text` | primary buttons, active tabs |
| `--success`, `--warning`, `--danger`, `--info`, `--offer` | stage dots, badges and alerts |
| `--badge-*-text`, `--badge-*-soft` | status badge text and its background tint |

Light and dark values are set in `:root` and `body.body--dark`; Quasar's own
brand variables are remapped onto them, so components follow the palette
automatically. `tests/test_design_system.py` checks that every token exists in
both themes and that each color pair meets its WCAG contrast ratio, so a
palette change that breaks accessibility fails the test suite.

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

Dependencies are pinned in `uv.lock`. To refresh them, run `uv lock --upgrade`,
then `uv sync` and the checks above.

The database lives at `data/cursustrace.db` and is created on first run
(`--db` / `CURSUS_DB_PATH` / `db_path` in the TOML point it elsewhere).

### Demo data

The dashboard and the screenshot tooling can run against a throwaway database:

```sh
# seed a fresh demo database (recreates data/demo.db, writes data/demo_manifest.json)
uv run python scripts/seed_demo.py --db data/demo.db

# run against it, keeping the startup backups out of the personal ones
CURSUS_BACKUP_DIR=backups/demo uv run cursustrace --db data/demo.db
```

The seeder refuses to touch `data/cursustrace.db` unless `--force` is passed,
and `data/` is git-ignored, so the demo file never reaches the repository.

## Contributing

Contributions are welcome as pull requests against `main`.

### Commit messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/):
the pattern is `<type>(<scope>): <summary>`. For example,
`feat(app): add history timeline`. Use the imperative mood, lower case, no
trailing period. The scope is optional.

Types in use:

- `feat`: a new feature
- `fix`: a bug fix
- `refactor`: a change that neither fixes a bug nor adds a feature
- `perf`: a performance improvement
- `docs`: documentation only
- `style`: formatting or whitespace, no behaviour change
- `chore`: maintenance, tooling, dependencies
- `ci`: CI/CD workflows
- `build`: build system or packaging

### Pull requests

Every pull request should explain:

- Why: the purpose and the problem it solves.
- What changed: a short summary of the changes.
- Technical details: brief notes on the approach, key files, and trade-offs.

Before requesting review, confirm:

- [ ] `uv run ruff check src tests`
- [ ] `uv run ruff format src tests`
- [ ] `uv run mypy src tests`
- [ ] `uv run pytest`

## License

Licensed under the [Apache License 2.0](LICENSE).
