#!/usr/bin/env bash
# TB3 static checks aggregator — runs every deterministic check against one task dir,
# fail-closed: any check exiting non-zero fails the whole gate. No API key, no network.
#
#   static-report.sh <task-dir>
#
# Adapted from the tb2 orchestration aggregator; the check SET is the TB3 (frontier-bench)
# suite vendored under checks/. GPTZero AI-detection is intentionally excluded from the
# gating set (a project decision — see docs/decisions.md).
set -uo pipefail
SUB="${1:?usage: static-report.sh <task-dir>}"
CHECKS="$(cd "$(dirname "$0")" && pwd)"

# Gating checks, in a sensible order: identity/metadata → instruction → docker/agent env →
# separate-verifier → hygiene. "script|human label".
ALL=(
  "check-task-fields.sh|Required task.toml metadata present + valid taxonomy"
  "check-subcategory.py|[metadata].subcategory is a valid subdomain of its category (TAXONOMY.md)"
  "check-task-package-name.sh|[task].name is insight/<folder>"
  "check-task-slug.sh|Task folder slug is <=3 tokens"
  "check-task-timeout.sh|Agent/verifier timeout within the 5h cap"
  "check-task-absolute-path.sh|instruction.md uses absolute paths"
  "check-allow-internet.sh|allow_internet is not set to false"
  "check-no-allow-internet-true.sh|allow_internet is not pinned to true (omit the key)"
  "check-gpu-types.sh|gpu_types are canonical Modal names"
  "check-nproc.sh|No bare nproc (returns host CPU count)"
  "check-dockerfile-platform.sh|No FROM --platform pin (stay arch-portable)"
  "check-dockerfile-references.sh|Agent Dockerfile does not COPY solution/tests"
  "check-dockerfile-sanity.sh|No pinned apt deps; apt hygiene"
  "check-pip-pinning.sh|pip/uv installs are ==-pinned"
  "check-compose-host-binds.sh|compose sidecars use named volumes, not host binds"
  "check-separate-verifier.sh|Task is fully in separate-verifier mode (tests/Dockerfile + artifacts)"
  "check-verifier-tooling-baked.sh|Verifier tooling baked into tests/Dockerfile, not installed at verify time"
  "check-pytest-version.sh|pytest / pytest-json-ctrf pinned to the canonical versions"
  "check-trial-network-fetch.sh|Verifier does no external fetch at trial time"
  "check-test-sh-sanity.sh|test.sh dependency isolation"
  "check-test-file-references.sh|Expected output files are named in instruction.md/task.toml"
)

FAILED=0
BODY=""
add(){ BODY+="$1"$'\n'; }
add "## 🧱 Static checks"
add ""
for entry in "${ALL[@]}"; do
  script="${entry%%|*}"; label="${entry#*|}"
  path="$CHECKS/$script"
  if [ ! -f "$path" ]; then add "- ⚠️ (missing check script: $script)"; continue; fi
  if [[ "$script" == *.py ]]; then out="$(python3 "$path" "$SUB" 2>&1)"; rc=$?
  else out="$(bash "$path" "$SUB" 2>&1)"; rc=$?; fi
  if [ "$rc" -eq 0 ]; then
    add "- ✅ ${label}"
    # surface any non-blocking Warning lines as recommendations
    while IFS= read -r ln; do [[ "$ln" == Warning* ]] && add "    - ⚠️ ${ln#Warning }"; done <<< "$out"
  else
    FAILED=1
    add "- ❌ **${label}**"
    while IFS= read -r ln; do
      [[ "$ln" =~ ^(FAIL|Warning|Error) ]] && add "    - ${ln}"
    done <<< "$out"
  fi
done
add ""
[ "$FAILED" -eq 0 ] && add "_All static checks passed._" || add "_Fix the ❌ items above and push; checks re-run automatically._"

printf '%s\n' "$BODY"
[ "$FAILED" -eq 0 ] || { echo "static checks failed" >&2; exit 1; }
