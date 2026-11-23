"""수집 워크플로(.github/workflows/collect.yml)의 단계 순서·조건·캐시 키·pipefail 동작을 검증한다."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "collect.yml"


class CollectWorkflowStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.wf = yaml.safe_load(cls.raw)
        cls.steps = cls.wf["jobs"]["collect"]["steps"]
        cls.by_name = {s["name"]: s for s in cls.steps if "name" in s}

    def test_step_order_and_ids(self):
        self.assertEqual(self.steps[0]["uses"], "actions/checkout@v4")
        self.assertEqual(self.steps[1]["uses"], "actions/setup-python@v5")
        self.assertIn("pip install -r requirements.txt", self.steps[2]["run"])

        named = [(s["name"], s.get("id")) for s in self.steps if "name" in s]
        self.assertEqual(
            named,
            [
                ("Run tests", "tests"),
                ("Restore state", "restore_state"),
                ("Collect", "collect"),
                ("Commit finalized data", "commit_data"),
                ("Prune finalized state", "prune_state"),
                ("Save state", "save_state"),
                ("Summary", "summary"),
            ],
        )

    def test_collect_step_uses_bash_pipefail_defer_prune_and_tee(self):
        step = self.by_name["Collect"]
        self.assertEqual(step.get("shell"), "bash")
        run = step["run"]
        self.assertIn("set -o pipefail", run)
        self.assertIn("scripts/collect.py --defer-prune", run)
        self.assertIn("tee collect.log", run)

    def test_commit_step_runs_only_on_success_with_strict_shell(self):
        step = self.by_name["Commit finalized data"]
        self.assertNotIn("always()", str(step.get("if", "")))
        self.assertNotIn("if", step)
        self.assertEqual(step.get("shell"), "bash")
        run = step["run"]
        self.assertIn("set -euo pipefail", run)
        self.assertIn("git add data", run)
        self.assertIn("git commit", run)
        self.assertIn("git push", run)

    def test_prune_step_runs_only_after_collect_and_commit_succeed(self):
        step = self.by_name["Prune finalized state"]
        cond = step["if"]
        self.assertIn("success()", cond)
        self.assertIn("steps.collect.outcome == 'success'", cond)
        self.assertIn("steps.commit_data.outcome == 'success'", cond)
        self.assertEqual(step.get("shell"), "bash")
        run = step["run"]
        self.assertIn("set -o pipefail", run)
        self.assertIn("scripts/collect.py --prune-only", run)
        self.assertIn("tee -a collect.log", run)

    def test_single_save_cache_step_with_matching_immutable_keys(self):
        save_steps = [s for s in self.steps if s.get("uses") == "actions/cache/save@v4"]
        self.assertEqual(len(save_steps), 1)
        restore = self.by_name["Restore state"]
        save = self.by_name["Save state"]

        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(restore["with"]["path"], "state")
        self.assertEqual(save["with"]["path"], "state")

        r_key = restore["with"]["key"]
        s_key = save["with"]["key"]
        self.assertEqual(r_key, s_key)
        self.assertIn("github.run_id", s_key)
        self.assertIn("github.run_attempt", s_key)
        self.assertEqual(restore["with"]["restore-keys"], "radar-state-")

        cond = save["if"]
        self.assertIn("always()", cond)
        self.assertIn("steps.collect.outcome == 'success'", cond)
        self.assertIn("steps.collect.outcome == 'failure'", cond)
        self.assertIn("hashFiles('state/state.json.gz') != ''", cond)

    def test_summary_step_always_runs_and_preserves_collect_log(self):
        step = self.by_name["Summary"]
        self.assertEqual(step["if"], "always()")
        self.assertIn("collect.log", step["run"])
        self.assertIn("GITHUB_STEP_SUMMARY", step["run"])


@unittest.skipUnless(shutil.which("bash") and shutil.which("tee"), "bash/tee unavailable")
class CollectPipefailBehaviorTests(unittest.TestCase):
    def _run_bash(self, cwd: Path, cmd: str):
        return subprocess.run(
            ["bash", "-c", cmd],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_pipefail_with_tee_propagates_exit_codes_and_captures_log(self):
        tmp = Path(tempfile.mkdtemp())
        ok_py = tmp / "ok.py"
        ok_py.write_text("print('[run] feeds ok 1/1')\n", encoding="utf-8")
        fail_py = tmp / "fail.py"
        fail_py.write_text("import sys\nprint('[run] feeds ok 0/1')\nsys.exit(1)\n", encoding="utf-8")
        crash_py = tmp / "crash.py"
        crash_py.write_text("print('[run] starting')\nraise RuntimeError('boom')\n", encoding="utf-8")
        prune_py = tmp / "prune.py"
        prune_py.write_text("print('[pruned] 2026-11-02')\n", encoding="utf-8")

        p_ok = self._run_bash(tmp, f'set -o pipefail; "{sys.executable}" ok.py 2>&1 | tee collect.log')
        self.assertEqual(p_ok.returncode, 0)
        self.assertIn("[run] feeds ok 1/1", (tmp / "collect.log").read_text(encoding="utf-8"))

        p_prune = self._run_bash(tmp, f'set -o pipefail; "{sys.executable}" prune.py 2>&1 | tee -a collect.log')
        self.assertEqual(p_prune.returncode, 0)
        log_text = (tmp / "collect.log").read_text(encoding="utf-8")
        self.assertIn("[run] feeds ok 1/1", log_text)
        self.assertIn("[pruned] 2026-11-02", log_text)

        p_fail = self._run_bash(tmp, f'set -o pipefail; "{sys.executable}" fail.py 2>&1 | tee collect.log')
        self.assertEqual(p_fail.returncode, 1)
        self.assertIn("[run] feeds ok 0/1", (tmp / "collect.log").read_text(encoding="utf-8"))

        p_crash = self._run_bash(tmp, f'set -o pipefail; "{sys.executable}" crash.py 2>&1 | tee collect.log')
        self.assertNotEqual(p_crash.returncode, 0)
        crash_log = (tmp / "collect.log").read_text(encoding="utf-8")
        self.assertIn("[run] starting", crash_log)
        self.assertIn("RuntimeError: boom", crash_log)


if __name__ == "__main__":
    unittest.main()
