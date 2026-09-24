# CursusTrace — Job Application Tracker

[![CI](https://github.com/riccione/cursustrace/actions/workflows/ci.yml/badge.svg)](https://github.com/riccione/cursustrace/actions/workflows/ci.yml)

Track job applications by scanning a listing URL and saving its details to a local
SQLite database.

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
[http://localhost:8080](http://localhost:8080).

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
your CV in Markdown with a live preview. Your profile and CV are stored only in
the SQLite database, and **📄 Export to PDF** downloads a formatted document
combining your contact header with the CV body.

The **📊 Statistics** tab shows how many positions you have in total and in each
pipeline stage (unapplied, applied, interview, rejected).

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
  --location Remote --status applied --json

# scrape and add one or more URLs
uv run cursustrace scan https://example.com/job/1 https://example.com/job/2 --json

# batch import a JSON array (structured items and/or URL-only items) from a file or stdin
uv run cursustrace import jobs.json --json
echo '[{"url": "https://example.com/job/1"}]' | uv run cursustrace import --json

# list stored positions
uv run cursustrace list --status applied --json

# position counts per pipeline stage
uv run cursustrace stats --json

# show the version
uv run cursustrace -V

# delete job positions (or everything with --all); --yes skips the prompt
uv run cursustrace clear --yes
uv run cursustrace clear --all --yes
```

Each JSON item may be structured (`url`, `title`, `company`, `location`,
`description`) or URL-only (the URL is scraped to fill the missing fields).

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
