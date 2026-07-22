#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-.}"
TARGET="$(cd -- "$TARGET" && pwd)"

if [[ ! -d "$TARGET/scripts" || ! -d "$TARGET/.github/workflows" ]]; then
  echo "Target does not look like the MyETF repository: $TARGET" >&2
  exit 2
fi

cp -R "$SCRIPT_DIR/repo-files/." "$TARGET/"
rm -f \
  "$TARGET/.github/workflows/house_check.yml" \
  "$TARGET/.github/workflows/senate_check.yml"

for entry in ".monitor-state/" "monitor-result.json"; do
  if ! grep -Fqx "$entry" "$TARGET/.gitignore" 2>/dev/null; then
    printf '\n%s\n' "$entry" >> "$TARGET/.gitignore"
  fi
done

printf 'Applied monitoring recovery files to %s\n' "$TARGET"
printf 'Next: review git diff, add Actions secrets, commit, push, enable the workflow, and manually run it with initialize_state selected.\n'
