#!/usr/bin/env bash
set -euo pipefail

TARGET="${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/repo-files" && pwd)}"
cd "$TARGET"
python -m compileall -q scripts tests
python -m pytest -q tests/test_monitor_disclosures.py
python - <<'PY'
from pathlib import Path
import yaml

workflow = Path('.github/workflows/disclosure_monitor.yml')
parsed = yaml.load(workflow.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
assert isinstance(parsed, dict)
assert set(parsed['on']) == {'schedule', 'workflow_dispatch'}
schedule = parsed['on']['schedule'][0]
assert schedule['timezone'] == 'America/New_York'
assert schedule['cron'] == '17 9,12,16 * * *'
assert 'jobs' in parsed and 'monitor' in parsed['jobs']
steps = parsed['jobs']['monitor']['steps']
steps_by_name = {step['name']: step for step in steps}
assert 'Restore durable state artifact when cache is unavailable' in steps_by_name
assert 'Upload durable monitor state' in steps_by_name
monitor_env = steps_by_name['Monitor House and Senate filings']['env']
assert 'ALLOW_STATE_INITIALIZATION' in monitor_env
assert 'PUSHOVER_API_TOKEN' in monitor_env and 'PUSHOVER_USER_KEY' in monitor_env
assert 'PUSHOVER_API_TOKEN' not in parsed.get('env', {})
print('workflow YAML parsed successfully')
PY
