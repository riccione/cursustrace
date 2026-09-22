# CursusTrace — Job Application Tracker

Track job applications by scanning a listing URL and saving its details to a local
SQLite database.

## Setup

Requires [uv](https://docs.astral.sh/uv/). Install dependencies:

```sh
uv sync
```

## Run

```sh
uv run streamlit run src/cursustrace/app.py
```

Paste a job listing URL, click **Scan & Save Position**, then use the
**Unapplied** and **Applied** tabs to manage each position. Toggle
**Mark as Applied** on a card to move it between tabs.

## Development

```sh
uv run pytest          # tests
uv run mypy src tests  # type checking
uv run ruff check src tests
```

The database lives at `data/cursustrace.db` and is created on first run.
