# Congressional disclosure monitor recovery

This replacement consolidates the House and Senate checks into one fail-closed monitor. It is designed to detect UNH / UnitedHealth disclosures without silently succeeding when a source, parser, notifier, or scheduler stops working.

## What it changes

- Reads the House annual ZIP as an index, filters `FilingType=P`, then downloads each new PTR PDF by document ID.
- Queries the Senate eFD Periodic Transaction Report search after accepting the public-use terms.
- Scans electronic filings directly and uses Tesseract OCR for image-only PDFs.
- Stores seen report IDs so the same filing is not alerted repeatedly.
- Restores state from a fast Actions cache, with a retained state artifact as a durable fallback.
- Requires explicit initialization before creating a new baseline, so unexpected state loss fails visibly instead of silently skipping filings.
- Retries transient HTTP failures and uses explicit timeouts.
- Exits non-zero on schema changes, empty sources, unreadable reports, missing Pushover credentials, or notification failure.
- Adds a scheduler heartbeat/dead-man URL, failure notifications, test execution, a run summary, and a JSON artifact.

## Installation

From the extracted recovery bundle:

```bash
./apply.sh /path/to/MyETF
cd /path/to/MyETF
git diff --check
git status --short
```

The installer replaces the two monitor scripts, removes the two obsolete workflow files, adds the consolidated workflow, adds tests, and ignores local state/output files.

Commit and push the resulting changes to the default branch.

## Required GitHub Actions secrets

In **Repository settings → Secrets and variables → Actions**, create:

- `PUSHOVER_API_TOKEN` — the Pushover application/API token.
- `PUSHOVER_USER_KEY` — the Pushover user or group key.
- `HEALTHCHECKS_PING_URL` — strongly recommended. Use a unique heartbeat URL whose service supports the common `/start` and `/fail` suffixes. Configure the external service to expect three runs per day and to alert when a run is late.

The workflow sets `REQUIRE_PUSHOVER=true`, so a missing Pushover secret is an immediate visible failure rather than a latent failure discovered only when a matching trade appears.

## First activation

1. Open the repository's **Actions** tab.
2. Select **Congressional disclosure monitor**.
3. Click **Enable workflow** if GitHub shows it as disabled.
4. Run **Run workflow** manually with `initialize_state` selected and `bootstrap_alerts` left off.
5. Confirm the run summary reports both House and Senate source counts.
6. Download the `disclosure-monitor-result-<run-id>` artifact and confirm `"success": true`; also confirm a `disclosure-monitor-state` artifact was created.
7. Confirm the external heartbeat service received both the start and success pings.

The initialized run baselines visible filings and sends no historical match notifications. Future runs process only unseen report IDs. Selecting both `initialize_state` and `bootstrap_alerts` with no restorable state intentionally scans all visible reports and may send many alerts or exceed the normal workflow timeout.

## Local verification

Ubuntu/Debian example:

```bash
sudo apt-get update
sudo apt-get install -y poppler-utils tesseract-ocr
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-monitor.txt pytest==9.0.2
python -m pytest -q tests/test_monitor_disclosures.py
```

A local source check without notifications:

```bash
python scripts/monitor_disclosures.py \
  --source all \
  --state-file /tmp/myetf-monitor-state.json \
  --result-file /tmp/myetf-monitor-result.json \
  --no-notify \
  --verbose
```

For a live notification test, use a disposable state path and set `PUSHOVER_API_TOKEN`, `PUSHOVER_USER_KEY`, and `BOOTSTRAP_ALERTS=true`. That can produce historical alerts.

## Configuration

Environment variables:

| Variable | Default | Purpose |
|---|---:|---|
| `KEYWORDS` | `UNH,UnitedHealth,UnitedHealth Group` | Comma-separated ticker/company terms. |
| `STATE_FILE` | `.monitor-state/disclosures.json` | Persistent seen-ID state. |
| `RESULT_FILE` | `monitor-result.json` | Machine-readable run report. |
| `SENATE_LOOKBACK_DAYS` | `120` | Senate search window. |
| `OCR_MAX_PAGES` | `75` | Refuse partial OCR beyond this page count. |
| `MAX_DOWNLOAD_BYTES` | `104857600` | Maximum filing/index download size. |
| `REQUIRE_PUSHOVER` | `false` locally; `true` in workflow | Validate Pushover credentials before source work. |
| `ALLOW_EMPTY_SOURCES` | `false` | Testing escape hatch; normally leave false. |
| `ALLOW_STATE_INITIALIZATION` | `true` locally; explicit workflow input | Permit creation of a new baseline when no state exists. Scheduled runs set this to false. |
| `DISCLOSURE_USER_AGENT` | Browser-compatible, repository-identifying default | Descriptive HTTP user agent. |

Command-line options override the main source/state/result settings. Run `python scripts/monitor_disclosures.py --help` for the complete list.

## Monitoring semantics

A green run means:

- required dependencies and parser tests passed;
- the selected government sources returned structurally valid PTR listings;
- every unseen filing was fetched and fully text-scanned or OCR-scanned;
- every positive match was delivered to Pushover;
- processed report IDs were written to state.

A red run is intentional when any of those guarantees cannot be made. The failed report is not marked seen, so it will be retried.

State is written incrementally, saved to a GitHub Actions cache, and uploaded as a 90-day `disclosure-monitor-state` artifact after each run. If the cache is unavailable, the workflow restores the newest unexpired state artifact. If neither copy exists, a scheduled run fails and alerts; it does not silently re-baseline. Creating a replacement baseline requires a manual run with `initialize_state` selected.

## GitHub's 60-day inactivity rule

GitHub automatically disables scheduled workflows in inactive public repositories after 60 days. The external heartbeat is the detection mechanism: it alerts when GitHub stops starting the job. It cannot re-enable GitHub Actions by itself.

For a scheduler that should remain active with no repository commits, move the same command to an external cron platform or always-on host and retain the heartbeat. Do not create meaningless automated commits solely to evade GitHub's inactivity policy.

## Disclosure use restrictions

Both the House and Senate disclosure portals display statutory restrictions on obtaining or using reports, including a restriction on commercial use other than news-media dissemination. The Senate eFD site also requires an affirmative agreement before search access; the monitor performs that agreement step for its session. Confirm that the intended use is permitted before activation. The monitor retains the source filing URL in every alert.
