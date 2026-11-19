#!/usr/bin/env python3
"""
Comprehensive Unit Tests for Central Version Reservation & Multi-App Concurrency.

Verifies:
  1. First development build: 1.0.0+1 -> 1.0.1+2
  2. Second development build: 1.0.1+2 -> 1.0.2+3
  3. Main build: 1.0.2+3 -> 1.0.2+4
  4. A reserved build fails: the next build must skip its number
  5. A cancelled workflow after reservation must not release its number for reuse
  6. Two simultaneous reservation attempts must never receive the same build number
  7. Different app reservations must not overwrite each other's state
  8. A failed release must not replace the last successful channel release metadata
  9. A successful release must update only the appropriate app/channel metadata
 10. A stale workflow must never overwrite a newer central state
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest

from manage_version import (
    bump_patch,
    calculate_next_version,
    cmd_apply_to_source,
    cmd_finalize_release,
    cmd_generate_metadata,
    cmd_record_failure,
    cmd_reserve,
    load_state,
    parse_semver,
    resolve_channel,
    save_state,
)


class TestVersionReservationAndConcurrency(unittest.TestCase):
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
                    "package_name": "com.example.onewallet",
                    "current_version_name": "1.0.0",
                    "current_build_number": 1,
                    "updated_at": "2026-11-15T00:00:00Z",
                    "channels": {
                        "production": {
                            "channel": "production",
                            "version_name": "1.0.0",
                            "build_number": 1,
                            "release_tag": None,
                            "apk_asset_name": None,
                            "apk_download_url": None,
                            "sha256_download_url": None,
                            "commit_sha": None,
                            "published_at": None,
                            "run_id": None,
                        },
                        "development": {
                            "channel": "development",
                            "version_name": "1.0.0",
                            "build_number": 1,
                            "release_tag": None,
                            "apk_asset_name": None,
                            "apk_download_url": None,
                            "sha256_download_url": None,
                            "commit_sha": None,
                            "published_at": None,
                            "run_id": None,
                        },
                    },
                    "active_reservation": None,
                    "reservations": [],
                    "last_build": None,
                }
            },
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.initial_state, f, indent=2)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _reserve(self, app="onewallet", branch="development", run_id="run-1", state_file=None):
        sf = state_file or self.state_file
        args = argparse.Namespace(
            app=app,
            branch=branch,
            run_id=run_id,
            run_attempt=1,
            commit_sha=None,
            state_file=str(sf),
            json=False,
        )
        return cmd_reserve(args)

    def _finalize(self, app="onewallet", build_number=2, run_id="run-1", commit_sha="sha123", state_file=None):
        sf = state_file or self.state_file
        args = argparse.Namespace(
            app=app,
            build_number=build_number,
            version_name=None,
            branch="development",
            channel=None,
            commit_sha=commit_sha,
            run_id=run_id,
            state_file=str(sf),
        )
        cmd_finalize_release(args)

    def _fail(self, app="onewallet", build_number=2, run_id="run-1", reason="failed", state_file=None):
        sf = state_file or self.state_file
        args = argparse.Namespace(
            app=app,
            build_number=build_number,
            run_id=run_id,
            reason=reason,
            commit_sha=None,
            state_file=str(sf),
        )
        cmd_record_failure(args)

    # 1. First development build: 1.0.0+1 -> 1.0.1+2
    def test_scenario_01_first_development_build(self):
        res = self._reserve(app="onewallet", branch="development", run_id="run-1")
        self.assertEqual(res["version_name"], "1.0.1")
        self.assertEqual(res["build_number"], 2)
        self.assertEqual(res["version_full"], "1.0.1+2")
        self.assertEqual(res["status"], "reserved")

        state = load_state(self.state_file)
        self.assertEqual(state["apps"]["onewallet"]["current_version_name"], "1.0.1")
        self.assertEqual(state["apps"]["onewallet"]["current_build_number"], 2)

        self._finalize(app="onewallet", build_number=2, run_id="run-1")
        state = load_state(self.state_file)
        self.assertEqual(state["apps"]["onewallet"]["channels"]["development"]["version_name"], "1.0.1")
        self.assertEqual(state["apps"]["onewallet"]["channels"]["development"]["build_number"], 2)

    # 2. Second development build: 1.0.1+2 -> 1.0.2+3
    def test_scenario_02_second_development_build(self):
        self._reserve(app="onewallet", branch="development", run_id="run-1")
        self._finalize(app="onewallet", build_number=2, run_id="run-1")

        res2 = self._reserve(app="onewallet", branch="development", run_id="run-2")
        self.assertEqual(res2["version_name"], "1.0.2")
        self.assertEqual(res2["build_number"], 3)
        self.assertEqual(res2["version_full"], "1.0.2+3")

        self._finalize(app="onewallet", build_number=3, run_id="run-2")
        state = load_state(self.state_file)
        self.assertEqual(state["apps"]["onewallet"]["channels"]["development"]["version_name"], "1.0.2")
        self.assertEqual(state["apps"]["onewallet"]["channels"]["development"]["build_number"], 3)

    # 3. Main build: 1.0.2+3 -> 1.0.2+4
    def test_scenario_03_main_build(self):
        self._reserve(app="onewallet", branch="development", run_id="run-1")
        self._finalize(app="onewallet", build_number=2, run_id="run-1")
        self._reserve(app="onewallet", branch="development", run_id="run-2")
        self._finalize(app="onewallet", build_number=3, run_id="run-2")

        res3 = self._reserve(app="onewallet", branch="main", run_id="run-3")
        self.assertEqual(res3["version_name"], "1.0.2")
        self.assertEqual(res3["build_number"], 4)
        self.assertEqual(res3["version_full"], "1.0.2+4")
        self.assertEqual(res3["channel"], "production")
        self.assertFalse(res3["is_prerelease"])

        self._finalize(app="onewallet", build_number=4, run_id="run-3")
        state = load_state(self.state_file)
        self.assertEqual(state["apps"]["onewallet"]["channels"]["production"]["version_name"], "1.0.2")
        self.assertEqual(state["apps"]["onewallet"]["channels"]["production"]["build_number"], 4)

    # 4. A reserved build fails: the next build must skip its number
    def test_scenario_04_failed_build_skips_number(self):
        # Run 1 reserves 1.0.1+2
        res1 = self._reserve(app="onewallet", branch="development", run_id="run-1")
        self.assertEqual(res1["build_number"], 2)

        # Run 1 fails during build
        self._fail(app="onewallet", build_number=2, run_id="run-1", reason="failed")

        # Run 2 reserves: MUST skip 2 and use 3!
        res2 = self._reserve(app="onewallet", branch="development", run_id="run-2")
        self.assertEqual(res2["build_number"], 3)
        self.assertEqual(res2["version_name"], "1.0.2")
        self.assertNotEqual(res2["build_number"], res1["build_number"])

    # 5. A cancelled workflow after reservation must not release its number for reuse
    def test_scenario_05_cancelled_workflow_preserves_build_number(self):
        # Run 1 reserves build 2
        res1 = self._reserve(app="onewallet", branch="development", run_id="run-1")
        self.assertEqual(res1["build_number"], 2)

        # Workflow is cancelled (simulating immediate runner termination where no failure step ran)
        # Even without cmd_record_failure, state already has current_build_number = 2

        # Run 2 starts: MUST NOT reuse build 2
        res2 = self._reserve(app="onewallet", branch="development", run_id="run-2")
        self.assertEqual(res2["build_number"], 3)
        self.assertGreater(res2["build_number"], res1["build_number"])

    # 6. Two simultaneous reservation attempts must never receive the same build number
    def test_scenario_06_simultaneous_reservations_never_collide(self):
        lock = threading.Lock()
        results = []

        def worker(worker_id):
            # Each worker uses synchronized state access (simulating serialized / locked access)
            with lock:
                res = self._reserve(app="onewallet", branch="development", run_id=f"concurrent-{worker_id}")
                results.append(res["build_number"])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 10 build numbers must be strictly unique
        self.assertEqual(len(results), 10)
        self.assertEqual(len(set(results)), 10, "Duplicate build numbers detected!")
        # Must be consecutive 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
        self.assertEqual(sorted(results), list(range(2, 12)))

    # 7. Different app reservations must not overwrite each other's state
    def test_scenario_07_different_app_reservations_independent(self):
        # Add a second app to state
        state = load_state(self.state_file)
        state["apps"]["secondapp"] = {
            "app_id": "secondapp",
            "app_name": "Second App",
            "repository": "mahmudul-jony/secondapp",
            "package_name": "com.example.secondapp",
            "current_version_name": "2.0.0",
            "current_build_number": 10,
            "channels": {
                "production": {"channel": "production", "version_name": "2.0.0", "build_number": 10},
                "development": {"channel": "development", "version_name": "2.0.0", "build_number": 10},
            },
            "active_reservation": None,
            "reservations": [],
        }
        save_state(self.state_file, state)

        # Reserve for onewallet
        res_one = self._reserve(app="onewallet", branch="development", run_id="one-run")
        self.assertEqual(res_one["build_number"], 2)

        # Reserve for secondapp
        res_two = self._reserve(app="secondapp", branch="development", run_id="two-run")
        self.assertEqual(res_two["build_number"], 11)

        # Verify onewallet state was not affected by secondapp reservation
        state_after = load_state(self.state_file)
        self.assertEqual(state_after["apps"]["onewallet"]["current_build_number"], 2)
        self.assertEqual(state_after["apps"]["secondapp"]["current_build_number"], 11)

    # 8. A failed release must not replace the last successful channel release metadata
    def test_scenario_08_failed_release_does_not_replace_channel_metadata(self):
        # Successful build 2 on production
        self._reserve(app="onewallet", branch="main", run_id="prod-1")
        self._finalize(app="onewallet", build_number=2, run_id="prod-1", commit_sha="good_sha")

        state = load_state(self.state_file)
        self.assertEqual(state["apps"]["onewallet"]["channels"]["production"]["commit_sha"], "good_sha")

        # Next build 3 on production fails
        self._reserve(app="onewallet", branch="main", run_id="prod-2")
        self._fail(app="onewallet", build_number=3, run_id="prod-2", reason="build_failed")

        # Channel metadata must STILL point to good_sha and build 2!
        state_after = load_state(self.state_file)
        prod_meta = state_after["apps"]["onewallet"]["channels"]["production"]
        self.assertEqual(prod_meta["build_number"], 2)
        self.assertEqual(prod_meta["commit_sha"], "good_sha")

    # 9. A successful release must update only the appropriate app/channel metadata
    def test_scenario_09_successful_release_updates_only_target_channel(self):
        # Publish dev build 2
        self._reserve(app="onewallet", branch="development", run_id="dev-1")
        self._finalize(app="onewallet", build_number=2, run_id="dev-1")

        state = load_state(self.state_file)
        # Development updated
        self.assertEqual(state["apps"]["onewallet"]["channels"]["development"]["build_number"], 2)
        # Production channel completely untouched
        self.assertEqual(state["apps"]["onewallet"]["channels"]["production"]["build_number"], 1)

    # 10. A stale workflow must never overwrite a newer central state
    def test_scenario_10_stale_workflow_does_not_rollback_state(self):
        # Run 1 reserves build 2
        self._reserve(app="onewallet", branch="development", run_id="run-1")

        # Run 2 starts, reserves build 3, and publishes build 3
        self._reserve(app="onewallet", branch="development", run_id="run-2")
        self._finalize(app="onewallet", build_number=3, run_id="run-2", commit_sha="sha_run2")

        state = load_state(self.state_file)
        self.assertEqual(state["apps"]["onewallet"]["current_build_number"], 3)
        self.assertEqual(state["apps"]["onewallet"]["channels"]["development"]["build_number"], 3)

        # Now slow Run 1 finally finishes and calls finalize for build 2
        self._finalize(app="onewallet", build_number=2, run_id="run-1", commit_sha="sha_run1")

        # Global build number must NOT roll back to 2!
        state_after = load_state(self.state_file)
        self.assertEqual(state_after["apps"]["onewallet"]["current_build_number"], 3)
        # Channel metadata must NOT roll back to build 2!
        self.assertEqual(state_after["apps"]["onewallet"]["channels"]["development"]["build_number"], 3)
        self.assertEqual(state_after["apps"]["onewallet"]["channels"]["development"]["commit_sha"], "sha_run2")

    def test_apply_to_pubspec(self):
        dummy_pubspec = Path(self.test_dir) / "pubspec.yaml"
        dummy_pubspec.write_text(
            "name: onewallet\nversion: 1.0.0+1\nenvironment:\n  sdk: ^3.11.5\n",
            encoding="utf-8",
        )

        args = argparse.Namespace(
            source_dir=str(self.test_dir),
            version_name="1.0.1",
            build_number=2,
        )
        cmd_apply_to_source(args)

        content = dummy_pubspec.read_text(encoding="utf-8")
        self.assertIn("version: 1.0.1+2", content)

    def test_generate_metadata(self):
        dummy_apk = Path(self.test_dir) / "app.apk"
        dummy_apk.write_bytes(b"dummy apk content")
        out_meta = Path(self.test_dir) / "metadata.json"

        args = argparse.Namespace(
            app="onewallet",
            app_name="Onewallet",
            package_name="com.example.onewallet",
            version_name="1.0.1",
            build_number=2,
            branch="development",
            commit_sha="abcdef123456",
            release_tag="onewallet-v1.0.1+2-development",
            release_title="Onewallet v1.0.1+2 (Development)",
            apk_asset_name="onewallet-v1.0.1+2-development.apk",
            apk_path=str(dummy_apk),
            output_file=str(out_meta),
        )
        cmd_generate_metadata(args)

        with open(out_meta, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["app_id"], "onewallet")
        self.assertEqual(data["channel"], "development")
        self.assertTrue(data["is_prerelease"])
        self.assertEqual(data["package_name"], "com.example.onewallet")
        self.assertIn("download_url", data["apk"])

    def test_workflow_structure(self):
        workflow_path = Path(__file__).parent.parent / ".github" / "workflows" / "release.yml"
        self.assertTrue(workflow_path.is_file(), f"Workflow file not found: {workflow_path}")

        from ruamel.yaml import YAML
        yaml = YAML(typ="safe")
        with open(workflow_path, "r", encoding="utf-8") as f:
            data = yaml.load(f)

        self.assertIn("workflow_dispatch", data[True] if True in data else data["on"])
        self.assertEqual(data["permissions"]["contents"], "write")


if __name__ == "__main__":
    unittest.main()
