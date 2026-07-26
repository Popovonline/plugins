from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = SKILL_ROOT / "scripts" / "inspect_pr_checks.py"


def load_inspector():
    spec = importlib.util.spec_from_file_location("gh_fix_ci_inspector", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load gh-fix-ci inspector")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FetchChecksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inspector = load_inspector()
        cls.repo = Path("/fixture/repo")

    def test_primary_json_path_is_preserved(self) -> None:
        payload = [
            {
                "name": "unit",
                "state": "SUCCESS",
                "detailsUrl": "https://example.test/actions/runs/1",
            }
        ]
        with patch.object(
            self.inspector,
            "run_gh_command",
            return_value=self.inspector.GhResult(0, json.dumps(payload), ""),
        ) as command:
            checks = self.inspector.fetch_checks("17", self.repo)

        self.assertEqual(checks, payload)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(command.call_args.args[0][:3], ["pr", "checks", "17"])

    def test_unsupported_json_flag_uses_status_rollup(self) -> None:
        rollup = {
            "statusCheckRollup": [
                {
                    "__typename": "CheckRun",
                    "name": "tests",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://example.test/actions/runs/42/job/7",
                    "workflowName": "CI",
                },
                {
                    "__typename": "StatusContext",
                    "context": "external/lint",
                    "state": "SUCCESS",
                    "targetUrl": "https://checks.example.test/9",
                },
            ]
        }
        responses = [
            self.inspector.GhResult(1, "", "unknown flag: --json"),
            self.inspector.GhResult(0, json.dumps(rollup), ""),
        ]
        with patch.object(
            self.inspector,
            "run_gh_command",
            side_effect=responses,
        ) as command:
            checks = self.inspector.fetch_checks("18", self.repo)

        self.assertIsNotNone(checks)
        assert checks is not None
        self.assertEqual(command.call_count, 2)
        self.assertEqual(
            command.call_args_list[1].args[0],
            ["pr", "view", "18", "--json", "statusCheckRollup"],
        )
        self.assertEqual(checks[0]["name"], "tests")
        self.assertEqual(checks[0]["workflow"], "CI")
        self.assertTrue(self.inspector.is_failing(checks[0]))
        self.assertEqual(checks[1]["name"], "external/lint")
        self.assertEqual(
            checks[1]["detailsUrl"],
            "https://checks.example.test/9",
        )
        self.assertFalse(self.inspector.is_failing(checks[1]))

    def test_field_drift_still_uses_reported_available_fields(self) -> None:
        responses = [
            self.inspector.GhResult(
                1,
                "",
                "invalid field\nAvailable fields:\nname\nstate\nbucket\nlink\n",
            ),
            self.inspector.GhResult(
                0,
                json.dumps([{"name": "lint", "bucket": "fail"}]),
                "",
            ),
        ]
        with patch.object(
            self.inspector,
            "run_gh_command",
            side_effect=responses,
        ) as command:
            checks = self.inspector.fetch_checks("19", self.repo)

        self.assertEqual(checks, [{"name": "lint", "bucket": "fail"}])
        self.assertEqual(
            command.call_args_list[1].args[0],
            ["pr", "checks", "19", "--json", "name,state,bucket,link"],
        )

    def test_malformed_status_rollup_fails_closed(self) -> None:
        responses = [
            self.inspector.GhResult(1, "", "unknown flag: --json"),
            self.inspector.GhResult(
                0,
                json.dumps({"statusCheckRollup": {"name": "not-a-list"}}),
                "",
            ),
        ]
        stderr = io.StringIO()
        with (
            patch.object(
                self.inspector,
                "run_gh_command",
                side_effect=responses,
            ),
            redirect_stderr(stderr),
        ):
            checks = self.inspector.fetch_checks("20", self.repo)

        self.assertIsNone(checks)
        self.assertIn("unexpected PR status rollup JSON shape", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
