# MyETF repository recovery report

Assessment date: 2026-07-22

## Executive finding

The monitoring feature is not currently operating. Both scheduled workflows are disabled, the Senate workflow never invokes its script, the Senate script is a placeholder, and the House script consumes the House ZIP incorrectly. Prior green workflow runs therefore do not establish that disclosures were actually monitored.

The supplied recovery overlay targets this monitor first because it is separable from the older scraping/dashboard application and can be made operational with a small, testable surface area.

## Findings, ranked

### P0 — Scheduled monitoring is stopped

Both repository workflows are disabled after more than 60 days without repository activity. GitHub applies this rule to scheduled workflows in public repositories. Manual re-enablement is required after committing the replacement workflow.

### P0 — Senate runs were false positives

`.github/workflows/senate_check.yml` checks out code and installs packages, but contains no step that runs `scripts/parse_unh_disclosures.py`. A green job only proved dependency installation succeeded.

The script itself uses a literal `your-test-disclosure.pdf` URL and calls an undefined `send_notification`, so invoking it would not produce a functioning monitor.

### P0 — House source handling cannot find current PTR PDFs

`scripts/check_house_disclosures.py` is fixed to `2025FD.ZIP` and assumes that ZIP directly contains PDF files. The annual House ZIP is an index containing a tab-delimited file/XML. PTR PDFs must be fetched separately from the row's `Year` and `DocID` after filtering `FilingType=P`.

The notification branch also references undefined Pushover constants and an undefined `Client`, so a real match would fail in the branch meant to alert.

### P1 — No delivery or freshness guarantees

The original scripts have no durable seen-ID state, no initial-baseline behavior, no request timeouts/status validation, no parser-change detection, and no independent heartbeat. Repeated historical alerts, silent empty results, and a dead scheduler are all possible.

### P1 — Scheduling is brittle

The original comments translate fixed UTC cron hours to Eastern time, which changes by an hour during standard time. Jobs are scheduled exactly at the top of an hour, when GitHub documents higher delay/drop risk. The replacement uses `America/New_York` and minute 17.

### P2 — The dashboard/API path is a separate incomplete deployment

The Docker image copies and runs the scraper/dbt path only; it does not copy or start `server.py`. It also requires `COPY .env ./` even though `.env` is ignored, which makes a clean remote build dependent on an untracked build-context file.

The React client calls `http://127.0.0.1:5000`, which points to the browser user's own machine after deployment. The API URL needs to be environment-driven or same-origin/proxied.

The backend test fixture calls `create_app(dbname=..., user=..., password=...)`, while the actual factory accepts no arguments and constructs a production-style PostgreSQL URI from settings. Several expected routes/import paths are stale. This application should be repaired as a second workstream rather than mixed into the monitoring recovery.

## Recovery overlay contents

- Consolidated House/Senate monitor.
- Dynamic House year/index/PTR handling.
- Senate eFD session, CSRF, terms, search, pagination, electronic-report parsing, and paper-PDF handling.
- PDF text extraction with OCR fallback.
- Case-insensitive keyword matching with ticker boundaries.
- Pushover positive-match and workflow-failure alerts, with secrets scoped only to the steps that need them.
- Atomic persistent state, deduplication, initial silent baseline, and retry-safe marking.
- Cache restoration with a retained state-artifact fallback; unexpected state loss fails unless initialization is explicitly authorized.
- HTTP retry/timeouts and fail-closed source validation.
- New York timezone-aware schedule at non-peak minutes.
- External dead-man heartbeat integration.
- GitHub run summary and JSON artifact.
- Offline parser/state/behavior tests.

## Validation performed in the analysis environment

- Python bytecode compilation succeeded.
- 13 unit/behavior tests passed, including a real text-layer PDF extraction check and an unexpected-state-loss guard.
- Workflow YAML parsed successfully.
- No live government requests were executed from the code sandbox because that runtime has no outbound DNS. The source routes and current site behavior were independently checked through browser-accessible sources, but the first manual GitHub Actions run remains the required live acceptance test.

## Recommended next workstream for the application

1. Decide whether the intended product is only an alerting monitor or also a hosted dashboard.
2. If a dashboard is required, split the scraper job, API service, database migrations/dbt job, and frontend build into distinct deployable processes.
3. Replace `.env` image copying with runtime secrets; add `.dockerignore`; pin a supported Python base; remove the deprecated Chrome `apt-key` flow.
4. Add a real WSGI command for the Flask API and a health endpoint.
5. Make the frontend API base URL environment-driven or serve it through the same origin.
6. Refactor `create_app` to accept test configuration and repair tests before deployment.
7. Add CI jobs for backend tests, frontend build/tests, Docker build, and a smoke test.
