#!/usr/bin/env python3
"""
Centralized Version Management Script for Flutter APK Releases.

Handles:
  1. Reading and validating central version state.
  2. Calculating next version names and build numbers according to branch rules:
     - 'development': patch version +1, build number +1
     - 'main': version name unchanged, build number +1
  3. Exporting calculated variables to GitHub Actions environment.
  4. Updating fetched source pubspec.yaml before build (without committing to source repo).
  5. Committing new version state after successful release publication.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, Tuple


def parse_semver(version_str: str) -> Tuple[int, int, int]:
    """Parse a semantic version string in X.Y.Z format."""
    clean = version_str.strip()
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", clean)
    if not match:
        raise ValueError(
            f"Invalid semantic version '{version_str}'. Expected format: MAJOR.MINOR.PATCH (e.g. 1.0.0)"
        )
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_patch(version_str: str) -> str:
    """Increment the patch component of a semantic version string (X.Y.Z -> X.Y.Z+1)."""
    major, minor, patch = parse_semver(version_str)
    return f"{major}.{minor}.{patch + 1}"


def load_state(state_file: Path) -> Dict[str, Any]:
    """Load and validate the central version state file."""
    if not state_file.is_file():
        raise FileNotFoundError(f"Version state file not found: {state_file}")

    with open(state_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "apps" not in data or not isinstance(data["apps"], dict):
        raise ValueError(f"Invalid state file format in {state_file}: missing 'apps' dictionary.")

    return data


def save_state(state_file: Path, data: Dict[str, Any]) -> None:
    """Save the updated state file atomically and pretty-formatted."""
    temp_file = state_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    temp_file.replace(state_file)


def calculate_next_version(
    current_version_name: str,
    current_build_number: int,
    branch: str,
) -> Tuple[str, int]:
    """
    Calculate the next version name and build number based on source branch rules:

    - 'development':
      1. Increase patch portion of centrally stored version name by one.
      2. Increase centrally stored build number by one.
    - 'main':
      1. Keep latest centrally stored version name unchanged.
      2. Increase centrally stored build number by one.
    """
    # Validate current values
    parse_semver(current_version_name)
    if not isinstance(current_build_number, int) or current_build_number < 1:
        raise ValueError(f"Current build number must be a positive integer, got: {current_build_number}")

    branch_norm = branch.strip().lower()

    if branch_norm == "development":
        next_version_name = bump_patch(current_version_name)
    elif branch_norm == "main":
        next_version_name = current_version_name.strip()
    else:
        raise ValueError(
            f"Unsupported branch '{branch}'. Central release only supports 'development' and 'main'."
        )

    next_build_number = current_build_number + 1
    return next_version_name, next_build_number


def write_github_output(outputs: Dict[str, str]) -> None:
    """Write outputs to GITHUB_OUTPUT environment file if available."""
    github_output_path = os.getenv("GITHUB_OUTPUT")
    if github_output_path:
        with open(github_output_path, "a", encoding="utf-8") as f:
            for key, val in outputs.items():
                f.write(f"{key}={val}\n")

    github_env_path = os.getenv("GITHUB_ENV")
    if github_env_path:
        with open(github_env_path, "a", encoding="utf-8") as f:
            for key, val in outputs.items():
                f.write(f"{key}={val}\n")


def cmd_calculate(args: argparse.Namespace) -> None:
    """Calculate next version and export workflow outputs."""
    state_path = Path(args.state_file).resolve()
    data = load_state(state_path)

    app_id = args.app.strip().lower()
    if app_id not in data["apps"]:
        raise KeyError(
            f"App '{args.app}' not configured in {state_path}. Available apps: {list(data['apps'].keys())}"
        )

    app_config = data["apps"][app_id]
    current_ver = str(app_config.get("current_version_name", "1.0.0"))
    current_build = int(app_config.get("current_build_number", 1))

    next_ver, next_build = calculate_next_version(current_ver, current_build, args.branch)

    app_name = app_config.get("app_name", args.app.title())
    repository = app_config.get("repository", f"mahmudul-jony/{args.app}")
    package_name = app_config.get("package_name", "")
    flutter_version = app_config.get("flutter_version", "3.41.9")
    flutter_channel = app_config.get("flutter_channel", "stable")
    java_version = app_config.get("java_version", "17")

    branch_clean = args.branch.strip().lower()
    version_full = f"{next_ver}+{next_build}"
    release_tag = f"{app_id}-v{version_full}-{branch_clean}"
    release_title = f"{app_name} v{version_full} ({branch_clean})"
    apk_asset_name = f"{app_id}-v{version_full}-{branch_clean}.apk"

    outputs = {
        "APP_ID": app_id,
        "APP_NAME": app_name,
        "REPOSITORY": repository,
        "PACKAGE_NAME": package_name,
        "CURRENT_VERSION_NAME": current_ver,
        "CURRENT_BUILD_NUMBER": str(current_build),
        "NEXT_VERSION_NAME": next_ver,
        "NEXT_BUILD_NUMBER": str(next_build),
        "VERSION_NAME": next_ver,
        "BUILD_NUMBER": str(next_build),
        "VERSION_CODE": str(next_build),
        "VERSION_FULL": version_full,
        "RELEASE_TAG": release_tag,
        "RELEASE_TITLE": release_title,
        "APK_ASSET_NAME": apk_asset_name,
        "SOURCE_BRANCH": branch_clean,
        "FLUTTER_VERSION": str(flutter_version),
        "FLUTTER_CHANNEL": str(flutter_channel),
        "JAVA_VERSION": str(java_version),
    }

    write_github_output(outputs)

    if args.json:
        print(json.dumps(outputs, indent=2))
    else:
        print(f"=== Version Calculation for {app_name} ===")
        print(f"Source Branch       : {branch_clean}")
        print(f"Current Version     : {current_ver}+{current_build}")
        print(f"Calculated Version  : {version_full}")
        print(f"Release Tag         : {release_tag}")
        print(f"Release Title       : {release_title}")
        print(f"Target APK Asset    : {apk_asset_name}")
        print(f"Source Repository   : {repository}")
        print(f"Expected Package    : {package_name}")
        print(f"Flutter / Java      : Flutter {flutter_version} ({flutter_channel}) / Java {java_version}")


def cmd_apply_to_source(args: argparse.Namespace) -> None:
    """Apply the calculated version to pubspec.yaml in the fetched source checkout."""
    source_dir = Path(args.source_dir).resolve()
    pubspec_path = source_dir / "pubspec.yaml"

    if not pubspec_path.is_file():
        raise FileNotFoundError(f"pubspec.yaml not found at {pubspec_path}")

    parse_semver(args.version_name)
    build_num = int(args.build_number)
    if build_num < 1:
        raise ValueError(f"Build number must be positive integer: {build_num}")

    target_version_line = f"version: {args.version_name}+{build_num}"

    content = pubspec_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    updated = False
    new_lines = []
    old_version = None

    for line in lines:
        if re.match(r"^version:\s*", line):
            old_version = line.strip()
            new_lines.append(target_version_line)
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        raise ValueError(
            f"No 'version:' field found in {pubspec_path} to update."
        )

    pubspec_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    print(f"Applied version to source pubspec.yaml:")
    print(f"  Previous: {old_version}")
    print(f"  Updated : {target_version_line}")
    print(f"  File    : {pubspec_path}")


def cmd_record_success(args: argparse.Namespace) -> None:
    """Record successful release in the central version state file."""
    state_path = Path(args.state_file).resolve()
    data = load_state(state_path)

    app_id = args.app.strip().lower()
    if app_id not in data["apps"]:
        raise KeyError(f"App '{args.app}' not found in state file: {state_path}")

    app_config = data["apps"][app_id]
    current_build = int(app_config.get("current_build_number", 0))
    new_build = int(args.build_number)

    if new_build <= current_build:
        raise ValueError(
            f"Cannot update state: New build number ({new_build}) must be strictly greater than "
            f"current build number ({current_build}). Decreasing or reusing build numbers is prohibited."
        )

    parse_semver(args.version_name)

    now_iso = datetime.now(timezone.utc).isoformat()

    app_config["current_version_name"] = args.version_name
    app_config["current_build_number"] = new_build
    app_config["updated_at"] = now_iso
    app_config["last_build"] = {
        "branch": args.branch,
        "commit_sha": args.commit_sha,
        "version_name": args.version_name,
        "build_number": new_build,
        "release_tag": args.release_tag,
        "built_at": now_iso,
    }

    save_state(state_path, data)

    print(f"Central version state updated successfully for '{app_id}':")
    print(f"  Version Name : {args.version_name}")
    print(f"  Build Number : {new_build}")
    print(f"  Branch       : {args.branch}")
    print(f"  Commit SHA   : {args.commit_sha}")
    print(f"  Release Tag  : {args.release_tag}")
    print(f"  Timestamp    : {now_iso}")


def cmd_validate_state(args: argparse.Namespace) -> None:
    """Validate format and consistency of the state file."""
    state_path = Path(args.state_file).resolve()
    data = load_state(state_path)

    required_app_keys = [
        "app_id",
        "app_name",
        "repository",
        "current_version_name",
        "current_build_number",
    ]

    for app_id, app_cfg in data["apps"].items():
        for req in required_app_keys:
            if req not in app_cfg:
                raise ValueError(f"App '{app_id}' is missing required key '{req}' in {state_path}")

        parse_semver(app_cfg["current_version_name"])
        build_no = int(app_cfg["current_build_number"])
        if build_no < 1:
            raise ValueError(f"App '{app_id}' has non-positive build number: {build_no}")

    print(f"State file {state_path} is valid.")
    print(f"Configured apps: {list(data['apps'].keys())}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Centralized Release Version Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: calculate
    calc_parser = subparsers.add_parser("calculate", help="Calculate next version for app and branch")
    calc_parser.add_argument("--app", required=True, help="App identifier (e.g. onewallet)")
    calc_parser.add_argument("--branch", required=True, help="Branch ('development' or 'main')")
    calc_parser.add_argument(
        "--state-file", default="version-state.json", help="Path to version-state.json"
    )
    calc_parser.add_argument("--json", action="store_true", help="Output JSON result")

    # Subcommand: apply-to-source
    apply_parser = subparsers.add_parser(
        "apply-to-source", help="Update version in fetched source pubspec.yaml"
    )
    apply_parser.add_argument("--source-dir", required=True, help="Path to fetched source repo root")
    apply_parser.add_argument("--version-name", required=True, help="Version name (e.g. 1.0.1)")
    apply_parser.add_argument("--build-number", required=True, type=int, help="Build number (e.g. 2)")

    # Subcommand: record-success
    rec_parser = subparsers.add_parser(
        "record-success", help="Record successful build into central version-state.json"
    )
    rec_parser.add_argument("--app", required=True, help="App identifier (e.g. onewallet)")
    rec_parser.add_argument("--version-name", required=True, help="New version name")
    rec_parser.add_argument("--build-number", required=True, type=int, help="New build number")
    rec_parser.add_argument("--branch", required=True, help="Source branch used")
    rec_parser.add_argument("--commit-sha", required=True, help="Source commit SHA")
    rec_parser.add_argument("--release-tag", required=True, help="Release tag published")
    rec_parser.add_argument(
        "--state-file", default="version-state.json", help="Path to version-state.json"
    )

    # Subcommand: validate-state
    val_parser = subparsers.add_parser("validate-state", help="Validate version-state.json syntax")
    val_parser.add_argument(
        "--state-file", default="version-state.json", help="Path to version-state.json"
    )

    args = parser.parse_args()

    if args.command == "calculate":
        cmd_calculate(args)
    elif args.command == "apply-to-source":
        cmd_apply_to_source(args)
    elif args.command == "record-success":
        cmd_record_success(args)
    elif args.command == "validate-state":
        cmd_validate_state(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
