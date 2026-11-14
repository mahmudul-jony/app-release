#!/usr/bin/env python3
"""
Centralized Version, Channel & Build Number Reservation Management.

Handles:
  1. Atomic build-number reservation and persistence BEFORE builds start.
  2. Strict monotonic increase: reserved build numbers are NEVER reused or decreased.
  3. Channel separation:
     - 'main' branch        -> 'production' channel (prerelease: false)
     - 'development' branch -> 'development' channel (prerelease: true)
  4. Version progression rules:
     - 'development': patch version +1, build number +1
     - 'main'       : version name unchanged, build number +1
  5. Multi-application safe state persistence with reservation audit history.
  6. Finalization of successful releases into channel metadata.
  7. Recording failure/cancellation without releasing reserved build numbers.
  8. Applying versions to fetched source pubspec.yaml in runner memory.
  9. Generating machine-readable public release-metadata.json.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, Optional, Tuple

CENTRAL_REPO = "mahmudul-jony/app-release"
MAX_RESERVATIONS_HISTORY = 50


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


def resolve_channel(branch: str) -> Tuple[str, bool]:
    """
    Resolve branch into release channel and prerelease flag.
    'main'        -> ('production', False)
    'development' -> ('development', True)
    """
    branch_clean = branch.strip().lower()
    if branch_clean == "main":
        return "production", False
    elif branch_clean == "development":
        return "development", True
    else:
        raise ValueError(
            f"Unsupported branch '{branch}'. Central release supports only 'development' and 'main'."
        )


def calculate_next_version(
    current_version_name: str,
    current_build_number: int,
    branch: str,
) -> Tuple[str, int]:
    """
    Calculate next version name and build number based on source branch rules:

    - 'development':
      1. Increase patch portion of centrally stored version name by one.
      2. Increase centrally stored build number by one.
    - 'main':
      1. Keep latest centrally stored version name unchanged.
      2. Increase centrally stored build number by one.
    """
    parse_semver(current_version_name)
    if not isinstance(current_build_number, int) or current_build_number < 1:
        raise ValueError(
            f"Current build number must be a positive integer, got: {current_build_number}"
        )

    branch_norm = branch.strip().lower()

    if branch_norm == "development":
        next_version_name = bump_patch(current_version_name)
    elif branch_norm == "main":
        next_version_name = current_version_name.strip()
    else:
        raise ValueError(
            f"Unsupported branch '{branch}'. Central release supports only 'development' and 'main'."
        )

    next_build_number = current_build_number + 1
    return next_version_name, next_build_number


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


def write_github_output(outputs: Dict[str, str]) -> None:
    """Write outputs to GITHUB_OUTPUT and GITHUB_ENV environment files."""
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


def cmd_reserve(args: argparse.Namespace) -> Dict[str, Any]:
    """
    Atomically reserve and persist the next build number and version in central state.
    Once reserved, the build number will NEVER be reused, even if builds fail or cancel.
    """
    state_path = Path(args.state_file).resolve()
    data = load_state(state_path)

    app_id = args.app.strip().lower()
    if app_id not in data["apps"]:
        raise KeyError(
            f"App '{args.app}' not configured in {state_path}. Available apps: {list(data['apps'].keys())}"
        )

    app_config = data["apps"][app_id]
    branch_clean = args.branch.strip().lower()
    channel, is_prerelease = resolve_channel(branch_clean)

    run_id = str(args.run_id or os.getenv("GITHUB_RUN_ID", "manual-local"))
    run_attempt = int(args.run_attempt or os.getenv("GITHUB_RUN_ATTEMPT", 1))

    # 1. Idempotency check: if this run_id already reserved for this app, return existing reservation
    existing_reservations = app_config.get("reservations", [])
    for res in existing_reservations:
        if res.get("run_id") == run_id and res.get("app_id") == app_id:
            print(f"Existing reservation found for run_id={run_id}: reusing reservation {res['reservation_id']}")
            outputs = {
                "APP_ID": app_id,
                "APP_NAME": app_config.get("app_name", args.app.title()),
                "REPOSITORY": app_config.get("repository", f"mahmudul-jony/{args.app}"),
                "PACKAGE_NAME": app_config.get("package_name", ""),
                "VERSION_NAME": res["version_name"],
                "BUILD_NUMBER": str(res["build_number"]),
                "VERSION_CODE": str(res["build_number"]),
                "VERSION_FULL": res["version_full"],
                "CHANNEL": res["channel"],
                "IS_PRERELEASE": "true" if res.get("is_prerelease", is_prerelease) else "false",
                "RELEASE_TAG": res["release_tag"],
                "RELEASE_TITLE": res["release_title"],
                "APK_ASSET_NAME": res["apk_asset_name"],
                "METADATA_ASSET_NAME": res["metadata_asset_name"],
                "RESERVATION_ID": res["reservation_id"],
                "SOURCE_BRANCH": branch_clean,
                "FLUTTER_VERSION": str(app_config.get("flutter_version", "3.41.9")),
                "FLUTTER_CHANNEL": str(app_config.get("flutter_channel", "stable")),
                "JAVA_VERSION": str(app_config.get("java_version", "17")),
            }
            write_github_output(outputs)
            return res

    # 2. Calculate next version and build number
    current_ver = str(app_config.get("current_version_name", "1.0.0"))
    current_build = int(app_config.get("current_build_number", 1))

    next_ver, next_build = calculate_next_version(current_ver, current_build, branch_clean)

    app_name = app_config.get("app_name", args.app.title())
    repository = app_config.get("repository", f"mahmudul-jony/{args.app}")
    package_name = app_config.get("package_name", "")
    flutter_version = app_config.get("flutter_version", "3.41.9")
    flutter_channel = app_config.get("flutter_channel", "stable")
    java_version = app_config.get("java_version", "17")

    version_full = f"{next_ver}+{next_build}"
    release_tag = f"{app_id}-v{version_full}-{channel}"
    release_title = f"{app_name} v{version_full} ({channel.title()})"
    apk_asset_name = f"{app_id}-v{version_full}-{channel}.apk"
    metadata_asset_name = f"{app_id}-v{version_full}-{channel}-metadata.json"
    reservation_id = f"{app_id}-b{next_build}-run{run_id}"

    now_iso = datetime.now(timezone.utc).isoformat()

    reservation = {
        "reservation_id": reservation_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "app_id": app_id,
        "channel": channel,
        "is_prerelease": is_prerelease,
        "branch": branch_clean,
        "version_name": next_ver,
        "build_number": next_build,
        "version_full": version_full,
        "release_tag": release_tag,
        "release_title": release_title,
        "apk_asset_name": apk_asset_name,
        "metadata_asset_name": metadata_asset_name,
        "commit_sha": args.commit_sha or None,
        "reserved_at": now_iso,
        "status": "reserved",
        "updated_at": now_iso,
    }

    # 3. Persist reservation into state immediately before any build starts
    app_config["current_version_name"] = next_ver
    app_config["current_build_number"] = next_build
    app_config["updated_at"] = now_iso
    app_config["active_reservation"] = reservation

    if "reservations" not in app_config or not isinstance(app_config["reservations"], list):
        app_config["reservations"] = []

    app_config["reservations"].append(reservation)
    if len(app_config["reservations"]) > MAX_RESERVATIONS_HISTORY:
        app_config["reservations"] = app_config["reservations"][-MAX_RESERVATIONS_HISTORY:]

    save_state(state_path, data)

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
        "CHANNEL": channel,
        "IS_PRERELEASE": "true" if is_prerelease else "false",
        "RELEASE_TAG": release_tag,
        "RELEASE_TITLE": release_title,
        "APK_ASSET_NAME": apk_asset_name,
        "METADATA_ASSET_NAME": metadata_asset_name,
        "RESERVATION_ID": reservation_id,
        "SOURCE_BRANCH": branch_clean,
        "FLUTTER_VERSION": str(flutter_version),
        "FLUTTER_CHANNEL": str(flutter_channel),
        "JAVA_VERSION": str(java_version),
    }

    write_github_output(outputs)

    if args.json:
        print(json.dumps(outputs, indent=2))
    else:
        print(f"=== Central Build Number Reserved ===")
        print(f"Application Identifier : {app_id}")
        print(f"Application Name       : {app_name}")
        print(f"Reservation ID         : {reservation_id}")
        print(f"Source Branch          : {branch_clean}")
        print(f"Release Channel        : {channel} (is_prerelease: {is_prerelease})")
        print(f"Previous Central State : {current_ver}+{current_build}")
        print(f"Reserved Version       : {version_full}")
        print(f"Release Tag            : {release_tag}")
        print(f"Target APK Asset       : {apk_asset_name}")
        print(f"Status                 : reserved (persisted to central state)")

    return reservation


def cmd_finalize_release(args: argparse.Namespace) -> None:
    """
    Finalize a successfully published release in central state:
    - Mark reservation status = 'published'
    - Update channel release record (channels.production / channels.development)
    - Ensure newer reservations are never rolled back
    """
    state_path = Path(args.state_file).resolve()
    data = load_state(state_path)

    app_id = args.app.strip().lower()
    if app_id not in data["apps"]:
        raise KeyError(f"App '{args.app}' not found in state file: {state_path}")

    app_config = data["apps"][app_id]
    build_number = int(args.build_number)
    run_id = str(args.run_id or "")

    now_iso = datetime.now(timezone.utc).isoformat()

    # Find the corresponding reservation
    matched_reservation = None
    for res in app_config.get("reservations", []):
        if res.get("build_number") == build_number and (not run_id or res.get("run_id") == run_id):
            matched_reservation = res
            break

    if not matched_reservation:
        # Fallback to active_reservation if matching
        active = app_config.get("active_reservation")
        if active and active.get("build_number") == build_number:
            matched_reservation = active

    if matched_reservation:
        matched_reservation["status"] = "published"
        matched_reservation["updated_at"] = now_iso
        if args.commit_sha:
            matched_reservation["commit_sha"] = args.commit_sha
        channel = matched_reservation["channel"]
        version_name = matched_reservation["version_name"]
        release_tag = matched_reservation["release_tag"]
        apk_asset_name = matched_reservation["apk_asset_name"]
    else:
        # If reservation not found in history, infer channel and values from arguments
        print(f"Warning: Reservation record for build {build_number} not found in history. Using arguments.")
        channel = args.channel or ("production" if args.branch == "main" else "development")
        version_name = args.version_name or app_config.get("current_version_name", "1.0.0")
        release_tag = f"{app_id}-v{version_name}+{build_number}-{channel}"
        apk_asset_name = f"{app_id}-v{version_name}+{build_number}-{channel}.apk"

    download_base = f"https://github.com/{CENTRAL_REPO}/releases/download/{release_tag}"
    apk_download_url = f"{download_base}/{apk_asset_name}"
    sha256_download_url = f"{download_base}/{apk_asset_name}.sha256"

    # Update channel-specific release record only if this build is newer or equal
    if "channels" not in app_config:
        app_config["channels"] = {}

    channel_record = app_config["channels"].get(channel, {})
    existing_channel_build = int(channel_record.get("build_number") or 0)

    if build_number >= existing_channel_build:
        app_config["channels"][channel] = {
            "channel": channel,
            "version_name": version_name,
            "build_number": build_number,
            "release_tag": release_tag,
            "apk_asset_name": apk_asset_name,
            "apk_download_url": apk_download_url,
            "sha256_download_url": sha256_download_url,
            "commit_sha": args.commit_sha or matched_reservation.get("commit_sha") if matched_reservation else None,
            "published_at": now_iso,
            "run_id": run_id,
        }
        print(f"Channel '{channel}' updated to {version_name}+{build_number}")
    else:
        print(
            f"Notice: Channel '{channel}' already has newer release (build {existing_channel_build}). "
            f"Not rolling back channel record for build {build_number}."
        )

    # Protect current build number from accidental rollback by stale runs
    app_config["current_build_number"] = max(
        int(app_config.get("current_build_number", 0)), build_number
    )
    app_config["updated_at"] = now_iso

    # Update last_build record
    app_config["last_build"] = {
        "channel": channel,
        "branch": matched_reservation.get("branch") if matched_reservation else args.branch,
        "commit_sha": args.commit_sha or (matched_reservation.get("commit_sha") if matched_reservation else None),
        "version_name": version_name,
        "build_number": build_number,
        "release_tag": release_tag,
        "built_at": now_iso,
        "status": "published",
    }

    # Clear active_reservation if it matches this build
    active = app_config.get("active_reservation")
    if active and isinstance(active, dict) and active.get("build_number") == build_number:
        app_config["active_reservation"] = None

    save_state(state_path, data)

    print(f"Release successfully finalized for '{app_id}':")
    print(f"  Channel      : {channel}")
    print(f"  Version      : {version_name}+{build_number}")
    print(f"  Release Tag  : {release_tag}")
    print(f"  Status       : published")


def cmd_record_failure(args: argparse.Namespace) -> None:
    """
    Record build or publishing failure in central state.
    CRITICAL: Never decrements or releases the reserved build number.
    """
    state_path = Path(args.state_file).resolve()
    data = load_state(state_path)

    app_id = args.app.strip().lower()
    if app_id not in data["apps"]:
        print(f"App '{args.app}' not in state, skipping failure record.")
        return

    app_config = data["apps"][app_id]
    build_number = int(args.build_number)
    run_id = str(args.run_id or "")
    reason = args.reason or "failed"

    now_iso = datetime.now(timezone.utc).isoformat()

    # Find matching reservation and mark status
    for res in app_config.get("reservations", []):
        if res.get("build_number") == build_number and (not run_id or res.get("run_id") == run_id):
            res["status"] = reason
            res["updated_at"] = now_iso
            if args.commit_sha:
                res["commit_sha"] = args.commit_sha
            print(f"Reservation {res['reservation_id']} marked as '{reason}'.")
            break

    # If active reservation matches, mark or clear it
    active = app_config.get("active_reservation")
    if active and isinstance(active, dict) and active.get("build_number") == build_number:
        app_config["active_reservation"]["status"] = reason
        app_config["active_reservation"]["updated_at"] = now_iso

    app_config["updated_at"] = now_iso
    save_state(state_path, data)

    print(f"Failure recorded for {app_id} build {build_number} (status={reason}). Build number preserved as consumed.")


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
        raise ValueError(f"No 'version:' field found in {pubspec_path} to update.")

    pubspec_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    print("Applied version to source pubspec.yaml:")
    print(f"  Previous : {old_version}")
    print(f"  Updated  : {target_version_line}")
    print(f"  Target   : {pubspec_path}")


def cmd_generate_metadata(args: argparse.Namespace) -> None:
    """Generate machine-readable release metadata JSON file for public client discovery."""
    apk_path = Path(args.apk_path).resolve()
    if not apk_path.is_file():
        raise FileNotFoundError(f"APK file not found: {apk_path}")

    apk_size = apk_path.stat().st_size
    with open(apk_path, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()

    channel, is_prerelease = resolve_channel(args.branch)
    version_full = f"{args.version_name}+{args.build_number}"

    download_base = f"https://github.com/{CENTRAL_REPO}/releases/download/{args.release_tag}"
    apk_download_url = f"{download_base}/{args.apk_asset_name}"
    sha256_download_url = f"{download_base}/{args.apk_asset_name}.sha256"

    now_iso = datetime.now(timezone.utc).isoformat()

    metadata = {
        "schema_version": 1,
        "app_id": args.app,
        "app_name": args.app_name,
        "package_name": args.package_name,
        "version_name": args.version_name,
        "build_number": int(args.build_number),
        "version_full": version_full,
        "channel": channel,
        "is_prerelease": is_prerelease,
        "source_branch": args.branch,
        "source_commit_sha": args.commit_sha,
        "release_tag": args.release_tag,
        "release_title": args.release_title,
        "published_at": now_iso,
        "apk": {
            "asset_name": args.apk_asset_name,
            "download_url": apk_download_url,
            "sha256": sha256,
            "sha256_download_url": sha256_download_url,
            "size_bytes": apk_size,
        },
    }

    out_file = Path(args.output_file).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
        f.write("\n")

    print(f"Generated public release metadata at {out_file}:")
    print(f"  App ID       : {args.app}")
    print(f"  Channel      : {channel}")
    print(f"  Version      : {version_full}")
    print(f"  APK SHA-256  : {sha256}")
    print(f"  Download URL : {apk_download_url}")


def cmd_validate_state(args: argparse.Namespace) -> None:
    """Validate format and consistency of the state file."""
    state_path = Path(args.state_file).resolve()
    data = load_state(state_path)

    required_app_keys = [
        "app_id",
        "app_name",
        "repository",
        "package_name",
        "current_version_name",
        "current_build_number",
        "channels",
    ]

    for app_id, app_cfg in data["apps"].items():
        for req in required_app_keys:
            if req not in app_cfg:
                raise ValueError(f"App '{app_id}' is missing required key '{req}' in {state_path}")

        parse_semver(app_cfg["current_version_name"])
        build_no = int(app_cfg["current_build_number"])
        if build_no < 1:
            raise ValueError(f"App '{app_id}' has non-positive build number: {build_no}")

        if "channels" in app_cfg:
            for ch in ["production", "development"]:
                if ch not in app_cfg["channels"]:
                    raise ValueError(f"App '{app_id}' missing channel '{ch}' in {state_path}")

    print(f"State file {state_path} is valid.")
    print(f"Configured apps: {list(data['apps'].keys())}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Centralized Build Reservation & Channel Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: reserve
    res_parser = subparsers.add_parser("reserve", help="Reserve and persist next build number")
    res_parser.add_argument("--app", required=True, help="App identifier (e.g. onewallet)")
    res_parser.add_argument("--branch", required=True, help="Branch ('development' or 'main')")
    res_parser.add_argument("--run-id", default=None, help="GitHub Actions run ID")
    res_parser.add_argument("--run-attempt", default=1, type=int, help="Run attempt number")
    res_parser.add_argument("--commit-sha", default=None, help="Source commit SHA if known")
    res_parser.add_argument(
        "--state-file", default="version-state.json", help="Path to version-state.json"
    )
    res_parser.add_argument("--json", action="store_true", help="Output JSON result")

    # Subcommand: finalize-release
    fin_parser = subparsers.add_parser("finalize-release", help="Finalize successful release")
    fin_parser.add_argument("--app", required=True, help="App identifier")
    fin_parser.add_argument("--build-number", required=True, type=int, help="Build number published")
    fin_parser.add_argument("--version-name", default=None, help="Version name published")
    fin_parser.add_argument("--branch", default="development", help="Source branch")
    fin_parser.add_argument("--channel", default=None, help="Channel (production/development)")
    fin_parser.add_argument("--commit-sha", default=None, help="Source commit SHA")
    fin_parser.add_argument("--run-id", default=None, help="GitHub Actions run ID")
    fin_parser.add_argument(
        "--state-file", default="version-state.json", help="Path to version-state.json"
    )

    # Subcommand: record-failure
    fail_parser = subparsers.add_parser("record-failure", help="Record build/publish failure")
    fail_parser.add_argument("--app", required=True, help="App identifier")
    fail_parser.add_argument("--build-number", required=True, type=int, help="Build number that failed")
    fail_parser.add_argument("--run-id", default=None, help="GitHub Actions run ID")
    fail_parser.add_argument("--reason", default="failed", help="Failure reason (failed/cancelled)")
    fail_parser.add_argument("--commit-sha", default=None, help="Source commit SHA")
    fail_parser.add_argument(
        "--state-file", default="version-state.json", help="Path to version-state.json"
    )

    # Subcommand: apply-to-source
    apply_parser = subparsers.add_parser(
        "apply-to-source", help="Update version in fetched source pubspec.yaml"
    )
    apply_parser.add_argument("--source-dir", required=True, help="Path to fetched source repo root")
    apply_parser.add_argument("--version-name", required=True, help="Version name (e.g. 1.0.1)")
    apply_parser.add_argument("--build-number", required=True, type=int, help="Build number (e.g. 2)")

    # Subcommand: generate-metadata
    meta_parser = subparsers.add_parser(
        "generate-metadata", help="Generate public release-metadata.json file"
    )
    meta_parser.add_argument("--app", required=True, help="App identifier")
    meta_parser.add_argument("--app-name", required=True, help="Application name")
    meta_parser.add_argument("--package-name", required=True, help="Package ID / Application ID")
    meta_parser.add_argument("--version-name", required=True, help="Version name")
    meta_parser.add_argument("--build-number", required=True, type=int, help="Build number")
    meta_parser.add_argument("--branch", required=True, help="Source branch")
    meta_parser.add_argument("--commit-sha", required=True, help="Source commit SHA")
    meta_parser.add_argument("--release-tag", required=True, help="Release tag")
    meta_parser.add_argument("--release-title", required=True, help="Release title")
    meta_parser.add_argument("--apk-asset-name", required=True, help="APK asset filename")
    meta_parser.add_argument("--apk-path", required=True, help="Path to signed APK")
    meta_parser.add_argument("--output-file", required=True, help="Path to write metadata JSON")

    # Subcommand: validate-state
    val_parser = subparsers.add_parser("validate-state", help="Validate version-state.json syntax")
    val_parser.add_argument(
        "--state-file", default="version-state.json", help="Path to version-state.json"
    )

    args = parser.parse_args()

    if args.command == "reserve":
        cmd_reserve(args)
    elif args.command == "finalize-release":
        cmd_finalize_release(args)
    elif args.command == "record-failure":
        cmd_record_failure(args)
    elif args.command == "apply-to-source":
        cmd_apply_to_source(args)
    elif args.command == "generate-metadata":
        cmd_generate_metadata(args)
    elif args.command == "validate-state":
        cmd_validate_state(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
