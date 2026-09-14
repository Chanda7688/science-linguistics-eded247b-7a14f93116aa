#!/usr/bin/env python3
"""Count VALID agent failures for the pass@2 gate — harbor 0.18 output shapes.
A valid fail = the agent genuinely failed a SOUND task:
  reward < 1  AND  reward_hacking != fail  AND  refusals != fail
  AND task_specification != fail  AND approach_validity != fail
(approach_validity==fail means the failure exposes a task/verifier problem, not agent difficulty —
 our developed criterion, gating.)

TIMEOUTS ARE NOT A VALID FAIL (mirrors handshake-orchestration-tb2's deterministic taxonomy):
  - setup/verifier/infra timeouts and any non-agent exception -> infra, never a valid fail:
      EnvironmentStartTimeoutError, AgentSetupTimeoutError, VerifierTimeoutError, or any other
      exception_type != AgentTimeoutError.
  - AgentTimeoutError (agent ran out of working time; verifier still ran):
      valid ONLY if low_timeout == pass (the agent was stuck/looping and had enough time);
      if the agent was still making progress (low_timeout == fail / not_applicable / missing),
      the failure is a timeout artifact, NOT genuine difficulty -> not valid.
  - low_timeout == fail with no recorded exception (analyst saw a progressing cut-off) -> not valid.
Usage: count_valid_fails.py <run-output-dir> [<analyze-dir>]   → prints an integer.
"""
import json, glob, os, sys
run = sys.argv[1]
adir = sys.argv[2] if len(sys.argv) > 2 else run
def load(p):
    try: return json.load(open(p))
    except Exception: return {}
# rewards from the RUN output (result.json → stats.evals.*.reward_stats.reward {value:[trial,...]})
reward = {}
# exception_type per trial from the PER-TRIAL result.json (has trial_name + exception_info)
exc = {}
for res in glob.glob(f"{run}/**/result.json", recursive=True):
    d = load(res)
    for ev in ((d.get("stats") or {}).get("evals") or {}).values():
        for val, names in (ev.get("reward_stats", {}).get("reward", {})).items():
            for n in names: reward[n] = float(val)
    tn = d.get("trial_name")
    if not tn:
        base = os.path.basename(os.path.dirname(res))
        tn = base if "__" in base else None
    if tn:
        e = (d.get("exception_info") or {}).get("exception_type")
        if e: exc[tn] = e
# checks from ANALYSIS json(s): harbor 0.18 job -> {results:[...]}, per-trial -> {trial_name,checks}
def trials_of(d):
    if isinstance(d, dict):
        if "results" in d: return d["results"]
        if "trials" in d:  return d["trials"]
        if "checks" in d:  return [d]
    return []
def _outcome(o):
    v = o.get("value") if isinstance(o, dict) else o
    return v.lower() if isinstance(v, str) else v
checks = {}
for aj in glob.glob(f"{adir}/**/analysis.json", recursive=True) + glob.glob(f"{adir}/analyze-*.json"):
    for t in trials_of(load(aj)):
        n = t.get("trial_name")
        if n: checks[n] = {k: _outcome((v or {}).get("outcome")) for k, v in (t.get("checks") or {}).items()}
BLOCKERS = ("reward_hacking", "refusals", "task_specification", "approach_validity")
SETUP_TIMEOUTS = {"EnvironmentStartTimeoutError", "AgentSetupTimeoutError", "VerifierTimeoutError"}
valid = 0
for name, r in reward.items():
    if r >= 1: continue
    e = exc.get(name)
    if e in SETUP_TIMEOUTS: continue            # infra timeout -> not a valid fail
    if e and e != "AgentTimeoutError": continue  # any other infra exception -> not a valid fail
    c = checks.get(name, {})
    if any(c.get(b) == "fail" for b in BLOCKERS): continue
    lt = c.get("low_timeout")
    if e == "AgentTimeoutError":
        # agent hit its working-time cap: valid only if it had enough time (stuck/looping)
        if lt != "pass": continue
    elif lt == "fail":
        # no recorded exception, but the analyst saw the agent cut off mid-progress
        continue
    valid += 1
print(valid)
