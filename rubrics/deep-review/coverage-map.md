# Coverage map — `TB_spec/TB_spec_quality.md` vs Dynamo CI

For every TB_spec quality criterion: where it's **already checked** (CI static check `[chk]`, CI
Stage-2 rubric criterion `[rub]`, validation oracle/nop/build `[val]`, or pass@2/pass@5 + `harbor
analyze` `[passk]`), or **GAP** → owned by this `deep-review/` pass.

CI mechanisms (the skip list — deep-review does NOT re-grade these):
- **[chk]** 23 static checks: allow-internet, apt-upgrade, base-image, broad-chmod, diversity-labels,
  dockerfile-platform, dockerfile-references, dockerfile-sanity, dockerignore, fields-edited,
  gpu-types, instruction-length, no-heredoc-source, nproc, pip-pinning, reward-file,
  task-absolute-path, task-fields, task-slug, task-timeout, test-file-references, test-sh-sanity,
  trial-network-fetch.
- **[rub]** Stage-2 rubric (`.dynamo/dynamo-rubric.toml`, 31 criteria): code_dependent,
  essential_difficulty, interesting_realistic, novel, solvable, unambiguous, outcome_verified,
  instruction_concision, structured_data_schema, verifiable, functional_verification,
  deterministic_reproducible, test_instruction_alignment, anti_cheat, solution_quality,
  task_security, difficulty/solution/verification_explanation_quality, accurate_taxonomy_labels,
  expert_time_estimate, open_internet, reviewable, typos, no_extraneous_files,
  verifier_configuration, environment_hygiene, resource_configuration, task_name, task_readme,
  task_toml_schema.
- **[val]** validation stage (docker build + oracle reward=1 + nop reward<1).
- **[passk]** pass@2/pass@5 + `harbor analyze` (reward_hacking, refusals, near_miss, difficulty_crux,
  low_timeout, task_specification, approach_validity — automated per-trial classification).

Legend: ✅ covered · ◑ partially covered (deep-review confirms the residual) · ⛳ GAP (deep-review owns).

## General
| Criterion | Coverage |
|---|---|
| Harbor-compliant / Correct file layout | ✅ [chk task-fields, val] |
| Well-formed / Consistent formatting | ◑ [rub typos] — deep-review confirms shell/TOML/Python/Dockerfile validity holistically |
| **Unix-compatible files (no CRLF)** | ⛳ no CI check |
| Human-authored | ✅ [rub] (instruction prose) |
| **Text-only assets (PDFs/diagrams parsable)** | ⛳ no CI check |
| Original / novel | ✅ [rub novel] + similarity stage |
| Realistic / Agentic / Solvable / Verifiable / Outcome-based / Secure / Anti-cheat / Reviewable / Complete | ✅ [rub] |
| Deterministic | ◑ [rub deterministic_reproducible, val] — deep-review checks repeated-run determinism |

## Task package
| Criterion | Coverage |
|---|---|
| Valid config / task identity / resource config / network policy / artifacts / buildable / pinned deps / no runtime installs in tests / no info leakage / no unnecessary files | ✅ [chk + rub + val] |
| **No stale data (wall-clock/date/live web/mutable indexes)** | ◑ [chk trial-network-fetch] live fetch only — ⛳ time/date-dependence not checked |
| **No hidden state (prior runs / caches / host files)** | ⛳ validation runs clean but no explicit review |

## Instructions
| Criterion | Coverage |
|---|---|
| Clear objective / Concise / Realistic / Absolute paths / No hidden requirements / No solution hints / Valid constraints / Structured outputs specified / No subjective goals | ✅ [chk instruction-length, task-absolute-path; rub unambiguous, instruction_concision, structured_data_schema] |
| **Environment description accurate (instruction ↔ actual starting state)** | ⛳ instruction↔environment alignment not directly graded |
| **No unsupported claims (claims tools/data that don't exist)** | ⛳ |
| **No misleading distractors** | ⛳ |

## Environment
| Criterion | Coverage |
|---|---|
| No solution leakage / Clean build / Appropriate compute | ✅ [chk dockerfile-references, base-image, nproc, gpu-types; rub environment_hygiene] |
| Necessary / Realistic / Correct initial state / All assets included / Dependencies available | ◑ [val oracle proves runnable] — ⛳ "correct initial state matches instruction" is a review judgment |
| **No solution-only dependencies in agent image (reveals solution path)** | ⛳ environment_hygiene is generic; this specific leak not checked |
| **Stable services (health checks, ports)** | ⛳ (when services used) |
| **Appropriate permissions (agent can work; verifier materials protected)** | ⛳ |
| **No brittle time dependence (timestamps/seeds in env data)** | ⛳ |

## Solution
| Criterion | Coverage |
|---|---|
| Present/executable / Correct | ✅ [val oracle] |
| Clear / Non-trivial / Not hardcoded | ✅ [rub solution_quality; passk reward_hacking] |
| Appropriate decomposition (no big heredocs) | ✅ [chk no-heredoc-source] |
| **No privileged knowledge (no hidden verifier data/expected outputs)** | ◑ [rub anti_cheat] — ⛳ confirm against the actual solution scripts |
| **Consistent with tests / No verifier dependency** | ◑ ⛳ solution↔test alignment (does solve.sh read tests/ or solution-only artifacts?) |
| **No unnecessary actions (unrelated edits / tampering / net calls)** | ⛳ |

## Tests and verifier — *deep-review's primary territory (unverified requirements)*
| Criterion | Coverage |
|---|---|
| Present/executable / Reward file generated / Reward not pre-created / No runtime installs | ✅ [chk reward-file, test-sh-sanity, test-file-references] |
| Functional verification (not keyword/regex matching) | ✅ [rub functional_verification] |
| No extra requirements (tests assert only what instruction states) | ◑ [rub test_instruction_alignment] — deep-review enumerates specifics |
| **Complete coverage / No missing requirements (every material requirement tested + edge cases)** | ⛳ **the unverified-requirements analysis** |
| **Correct expected results (assertions map inputs→outputs; expected values derived/justified)** | ⛳ |
| **Decisive expected value discoverable by the agent (sole-discriminator fairness)** | ⛳ `unambiguous` [rub] can pass while the *decisive* assertion demands a spec-silent choice whose intended answer lives only in hidden GT / task.toml / solution — grade `decisive_answer_discoverable` from the agent's actual field of view; a lenient CI `unambiguous` PASS is a CI escape |
| **Granular (specific, not overly broad)** | ⛳ |
| **Readable (no encoded commands / opaque file hashes)** | ⛳ |
| **Tolerances justified (correct alternatives pass, incorrect fail)** | ⛳ |
| **Graceful failure (writes failing reward, never crashes without one)** | ◑ [chk reward-file presence] — ⛳ behavior on missing prereqs/no-op |
| **Fast enough (subprocess timeouts; within verifier timeout)** | ◑ [chk task-timeout cap] — ⛳ internal subprocess timeouts |
| **Materialized fixtures / Independent from Oracle / Independent from agent tampering (ground truth re-copied)** | ◑ [rub anti_cheat] — ⛳ confirm re-copy + no oracle import |
| No subjective grading / Anti-cheat resistant | ◑ [rub anti_cheat; passk reward_hacking] — ⛳ deeper resistance (monkey-patch, fake wrappers) via trace review |

## Correctness validation
| Criterion | Coverage |
|---|---|
| Image builds / Oracle passes / No-op fails / No reward-file errors / Clean-run | ✅ [val] |
| Benchmarked with agents / Pass rates recorded | ✅ [passk] |
| **Repeated Oracle/no-op/verifier determinism** | ⛳ CI runs each once (pass@5 gives multi-run signal but not an oracle/verifier determinism check) |
| **No hidden dependency validated (undeclared files/caches/env/creds)** | ◑ [val clean env] — ⛳ explicit confirmation |

## Component alignment — *deep-review's primary territory (domain-expert read)*
| Criterion | Coverage |
|---|---|
| Instruction-test alignment | ◑ [rub test_instruction_alignment] — deep-review does the full both-directions trace |
| **Instruction-solution / Instruction-environment / Environment-test / Environment-solution / Solution-test alignment** | ⛳ the full alignment matrix is not graded |
| **Metadata-component alignment (resource rationale, time estimate, explanations match reality)** | ◑ [rub *_explanation_quality, expert_time_estimate] — ⛳ resource/benchmark accuracy |
| **No contradictory requirements** | ⛳ |
| **No orphaned behavior (unused assets, untested reqs, dead verifier checks, stale metadata)** | ⛳ |
| **Traceable requirements (instruction → solution → verifier, end to end)** | ⛳ |

## Metadata
| Criterion | Coverage |
|---|---|
| Taxonomy labels / Expert time estimate / No default placeholders | ✅ [chk diversity-labels, fields-edited; rub accurate_taxonomy_labels, expert_time_estimate] |
| **Resource rationale accurate / Benchmark results accurate / No stale claims** | ⛳ |

## Security and anti-cheat — *deep-review's primary territory (trajectory)*
| Criterion | Coverage |
|---|---|
| No malicious / No exposed ground truth / No answer in image layers | ✅ [rub task_security, anti_cheat; chk dockerfile-references] |
| **No prompt injection (env/comments/fixtures manipulating the agent)** | ◑ [rub task_security] — ⛳ explicit injection sweep |
| **No obfuscation (hidden unicode, encoded payloads, eval patterns)** | ⛳ |
| **No mutable verifier / No trivial bypasses (reward-file write, PATH intercept, fake logs)** | ◑ [rub anti_cheat] — ⛳ verified against real traces |
| **Sidecar evidence protected** | ⛳ |
| **Agent traces reviewed (cheating / shortcutting / tampering / exploit paths)** | ⛳ **the trajectory analysis** — CI never reviews traces |

## Base images / Dockerfile structuring
| Criterion | Coverage |
|---|---|
| Approved base image (digest-pinned) / single apt block / no apt-get upgrade / no broad chmod / files-as-files (no heredoc) / .dockerignore | ✅ [chk base-image, apt-upgrade, broad-chmod, no-heredoc-source, dockerignore, pip-pinning] |
| **Layers ordered least→most volatile** | ⛳ no CI check (low priority) |
| **Multi-stage builds for compiled artifacts (toolchain not in runtime image)** | ⛳ no CI check |
| **Extract `.tar.gz` fixtures at build (no opaque archives)** | ⛳ no CI check |
