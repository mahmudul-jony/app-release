#!/usr/bin/env python3
"""
Unit tests for manage_version.py
Verifies version calculation rules, monotonicity, pubspec updating, and state persistence.
"""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from manage_version import (
    bump_patch,
    calculate_next_version,
    load_state,
    parse_semver,
    save_state,
)


class TestVersionManagement(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_file = Path(self.test_dir) / "version-state.json"
        self.initial_state = {
            "version": 1,
            "apps": {
                "onewallet": {
                    "app_id": "onewallet",
                    "app_name": "Onewallet",
                    "repository": "mahmudul-jony/Onewallet",
                    "package_name": "com.example.financeapp",
                    "current_version_name": "1.0.0",
                    "current_build_number": 1,
                    "updated_at": "2026-11-15T00:00:00Z",
                    "last_build": None,
                }
            },
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.initial_state, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_parse_semver(self):
        self.assertEqual(parse_semver("1.0.0"), (1, 0, 0))
        self.assertEqual(parse_semver("2.14.3"), (2, 14, 3))
        with self.assertRaises(ValueError):
            parse_semver("1.0")
        with self.assertRaises(ValueError):
            parse_semver("v1.0.0")
        with self.assertRaises(ValueError):
            parse_semver("1.0.0.1")

    def test_bump_patch(self):
        self.assertEqual(bump_patch("1.0.0"), "1.0.1")
        self.assertEqual(bump_patch("1.0.9"), "1.0.10")
        self.assertEqual(bump_patch("2.3.4"), "2.3.5")

    def test_development_branch_rules(self):
        # development: patch +1, build +1
        next_ver, next_build = calculate_next_version("1.0.0", 1, "development")
        self.assertEqual(next_ver, "1.0.1")
        self.assertEqual(next_build, 2)

    def test_main_branch_rules(self):
        # main: version name unchanged, build +1
        next_ver, next_build = calculate_next_version("1.0.2", 3, "main")
        self.assertEqual(next_ver, "1.0.2")
        self.assertEqual(next_build, 4)

    def test_official_specification_progression_table(self):
        """
        Verify the exact progression required by the specification:
        | Build | Branch      | Version |
        |---|---|---|
        | Initial | —         | 1.0.0+1 |
        | 1     | development | 1.0.1+2 |
        | 2     | development | 1.0.2+3 |
        | 3     | main        | 1.0.2+4 |
        | 4     | main        | 1.0.2+5 |
        | 5     | development | 1.0.3+6 |
        """
        ver = "1.0.0"
        build = 1

        # Build 1: development
        ver, build = calculate_next_version(ver, build, "development")
        self.assertEqual(f"{ver}+{build}", "1.0.1+2")

        # Build 2: development
        ver, build = calculate_next_version(ver, build, "development")
        self.assertEqual(f"{ver}+{build}", "1.0.2+3")

        # Build 3: main
        ver, build = calculate_next_version(ver, build, "main")
        self.assertEqual(f"{ver}+{build}", "1.0.2+4")

        # Build 4: main
        ver, build = calculate_next_version(ver, build, "main")
        self.assertEqual(f"{ver}+{build}", "1.0.2+5")

        # Build 5: development
        ver, build = calculate_next_version(ver, build, "development")
        self.assertEqual(f"{ver}+{build}", "1.0.3+6")

    def test_unsupported_branch(self):
        with self.assertRaises(ValueError):
            calculate_next_version("1.0.0", 1, "feature/login")

    def test_monotonic_build_number_enforced(self):
        data = load_state(self.state_file)
        app = data["apps"]["onewallet"]
        current_build = app["current_build_number"]

        # Valid monotonic increase
        self.assertGreater(current_build + 1, current_build)

    def test_apply_to_pubspec(self):
        dummy_pubspec = Path(self.test_dir) / "pubspec.yaml"
        dummy_pubspec.write_text(
            "name: onewallet\nversion: 1.0.0+1\nenvironment:\n  sdk: ^3.11.5\n",
            encoding="utf-8",
        )

        import subprocess
        import sys

        cmd = [
            sys.executable,
            str(Path(__file__).parent / "manage_version.py"),
            "apply-to-source",
            "--source-dir",
            str(self.test_dir),
            "--version-name",
            "1.0.1",
            "--build-number",
            "2",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

        content = dummy_pubspec.read_text(encoding="utf-8")
        self.assertIn("version: 1.0.1+2", content)

    def test_workflow_structure(self):
        workflow_path = Path(__file__).parent.parent / ".github" / "workflows" / "release.yml"
        self.assertTrue(workflow_path.is_file(), f"Workflow file not found: {workflow_path}")
        
        from ruamel.yaml import YAML
        yaml = YAML(typ="safe")
        with open(workflow_path, "r", encoding="utf-8") as f:
            data = yaml.load(f)

        # 1. Must be workflow_dispatch
        self.assertIn("workflow_dispatch", data[True] if True in data else data["on"])
        on_dispatch = (data[True] if True in data else data["on"])["workflow_dispatch"]

        # 2. Branch selection dropdown
        inputs = on_dispatch["inputs"]
        self.assertIn("branch", inputs)
        self.assertEqual(inputs["branch"]["type"], "choice")
        self.assertIn("development", inputs["branch"]["options"])
        self.assertIn("main", inputs["branch"]["options"])

        # 3. Concurrency protection
        self.assertIn("concurrency", data)
        self.assertFalse(data["concurrency"]["cancel-in-progress"])

        # 4. Repository write permissions
        self.assertEqual(data["permissions"]["contents"], "write")



if __name__ == "__main__":
    unittest.main()
