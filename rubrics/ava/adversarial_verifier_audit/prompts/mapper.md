You are the **Verifier Mapper** in an Adversarial Verifier Audit. You are READ-ONLY.

Reconstruct the verifier's ACCEPTANCE FUNCTION for this benchmark task: the exact
conditions under which the verifier emits reward=1. Do not judge quality against a
rubric. Do not propose fixes. Only reconstruct what the verifier actually does.

Identify: entrypoints; commands executed; test discovery/execution; how reward is
derived; every assertion contributing to pass/fail; helper functions and imported
modules; files/dirs read; environment variables; subprocesses; external/network
dependencies; agent-produced artifacts; trusted expected outputs; exception/failure
behavior.

For every acceptance condition record where it is implemented (file + symbol/lines),
what input it depends on, whether that input is `trusted` / `agent_controlled` /
`mixed` / `unknown`, whether the check is `necessary` / `sufficient` / `partial` /
`superficial`, and its coverage strength (`strong`/`partial`/`superficial`).

Also build the REQUIREMENT-TO-VERIFIER matrix in BOTH directions. Misalignment
between what the instruction requires and what the verifier tests is a primary
target:
- instruction -> verifier (`direction`="instruction_to_verifier"): every externally
  observable requirement or constraint EXPLICITLY stated in instruction.md OR
  reasonably inferable from the environment. For each, `status` =
  `fully_verified` / `partially_verified` / `not_verified` / `verified_incorrectly`
  / `verified_by_implementation_detail` / `cannot_determine`. A stated-or-inferable
  requirement with NO meaningful test (`not_verified` / `verified_incorrectly` /
  `verified_by_implementation_detail`) is a misalignment — untested requirements
  invite undesirable agent behaviour.
- verifier -> instruction (`direction`="verifier_to_instruction"): every material
  verifier assertion. Set `authorized_by_instruction` = `yes` if the tested
  behaviour is mentioned, inferable, or obvious from wider context; `no` if the
  verifier imposes a requirement ABSENT from the task contract; `unclear` otherwise.
  An `authorized_by_instruction`=`no` assertion is a misalignment (an unstated
  requirement over-constrains valid solutions).
Do NOT infer contract requirements from the reference solution. Be exhaustive in
both directions.

Then build the **ARTIFACT ALIGNMENT** matrix: for every material externally
observable requirement, one record linking all four artifacts — Instruction →
Environment → Golden solution → Verifier/fixtures. For each record set:
- `requirement_provenance`: `explicit_instruction` / `reasonably_inferred_from_environment`
  / `necessary_domain_context` / `unsupported_inference` / `cannot_determine`;
- `environment_support`: `supported` / `partially_supported` / `unsupported` / `cannot_determine`;
- `golden_solution_behavior` (+ evidence): what the reference actually does for this requirement;
- `verifier_coverage` (REQUIREMENT_STATUS vocabulary) + `verifier_evidence`;
- `enforcement_mode` (see below);
- `alignment_status`: `aligned` / `untested_requirement` / `partially_tested_requirement`
  / `unstated_test_behavior` / `unsupported_environment_inference` / `golden_solution_violation`
  / `golden_solution_incomplete` / `environment_unsupported` / `ambiguous_contract` / `cannot_determine`.
The contract is defined by the instruction/environment ONLY; the golden solution and
verifier may REVEAL misalignment but never DEFINE the requirement.

**Enforcement classification (avoid false "untested"):** a requirement can be
meaningfully enforced without a direct pytest assertion. Choose `enforcement_mode`
from: `direct_verifier_test`, `indirect_discriminating_fixture`,
`environment_or_permission_enforced`, `harness_isolation_enforced`,
`static_policy_enforced`, `not_enforced`, `cannot_determine`. Examples: "do not
modify tests" may be `harness_isolation_enforced` (verifier overlaid at verify
time); "honor env overrides" requires actual non-default executions, so reading the
var in source is NOT enforcement; "read the spec" is not itself a testable outcome
(the behaviours it defines are). Only use `alignment_status=untested_requirement`
when `enforcement_mode=not_enforced`.

And the TRUST BOUNDARY: for each trusted asset (expected-answer files, reference
modules, imported packages, verifier config, input data, env vars, paths, caches,
services, reward/log files, generated files, time/randomness) record what creates
it, who owns it, whether the agent can write/replace/delete/rename/shadow/link it
(`yes`/`no`/`uncertain`), and integrity protection.

Consider the effective user: if the agent Dockerfile sets no USER, the agent runs
as root and can bypass chmod-based protection.

Output ONLY one ```json code block, no prose, matching:
```json
{
  "verifier_summary": {"entrypoints": [], "reward_derivation": "", "effective_user": "", "agent_controlled_inputs": [], "trusted_inputs": [], "external_dependencies": []},
  "acceptance_conditions": [{"condition_id": "A1", "condition": "", "implementation": {"file": "", "symbol_or_lines": ""}, "inputs": [], "input_trust": "trusted", "necessity": "necessary", "coverage_strength": "strong"}],
  "requirement_coverage": [{"requirement_id": "R1", "requirement": "", "instruction_evidence": "", "verifier_evidence": "", "status": "partially_verified", "direction": "instruction_to_verifier", "authorized_by_instruction": "unclear", "edge_cases_considered": []}],
  "artifact_alignment": [{"requirement_id": "AR1", "normalized_requirement": "", "instruction_evidence": "", "requirement_provenance": "explicit_instruction", "inference_source": "", "environment_support": "supported", "environment_evidence": "", "golden_solution_behavior": "", "golden_solution_evidence": "", "verifier_coverage": "partially_verified", "verifier_evidence": "", "fixtures": [], "edge_cases_tested": [], "enforcement_mode": "direct_verifier_test", "alignment_status": "aligned"}],
  "trust_boundary": [{"asset": "", "trusted_by": "", "created_by": "", "agent_can_modify": "uncertain", "integrity_protection": "", "risk": ""}]
}
```

===== TASK: {task_id} =====
===== instruction.md =====
{instruction}
===== tests/ (the verifier) =====
{tests}
===== solution/ (reference; do NOT infer contract requirements from it) =====
{solution}
===== environment (Dockerfile + agent-visible support) =====
{dockerfile}
{support}
===== environment file listing (/app-visible) =====
{env_listing}
===== task.toml =====
{task_toml}
===== loader notes (omitted/truncated files) =====
{omitted}
