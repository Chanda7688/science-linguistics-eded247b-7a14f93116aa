import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tb3-review.yml"
CANDIDATE_SHA = "d27e555806e1f025f1eef3603d9984cc5fb8490e"
ROLLBACK_SHA = "9bb3166fe5364531d8e8a64769aeddb63ceaaf95"


class Tb3ReviewActivationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text()

    def test_pins_candidate_and_rollback_to_distinct_commits(self) -> None:
        self.assertRegex(CANDIDATE_SHA, r"^[0-9a-f]{40}$")
        self.assertRegex(ROLLBACK_SHA, r"^[0-9a-f]{40}$")
        self.assertNotEqual(CANDIDATE_SHA, ROLLBACK_SHA)
        self.assertIn(
            "uses: handshake-project-insight/handshake-orchestration-tb3/"
            f".github/workflows/tb3-review.yml@{CANDIDATE_SHA}",
            self.workflow,
        )
        self.assertIn(f"checks_ref: {CANDIDATE_SHA}", self.workflow)
        self.assertIn(f"rollout_rollback_ref: {ROLLBACK_SHA}", self.workflow)

    def test_enables_redesigned_review_only_for_real_task_repositories(self) -> None:
        self.assertIn(
            "redesigned_pipeline_canary: "
            "${{ startsWith(github.repository, "
            "'handshake-project-insight/insight-') || "
            "contains(github.repository, '-eded247b-') }}",
            self.workflow,
        )
        self.assertNotIn(
            "redesigned_pipeline_canary: true",
            self.workflow,
        )

    def test_does_not_follow_mutable_main_for_automated_review(self) -> None:
        mutable_pointer = re.compile(
            r"uses:\s*handshake-project-insight/handshake-orchestration-tb3/"
            r"\.github/workflows/tb3-review\.yml@main"
        )
        self.assertNotRegex(self.workflow, mutable_pointer)


if __name__ == "__main__":
    unittest.main()
