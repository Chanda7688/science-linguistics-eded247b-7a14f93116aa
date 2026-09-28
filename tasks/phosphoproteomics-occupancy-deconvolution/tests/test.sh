#!/usr/bin/env bash
# Verifier entrypoint. Runs the pytest suite with CTRF output and writes the binary reward.
# Do NOT install/fetch anything here — bake it into tests/Dockerfile.
set -uo pipefail
cd /tests
python3 -m pytest test_outputs.py -p pytest_json_ctrf --json-ctrf=/logs/verifier/ctrf.json
rc=$?
mkdir -p /logs/verifier
# binary reward: exactly 0 or 1
[ $rc -eq 0 ] && echo -n 1 > /logs/verifier/reward.txt || echo -n 0 > /logs/verifier/reward.txt
exit 0
