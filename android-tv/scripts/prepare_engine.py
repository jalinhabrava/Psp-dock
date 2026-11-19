#!/usr/bin/env python3
"""Prepare the immutable PPSSPP checkout, or verify it without network access.

Gitlinks pin every recursive dependency. Existing edited/wrong-version checkouts
are rejected rather than reset. Android/CI builds use --verify-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
LOCK = json.loads((PROJECT / "engine.lock.json").read_text(encoding="utf-8"))


def git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), *args], check=True, text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip("\n")


def verify(source: Path, require_dependencies: bool = True) -> None:
    if not source.is_dir():
        raise RuntimeError("Engine source is missing. Run python3 scripts/prepare_engine.py first.")
    if Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source.resolve():
        raise RuntimeError("The engine source must be the root of its own Git checkout")
    if git(source, "rev-parse", "HEAD") != LOCK["commit"]:
        raise RuntimeError("Engine checkout does not match engine.lock.json; refusing to reset it")
    dirty = git(source, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all")
    if dirty:
        raise RuntimeError("Engine tracked files have changed; refusing a non-reproducible build")
    statuses = git(source, "submodule", "status", "--recursive").splitlines()
    for status in statuses:
        if not status:
            continue
        if status[0] in "+U" or (require_dependencies and status[0] == "-"):
            raise RuntimeError("Engine dependency is missing or differs from its pinned gitlink: " + status)
    if require_dependencies:
        dirty_dependencies = git(source, "status", "--porcelain", "--untracked-files=no")
        if dirty_dependencies:
            raise RuntimeError("Engine dependencies contain tracked changes")
    activity = source / "android/src/org/ppsspp/ppsspp/PpssppActivity.java"
    if hashlib.sha256(activity.read_bytes()).hexdigest() != LOCK["activity_sha256"]:
        raise RuntimeError("The upstream Activity differs from the reviewed embedding contract")


def prepare(source: Path) -> None:
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        # Fetch the full commit ID, not a mutable release tag or branch.
        subprocess.run(["git", "init", str(source)], check=True)
        git(source, "config", "core.autocrlf", "false")
        git(source, "remote", "add", "origin", LOCK["repository"])
        git(source, "fetch", "--depth", "1", "origin", LOCK["commit"])
        git(source, "checkout", "--detach", "FETCH_HEAD")
    verify(source, require_dependencies=False)
    git(source, "submodule", "update", "--init", "--recursive", "--depth", "1", "--jobs", "4")
    verify(source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=PROJECT / "third_party/ppsspp")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify_only:
            verify(args.source.resolve())
        else:
            prepare(args.source.resolve())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as failure:
        print("PPSSPP source preparation failed: " + str(failure), file=sys.stderr)
        return 1
    print("Verified PPSSPP " + LOCK["version"] + " at " + LOCK["commit"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
