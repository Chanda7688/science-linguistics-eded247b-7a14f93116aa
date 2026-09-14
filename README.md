# Terminal-Bench 3 — Task Template

Welcome! This repo is a ready-to-use template for writing a **Terminal-Bench 3** task: a
self-contained challenge that an AI agent tries to solve in a terminal, plus the tests that decide
whether it succeeded. Fork it, write your task, and open a pull request — an automated reviewer
checks your work and leaves feedback right on the PR. When every check is green, a maintainer takes
a final look.

This guide is everything you need to submit your first task. No setup beyond a GitHub account and Docker for testing locally. 
While we do not disallow agent use, the task must be your own creation and rooted in domain-specific workflows. See the [terminal bench 3](https://github.com/harbor-framework/terminal-bench/tree/main/tasks) submissions for inspiration.

Tasks that do not require deep domain specific expertise will struggle to stump the frontier models.

---

## What makes a good task

A great Terminal-Bench 3 task is:

- **Real.** It comes from something you actually know how to do — your field, your tooling, a
  problem you've genuinely solved. The template asks for your relevant experience for exactly this
  reason.
- **Hard for an AI, doable by an expert.** A focused expert could finish it in the time you set; a
  strong AI agent will struggle. If an agent solves it easily on the first try, it's too easy.
- **Objectively checkable.** Success is decided by tests that inspect what the agent *produced* —
  files, outputs, program behavior — never by re-reading the instructions.
- **Self-contained.** Everything the task needs is in the repo. No relying on a website that might
  change or go down.
- **Open-internet by default.** Every Project Insight task runs with **full internet access** — the
  agent can search, read docs, and pull packages. So the difficulty has to be *genuine*: it can't be
  something an agent solves by finding the answer, a write-up, or a ready-made solution online. Make
  the challenge come from real domain expertise, reasoning, or a novel problem — not a lookup.

---

## The five files you'll write

Rename `tasks/REPLACE-with-task-name/` to your own task name (short, lowercase, hyphenated — at most
three words, e.g. `parse-server-logs`) and fill in:

| File | What it is |
|---|---|
| `instruction.md` | The prompt the agent sees. Describe the goal and the exact end state — which files to produce and where — using absolute paths (`/app/output.json`). **No hints or solution steps.** |
| `task.toml` | Task settings: a short description, difficulty and category, your relevant experience, time limits, and the machine size. Every field is annotated in `docs/task-template.toml`. |
| `environment/Dockerfile` | The starting environment the agent works in. Seed it with any code or data the task needs — but **never** the solution or the tests. |
| `solution/solve.sh` | Your own correct solution. It proves the task is solvable and must fully pass the tests. |
| `tests/` | The grader: `test_outputs.py` (the checks), `test.sh` (runs them), and a `Dockerfile` for the grading environment. This is where the correct answers live — the agent never sees them. |

`docs/task-template.toml` walks through every `task.toml` field with inline notes.

`solve.sh` and `test.sh` are just entrypoints — each can call helper files alongside it. Keep the
real logic in a `solution/solve.py` (or several scripts) that `solve.sh` invokes, and put your
checks in `tests/test_outputs.py` (or more pytest files) that `test.sh` runs. Full layout:

```
tasks/<task-name>/
├── instruction.md      # Task instruction for the agent
├── task.toml           # Configuration and metadata
├── environment/
│   ├── Dockerfile      # Container environment setup
│   └── ...             # Optional: data files (e.g., in data/ folder)
├── solution/
│   ├── solve.sh        # Reference solution (Oracle)
│   └── ...             # Optional: Python scripts used by solve.sh
├── tests/
    ├── test.sh         # Test script
    └── ...             # Optional: Pytest test files used by test.sh
```

---

## How to submit

1. **Fork** this repo (or use GitHub's **Use this template**).
2. Rename the task folder and fill in the five files above.
3. Commit on a branch and **open a pull request** back to the repo.
4. The automated review runs and posts comments on your PR. Read them, fix anything marked ❌, and
   push again — the checks re-run automatically.
5. Once everything is green, a maintainer reviews and merges.

You don't need any keys or secrets. Just open the PR.

---

## What the review checks

Each stage below posts its own comment on your PR. ✅ means that stage is happy; ❌ tells you what to
fix. The first few are instant; the agent-trial stages take longer because real agents are attempting
your task.

### 1. Format checks (instant)

Quick, automatic checks that your task is shaped correctly. In plain terms, they make sure:

- **Metadata is complete** — your `task.toml` has all required fields (description, difficulty,
  category, relevant experience) and a valid category.
- **Naming lines up** — the task name matches its folder, and the folder name is short (≤ 3 words).
- **Time limits are sane** — the agent and grader time limits are set and within the 5-hour cap.
- **The instructions are usable** — they use absolute paths and name every output file the tests look
  for.
- **The environment is clean** — the agent's Dockerfile doesn't sneak in the solution or tests,
  isn't pinned to one CPU architecture, and installs packages at fixed versions so builds are
  reproducible.
- **The grader is self-contained** — the tests live in their own image with their tools already
  installed, don't reach out to the internet while grading, and read the agent's output rather than
  its source.

If any of these fail, the comment points at the exact file and line.

### 2. Duplicate check

Your task must be **novel**. The system compares your `instruction.md` against the existing
Terminal-Bench 3 benchmark, pulls the most similar tasks, and an AI judge decides whether yours is a
reworded or reskinned version of one that already exists. A duplicate blocks the PR — sharing a
domain or tool with an existing task is fine, but the underlying problem must be genuinely new.

### 3. Quality review

An AI reviewer reads your task against a quality rubric — is the instruction clear and unambiguous,
is the difficulty honest, do the tests actually verify what the instruction asks for? It posts its
findings and flags anything that needs work.

### 4. Validation

The system builds your task, runs **your** reference solution, and confirms it **fully passes**.
Then it runs a "do nothing" agent and confirms that **fails**. This catches two common problems: a
solution that doesn't actually work, and tests so lenient that an empty attempt would pass.

### 5. Difficulty check

Real AI agents attempt your task a couple of times. **At least one attempt must fail** — otherwise
the task is too easy to be useful. The comment summarizes how the agents did and how close they got.

### 6. Deep review

A thorough automated read-through for correctness and quality — checking the solution really solves
the task, the tests are sound, and the difficulty is genuine.

### 7. Verifier audit (advisory)

An automated audit double-checks that your **tests** can't be easily gamed and actually cover what
the instruction asks — looking for ways a wrong answer could still pass, or a right answer be wrongly
rejected. It posts advisory notes for the maintainer and **never blocks** your submission; treat any
findings as a chance to harden your `tests/`.

A maintainer may also run extra agent attempts on your PR; those are advisory too and won't block you.

---

## Test it yourself first (optional but recommended)

Catching problems locally is faster than waiting on the PR. With [Docker](https://www.docker.com/)
and [`uv`](https://docs.astral.sh/uv/) installed:

```bash
uv tool install "harbor==0.18.0"

# format checks
bash checks/static-report.sh tasks/<your-task-name>

# your solution must fully pass…
harbor run -p tasks/<your-task-name> --agent oracle --env docker

# …and a do-nothing agent must fail
harbor run -p tasks/<your-task-name> --agent nop --env docker
```

If those look right, your PR is very likely to sail through.

---

## Tips

- Write the **instruction** as if handing the task to a sharp colleague with no hints: state the
  goal and the exact required output, nothing more.
- Put **every** correctness check in `tests/` — that's the only place ground truth belongs.
- Make the tests hard to game. Check real behavior and real outputs, not the presence of a string.
- Keep it focused: one clear objective beats a sprawling multi-part task.

Questions or stuck? Open a draft PR anyway — the comments will tell you what's missing, and a
maintainer is happy to help.
