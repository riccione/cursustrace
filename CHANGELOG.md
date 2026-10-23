# Changelog

All notable changes to `cursustrace` will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-10-23

### Bug Fixes

- *(docker)* Let the container prepare its bind mounts

### Documentation

- *(docker)* Document the published image and pull-based upgrades
- *(docker)* Explain the automatic mount preparation
- Expand the release guide

### Features

- *(docker)* Point compose at the published Docker Hub image
## [0.2.0] - 2026-10-23

### Bug Fixes

- *(dedup)* Preserve posting-id query params in canonical URLs
- *(db)* Recompute fingerprints and drop legacy global index
- *(web)* Make app footer inline instead of fixed
- *(web)* Reset reused dialogs when they open
- *(web)* Anchor the More menu to its button
- *(web)* Nest the More menu inside its trigger button
- *(web)* Use a select for the appearance theme

### Documentation

- *(readme)* Document multi-user mode
- *(readme)* Document discover search and filtering
- *(readme)* Document URL dedup rules
- *(readme)* Note new profile copies CV sections by default
- *(readme)* Mention scroll-to-top button
- *(readme)* Note page footer
- *(readme)* Document the Discover tab
- *(readme)* Clarify footer placement
- *(readme)* Document the headless-browser render tier
- *(readme)* Note that the add and edit forms start fresh
- *(readme)* Describe the Add Job panel
- *(readme)* Describe the needs-attention badges
- *(readme)* Describe the attention highlights
- *(readme)* Describe the filter toolbar and drawer
- *(readme)* Describe the primary stage tabs
- *(readme)* Note how to refresh dependencies
- *(readme)* Document the webapp color system
- *(readme)* Document the offer stage and the job cards
- *(cli)* Move the CLI reference to docs/CLI.md
- *(readme)* Restructure the README into a scannable landing page
- *(readme)* Document the board view
- *(readme)* Document the db path and the demo workflow
- *(readme)* Drop the remaining emoji
- *(readme)* Add the interface screenshots

### Features

- *(config)* Add multi-user toggle
- *(db)* Add users and per-user ownership
- *(auth)* Password hashing, validation, and admin seeding
- *(web)* Login, register, and route protection
- *(web)* Logout and change-password controls
- *(discover)* Search sources by keyword and filter link noise
- *(cli)* Add discover --query and --only-live
- *(mcp)* Add discover query and only_live
- *(db)* Copy CV sections when cloning a profile
- *(profile)* Add copy-CV checkbox to Add profile dialog
- *(web)* Add scroll-to-top button to dashboard and detail
- *(web)* Show app name and version in a page footer
- *(discovery)* List sources and scope discover to a user
- *(web)* Add Discover tab to the dashboard
- *(scraper)* Add headless-browser render tier
- *(web)* Move scanning behind a primary Add Job button
- *(web)* Replace the needs-attention banner with tab badges
- *(web)* Highlight positions that need attention
- *(web)* Condense the filter controls into a toolbar
- *(web)* Show only the active stages as tabs
- *(web)* Add a unified color system for light and dark themes
- *(db)* Add an offer stage to the pipeline
- *(web)* Show every position as a card with a stage tracker
- *(web)* Show only the app name in the header
- *(web)* Add a Kanban board view
- *(config)* Let the database path be configured
- *(web)* Use Material icons and polish the boards and details
- *(web)* Finish the Material icon pass and flatten the tab strip
- *(web)* Remove the last emoji from the interface
- *(scraper)* Match the location marker without the emoji
- *(docker)* Ship a Docker image and compose setup

### Miscellaneous Tasks

- *(docker)* Restrict manual runs to main and name the exact action versions
- Add cache scope to Docker workflow
- *(release)* Add workflow to publish github release with source tarball
- *(changelog)* Add git-cliff configuration
- Add bump-my-version configuration to pyproject.toml
- Generate CHANGELOG.md
- Update CHANGELOG

### Refactoring

- *(web)* Extract reusable run_scan helper

### Styling

- *(cli)* Use a colon in the command group docstring

### Testing

- *(renderer)* Patch os.geteuid directly for strict mypy
- *(web)* Enforce the color tokens and their contrast
- *(cli)* Dynamically assert package version in version flag tests
## [0.1.0] - 2026-10-11

### Bug Fixes

- *(db)* Enable WAL and set an explicit busy timeout
- *(pdf)* Surface the underlying WeasyPrint render error
- *(ui)* Wrap long markdown so descriptions don't overflow
- *(ui)* Mark the scan URL box so tests target it deterministically
- *(ui)* Load webapp stylesheet in run() with actionable error

### Documentation

- Add usage instructions to README
- Document uv run cursustrace
- Describe job detail view
- Describe profile and CV editor
- Update profile storage description
- Update README for NiceGUI
- *(db)* Note that interpolated SQL identifiers are internal constants
- Document configuration options
- Add contributing guide, PR template, and license section
- Document file logging configuration
- *(job-sources)* Add regional job source lists
- Document delete command, batch tags, and location sources
- Document MCP server and opencode setup
- *(job-sources)* Add Wantapply to remote sources
- *(job-sources)* Re-verify wantapply as live
- Document scrape_position and applicability gating
- Document application deadlines
- Document the needs-attention view
- Document scraping fallbacks and limits
- *(readme)* Describe detail-view checkboxes and profile contact fields
- *(readme)* Document the outdated stage
- *(readme)* Restructure dashboard section and plain up the prose
- *(readme)* Document per-position CV tracking

### Features

- *(db)* Implement SQLite persistence layer
- *(errors)* Add domain exception hierarchy
- *(scraper)* Add job listing scraper
- *(app)* Add Streamlit job tracker UI
- *(cli)* Add cursustrace console script
- *(app)* Add job detail view with full description
- *(utils)* Add URL cleaning and fingerprint helpers
- *(db)* Add fingerprint column, migration, and duplicate checks
- *(app)* Surface duplicate warning badge
- *(db)* Add clear_all_jobs with sequence reset
- *(app)* Add danger-zone database clear control
- *(db)* Add profile table with CV sync
- *(app)* Add profile and CV editor tab
- *(app)* Use wide page layout for main content
- *(pdf)* Add Markdown CV to PDF exporter
- *(app)* Add Export to PDF button to profile editor
- *(tracking)* Add interview and rejected pipeline statuses
- *(app)* Migrate dashboard to NiceGUI
- *(db)* Add settings storage and single-job deletion
- *(app)* Add theming, checkbox rules, deletion, batch scan, graceful shutdown
- *(app)* Widen settings drawer to avoid horizontal scrolling
- *(pdf)* Load CV stylesheet from customizable styles/cv.css
- *(app)* Add and edit positions manually with validation
- *(cli)* Add headless job-ingestion commands with Click
- *(cli)* Add --version flag and clear command
- *(search)* Add fuzzy company search
- *(comments)* Add per-stage comments for positions
- *(stats)* Add a Statistics tab and 'stats' CLI command
- *(search)* Search across all pipeline stages
- *(config)* Load settings from TOML, env vars, and flags
- *(app)* Accept settings in run() and honour the CV stylesheet setting
- *(cli)* Add run subcommand with host, port, and config flags
- *(db)* Log job events with cascade and one-time backfill
- *(app)* Show job history timeline and days-since-applied badge
- *(logging)* Add configurable daily file logging
- *(cli)* Add --log-level and log ingestion errors
- *(app)* Log startup and ingestion errors to the daily file
- Treat different links as distinct and flag similar positions
- *(ui)* Keep only failed URLs in the scan box after scanning
- *(db)* Add optional salary fields
- *(app)* Edit, filter, and chart salary
- *(cli)* Accept and report salary
- *(export)* Add cursustrace export command
- *(backup)* Add cursustrace backup command
- *(app)* Back up the database on startup
- *(db)* Sort jobs by date, company, or status
- *(cli)* Sort and paginate list output
- *(ui)* Add sort and pagination controls to the dashboard
- *(db)* Store user-defined tags
- *(cli)* Tag assignment, filtering and management
- *(ui)* Tag chips, assignment and dashboard filter
- *(ui)* Collapse advanced filters behind an Advanced bar
- *(db)* Aggregate statistics for dashboard charts
- *(ui)* Add status, timeline and funnel charts to Statistics
- *(ui)* Render charts with dark theme
- *(ui)* Move main navigation tabs into the header
- *(cv)* Split markdown cv into summary, work history, education and skills fields
- *(profile)* Support multiple named profiles
- *(app)* Print config source and backup status on startup
- *(cli)* Add discover command for agent-driven job discovery
- *(cli)* Add delete command
- *(cli)* Pass tags through scan and discover --add
- *(scraper)* Extract location from JSON-LD and page markers
- *(mcp)* Add MCP server for agent tool access
- *(ui)* Add copy buttons to profile contact fields
- *(ui)* Add copy buttons to CV markdown textareas
- *(scraper)* Fetch through curl-cffi browser impersonation
- *(applicability)* Flag country-restricted listings
- *(mcp)* Add scrape_position preview tool
- *(mcp)* Gate add_position, scan and discover on applicability flags
- *(scraper)* Extract validThrough deadline from JobPosting JSON-LD
- *(db)* Store application deadlines
- *(cli)* Surface deadlines through add, scan, import and list
- *(mcp)* Expose deadlines in scrape, add and position payloads
- *(ui)* Render deadline badges and add form field
- *(config)* Add attention-day thresholds
- *(attention)* Detect stale stages and imminent deadlines
- *(cli)* Add list --attention filter
- *(ui)* Show needs-attention banner with settings toggle
- *(scraper)* Reject thin scrapes and mine embedded job payloads
- *(scraper)* Recover thin listings from known job-board APIs
- *(scraper)* Render JavaScript shells with lightpanda
- *(app)* Status checkboxes and profile contact on detail view
- *(db)* Outdated as fifth pipeline stage
- *(config)* Add outdated_after_days setting
- *(attention)* Park-eligibility rule for stale positions
- *(app)* Outdated tab, checkbox, and statistics
- *(app)* Auto-park stale positions on dashboard refresh
- *(cli,mcp)* Accept and report outdated status
- *(web)* Copy personal data when adding a profile
- *(db)* Store the profile used per position
- *(web)* Pick the CV used on the job detail page
- *(web)* Show the CV used on dashboard cards

### Miscellaneous Tasks

- Initialize project structure and dependencies
- *(ui)* Run streamlit headless to stop auto-opening browser
- *(ui)* Increase default base font size to 18
- *(ui)* Increase default base font size to 22
- Ignore imported CSV files under data/
- Add pre-commit hooks (ruff, ruff-format, pytest)
- *(db)* Remove unused PROFILE_FIELDS constant
- Add GitHub Actions workflow, Dependabot, and CI badge
- Add Apache License 2.0
- Skip lint and test for markdown-only changes
- Ignore local scripts, screenshots, and data directory
- Delegate to reusable CI workflow and update pre-commit hook settings (#31)
- Run workflow on pull requests only
- Comment out pull_request trigger

### Other

- *(deps)* Add markdown and xhtml2pdf for PDF export
- *(deps)* Replace Streamlit with NiceGUI
- *(deps-dev)* Update uv-build requirement
- *(deps-dev)* Bump ruff from 0.16.8 to 0.16.9
- *(deps-dev)* Bump mypy from 2.3.1 to 2.4.0
- *(deps)* Bump trafilatura from 2.2.0 to 2.3.0
- *(deps-dev)* Bump ruff from 0.16.9 to 0.16.10

### Performance

- *(db)* Look jobs up by id and return the new id from add_job

### Refactoring

- *(cli)* Exec streamlit directly for clean shutdown
- *(db)* Store profile and CV only in SQLite
- *(pdf)* Build unified CV from profile metadata
- *(pdf)* Render CV with WeasyPrint using md-to-pdf styles
- *(app)* Extract shared delete-confirmation dialog
- *(db)* Centralize job_status derivation
- *(db)* Simplify duplicate checks with an exclude sentinel
- *(app)* Drop the unused CV PDF cache
- *(app)* Split _render_job_card into focused helpers
- *(cli)* Extract shared scan/import summary reporting
- *(cli)* Extract per-item import ingestion
- *(db)* Drop unused fingerprint parameter from add_job
- *(app)* Localize shutdown handling in run()
- *(app)* Track the search query with nonlocal instead of a dict
- *(cli)* Extract delete target resolution
- *(ui)* Move global CSS to styles/webapp.css
- *(web)* Extract constants and settings state
- *(web)* Extract attention banner and parking UI
- *(web)* Extract status controls and dialogs
- *(web)* Extract job card and statistics
- *(web)* Extract scan, settings drawer, job form, and profile
- *(web)* Move page handlers behind register_pages
- *(cli)* Split cli module into a cli package

### Styling

- Apply ruff formatting
- *(ui)* Apply textarea line spacing globally

### Testing

- *(db)* Cover persistence layer behavior
- *(scraper)* Cover scraper behavior
- *(app)* Add Streamlit AppTest smoke suite
- *(cli)* Cover run launcher
- *(app)* Cover job detail view
- *(cli)* Cover exec-based runner and fallback
- *(dedupe)* Cover URL, fingerprint, and fuzzy matching
- *(settings)* Cover clear confirmation gating
- *(profile)* Cover profile persistence and editor
- *(app)* Rewrite UI tests for NiceGUI user simulation
- *(job-sources)* Validate the source tables offline
- *(app)* Widen notice polling window for slow CI
