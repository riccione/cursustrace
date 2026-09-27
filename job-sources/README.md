# Job sources

Curated lists of websites to look for a new job, grouped by region. Each file
is a markdown table of boards, aggregators, public employment services and
communities, with a live-verified status — usable by humans and as plain input
for AI agents.

| File | Covers |
|------|--------|
| [usa.md](usa.md) | United States |
| [eu.md](eu.md) | EU/EEA, UK, Switzerland, Nordics |
| [serbia.md](serbia.md) | Serbia |
| [remote.md](remote.md) | Fully remote roles worldwide |

## File format

Every region file starts with frontmatter:

```yaml
---
region: usa            # matches the file name (minus .md)
title: United States job sources
last_verified: 2026-09-27   # ISO date of the last URL check
---
```

followed by `##` sections, each holding one table:

```markdown
| Site | URL | Type | Notes | Status |
|------|-----|------|-------|--------|
```

### Type vocabulary

| Type | Meaning |
|------|---------|
| `aggregator` | Meta-search across many boards |
| `board` | A job board listing employers directly |
| `remote` | Dedicated remote-work board |
| `ats` | Applicant tracking system / vendor job feed |
| `community` | Forum or community where roles are posted |
| `government` | Public employment service |
| `search` | General search surface (e.g. Google for Jobs) |
| `specialist` | Niche by industry, seniority or contract type |

### Status vocabulary

| Status | Meaning |
|--------|---------|
| `live` | URL answered with HTTP 200/202 on the last check |
| `blocked` | Real site, but bot/WAF protection answers 403/406/429 — browse it manually |
| `login` | Requires an account to view listings |
| `dead` | Gone (DNS failure or 404/410 homepage); prefer deleting the row instead |

## Conventions

- Rows are sorted alphabetically by **Site** within each section.
- Every site has one home file, except a handful of global jobsites
  (LinkedIn, Indeed, Jooble, Glassdoor) that repeat across files so each file
  stays self-contained.
- Notes describe what the source is *for* (its edge, not marketing copy).
- A new country gets its own `<cc>.md` plus a row in the table above.

## Maintaining

Re-verify URLs periodically and bump `last_verified`:

```sh
grep -oE 'https://[^ |]+' job-sources/*.md | cut -d: -f2- | sort -u | \
  xargs -P8 -I{} sh -c 'printf "%s %s\n" "$(curl -sL -o /dev/null -w "%{http_code}" --max-time 10 "{}")" "{}"'
```

Accept `200`/`401`/`403`/`429` as up (`403`/`429` → `blocked`). A site whose
homepage 404s twice, or whose domain no longer resolves, is `dead` — drop the
row. `tests/test_job_sources.py` guards the schema offline (columns, frontmatter,
type/status vocabulary, unique URLs, sort order).

## Provenance

Seeded from [career-ops](https://github.com/career-ops-hq/career-ops) (MIT
License), then independently verified against the live sites on 2026-09-27.
