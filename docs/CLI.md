# CLI Usage

Everything the dashboard does, from the shell — no browser required.


The `cursustrace` command also offers headless subcommands for adding positions
without the UI (duplicates are skipped, never overwritten). Pass `--json` for
machine-readable output.

URLs are deduplicated by a canonical form: tracking parameters (`utm_*`, `ref`,
`gh_src`, board-specific noise) are ignored, while parameters that carry the
posting id on some boards (`id`, `gh_jid`, `ashby_jid`, ...) are preserved and
sorted. Two roles on the same board path therefore stay distinct even when only
their query id differs.

Add one position from explicit fields:

```sh
uv run cursustrace add --url https://example.com/job/1 \
  --title "Backend Engineer" --company Acme --description "Build APIs" \
  --location Remote --status applied --deadline 2026-12-31 --tag remote --json
```

Scrape and add one or more URLs (a repeatable `--tag` labels the whole batch):

```sh
uv run cursustrace scan https://example.com/job/1 https://example.com/job/2 --tag qa --json
```

Discover candidate listing links from a source page (URL or job-sources site
name):

```sh
uv run cursustrace discover RemoteOK --json
uv run cursustrace discover https://example.com/jobs --add --tag qa --json
```

Batch import a JSON array (structured items and/or URL-only items) from a file
or stdin:

```sh
uv run cursustrace import jobs.json --json
echo '[{"url": "https://example.com/job/1"}]' | uv run cursustrace import --json
```

List stored positions (a repeatable `--tag` filter matches any tag):

```sh
uv run cursustrace list --status applied --json
uv run cursustrace list --tag remote --tag startup --json
```

List positions that need attention (a stale stage, or a deadline in the
warning window). The filter composes with `--status`, `--tag`, and the rest,
and text output appends the reasons. Parked `Outdated` positions never match;
list those with `--status outdated`:

```sh
uv run cursustrace list --attention
uv run cursustrace list --attention --status applied --json
```

Sort (newest, oldest, company, company_desc, status) and paginate:

```sh
uv run cursustrace list --sort company --limit 50 --json
uv run cursustrace list --sort status --limit 50 --offset 50 --json
```

Export positions as import-compatible JSON (stdout by default) or CSV:

```sh
uv run cursustrace export
uv run cursustrace export jobs.json
uv run cursustrace export --format csv --status applied
uv run cursustrace export --min-salary 60000 --salary-currency EUR
uv run cursustrace export --tag remote --format csv
```

Manage tags (labels) for counts, creation, rename, and delete:

```sh
uv run cursustrace tags list --json
uv run cursustrace tags add remote referral --json
uv run cursustrace tags rename remote wfh
uv run cursustrace tags rm wfh
```

Position counts per pipeline stage:

```sh
uv run cursustrace stats --json
```

Write a database backup (retention settings are documented under
[Backups](../README.md#backups)):

```sh
uv run cursustrace backup --json
uv run cursustrace backup --dir /mnt/backup --keep 30
```

Show the version:

```sh
uv run cursustrace -V
```

Delete positions by id, URL, a URL list file, or the list filters. The
command previews matches and prompts for confirmation unless you pass
`--yes` (`--json` requires `--yes`):

```sh
uv run cursustrace delete 42 43 --yes
uv run cursustrace delete --url https://example.com/job/1 --yes
uv run cursustrace delete --url-file urls.txt --yes --json
uv run cursustrace delete --status rejected --tag spam --yes
```

Delete all job positions at once, or everything with `--all`. `--yes` skips
the prompt:

```sh
uv run cursustrace clear --yes
uv run cursustrace clear --all --yes
```

Each JSON item may be structured (`url`, `title`, `company`, `location`,
`description`, `status`, `salary_*`, `deadline`, `tags`) or URL-only (the URL is
scraped to fill the missing fields; see the
[Scraping](../README.md#scraping) section for the extraction and fallback
chain). Scraped positions resolve `location` from
the page's Open Graph meta, then its JSON-LD `JobPosting` address, then a
`Location:` line in the description, and `deadline` from the JSON-LD
`validThrough` date. `export` writes that same shape, so
`cursustrace export jobs.json` can be edited and fed back into `import`: status,
stage comments, tags, and deadlines survive the round trip (unknown tags are
created), timestamps are re-stamped on import. The CSV variant is a flat
spreadsheet view with tags joined by `;`. `import` reads JSON only.

### Discovering positions with an AI agent

`discover` fetches one source page (a search or board page), extracts candidate
listing links, and checks each against the database. It does no LLM calls
itself; an AI agent (or you) does the reasoning on top of its JSON output:

```sh
# by site name from job-sources/ or by URL; --limit caps candidates (default 100)
uv run cursustrace discover RemoteOK --json
uv run cursustrace discover https://example.com/jobs --limit 50 --json

# search a source for a keyword; sites with a Search template filter server-side
uv run cursustrace discover RemoteOK --query qa --json
uv run cursustrace discover https://example.com/jobs --query qa --json

# skip named sources that are blocked/login/dead in job-sources/
uv run cursustrace discover "Indeed RS" --only-live --json

# or scrape and add every new link in one step (no agent needed)
uv run cursustrace discover https://example.com/jobs --add --json
```

The JSON payload reports `source` (input, resolved URL, job-sources status),
`extracted` / `new_count` / `known_count`, `known_by_status` (so already-applied
positions are visible), and one `candidates` entry per link: `known: false`
for new ones, plus `status`, `id`, `title` for known ones. With `--query` it
adds `query` and `fetched_url`; a non-live source adds a `warning`. With `--add`
it also returns `added`, `skipped`, `errors` and `similar`, and exits non-zero
when any scrape failed; a failed fetch prints an `error` key. Candidates that
are already in the database are skipped whatever their stage, so discovery never
re-adds an applied position.

Link extraction drops obvious page chrome (navigation, legal, blog, social
links) before applying `--limit`, so the budget goes to real listings. `--query`
substitutes the term into the site's `Search` template from `job-sources/`
(server-side filtering) when one exists, and otherwise filters the extracted
links client-side.

A workflow you can paste to an AI agent:

> Discover positions for me from `<source>`. Run
> `cursustrace discover <source> --json`, review the candidates with
> `known: false` and keep only the ones matching my profile, then run
> `cursustrace scan <chosen urls> --json` and summarize what was added, what
> was already tracked, and what you skipped and why.

### Sweeping every known board

`--all` fetches every live board in `job-sources/` (one request per board, six at
a time), merges the results, and drops links that are already tracked:

```sh
uv run cursustrace discover --all --json
uv run cursustrace discover --all -q "python" --per-board 10 --total 300 --json
uv run cursustrace discover --all --include-blocked --add --tag sweep
```

`--per-board` caps the links taken from each board, `--total` stops the sweep
once that many new links are found, and `--include-blocked` adds boards marked
blocked/login/dead. The JSON payload reports `source.boards` /
`source.skipped_not_live`, `extracted`, `new_count`, `known_count`, `truncated`,
`cancelled`, `candidates[{url,site}]` and `board_errors[]`; with `--add` it adds
`added`, `skipped`, `errors` and `similar`. Only boards with a `Search` template
filter the query server-side; elsewhere the term filters the links extracted
from the board page.

### Searching JSearch

`--jsearch` queries [JSearch](https://www.openwebninja.com/api/jsearch) instead of
a board page. The key comes from `CURSUS_JSEARCH_API_KEY`, `jsearch_api_key` in
`cursustrace.toml`, or the key saved in the Discover tab.

```sh
# one page (10 results); --results fetches more pages, one request per 10
uv run cursustrace discover --jsearch "python developer in Belgrade" --json
uv run cursustrace discover --jsearch "qa engineer" --country rs \
  --date-posted week --remote --results 30 --json

# add the new positions directly, without scraping
uv run cursustrace discover --jsearch "python developer" --add --tag jsearch
```

The JSON payload adds `requests`, `usage` (`used`, `limit`, `month`),
`new_count` / `known_count`, `flagged_count`, and per-candidate `title`,
`company`, `location` and `flags`. Positions flagged as outside Serbia/remote-EU
are listed but skipped by `--add` unless `--force` is passed. The free plan allows
200 requests per month; `usage.used` counts only the requests made through
CursusTrace.

---

[← Back to the README](../README.md)
