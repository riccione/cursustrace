# CursusTrace — Job Application Tracker

Track job applications by scanning a listing URL and saving its details to a local
SQLite database.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Install dependencies:

```sh
uv sync
```

## Run

Install and launch the app:

```sh
uv sync
uv run cursustrace
```

Alternatively, run the Streamlit app directly:

```sh
uv run streamlit run src/cursustrace/app.py
```

Paste a job listing URL, click **Scan & Save Position**, then use the
**Unapplied** and **Applied** tabs to manage each position. Toggle
**Mark as Applied** on a card to move it between tabs. Select
**View full details** on a card to open the complete scraped description and
metadata.

Use the **👤 Profile & CV Editor** tab to set your contact details and edit
your CV in Markdown with a live preview. Your profile and CV are stored only in
the SQLite database, and **📄 Export to PDF** downloads a formatted document
combining your contact header with the CV body.

## Development

```sh
uv run pytest          # tests
uv run mypy src tests  # type checking
uv run ruff check src tests
```

The database lives at `data/cursustrace.db` and is created on first run.
