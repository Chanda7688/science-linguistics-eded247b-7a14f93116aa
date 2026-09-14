"""Verifier tests — check the agent's ACTUAL outputs (not source strings). Ground truth lives
here, baked into the verifier image, so the agent never sees it. Test every behavior the
instruction describes; give each test an informative docstring; make cheating hard."""
import json, os


def test_output_exists():
    """The agent must produce the required artifact at the path named in instruction.md."""
    assert os.path.exists("/app/output.json"), "expected /app/output.json"


def test_output_correct():
    """The output must satisfy the task's correctness criteria (replace with real checks)."""
    got = json.load(open("/app/output.json"))
    assert got == {"TODO": "expected result"}
