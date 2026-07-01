from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CiWorkflowTest(unittest.TestCase):
    def test_release_workflow_validates_pull_requests_and_main_pushes(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        self.assertRegex(workflow, re.compile(r"pull_request:\s*\n\s*branches:\s*\n\s*-\s+main"))
        self.assertRegex(workflow, re.compile(r"push:\s*\n\s*branches:\s*\n\s*-\s+main"))
        self.assertRegex(workflow, re.compile(r"tags:\s*\n\s*-\s+\"v\*\""))
        self.assertIn("Run tests", workflow)
        self.assertIn("Build plugin", workflow)
        self.assertIn("Run C++ smoke", workflow)
        self.assertIn("ctest --test-dir", workflow)
        self.assertIn("Publish GitHub Release", workflow)


if __name__ == "__main__":
    unittest.main()
