#!/usr/bin/env python3
"""Read-only consistency check between eve-skillpacks and eve-horizon source."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(source: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(source), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def source_parts(key: str) -> list[str]:
    return [part.strip() for part in key.split(" + ") if part.strip()]


def path_matches(path: str, pattern: str) -> bool:
    pattern = pattern.rstrip("/")
    return path == pattern or path.startswith(pattern + "/")


def normalized_remote(value: str) -> str:
    remote = value.strip().removesuffix(".git").removesuffix("/")
    if remote.startswith("git@github.com:"):
        remote = "github.com/" + remote.removeprefix("git@github.com:")
    elif remote.startswith("ssh://git@github.com/"):
        remote = "github.com/" + remote.removeprefix("ssh://git@github.com/")
    elif remote.startswith("https://"):
        remote = remote.removeprefix("https://")
    elif remote.startswith("http://"):
        remote = remote.removeprefix("http://")
    return remote.lower()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--source", type=Path)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    repo_result = subprocess.run(
        ["git", "-C", str(args.repo.resolve()), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if repo_result.returncode:
        print(repo_result.stderr.strip(), file=sys.stderr)
        return 1
    repo = Path(repo_result.stdout.strip()).resolve()

    try:
        state = json.loads((repo / ".sync-state.json").read_text(encoding="utf-8"))
        sync_map = json.loads((repo / ".sync-map.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"sync configuration error: {error}", file=sys.stderr)
        return 1

    configured = Path(str(state.get("eve_horizon_path", "")))
    source = args.source.resolve() if args.source else (repo / configured).resolve()
    report: dict[str, object] = {
        "ok": False,
        "repo": str(repo),
        "source": str(source),
        "baseline": state.get("last_synced_commit"),
        "requested_head": args.head,
        "changed": [],
        "dirty_watched": [],
        "mapped": {},
        "unmapped": [],
        "errors": [],
    }
    errors = report["errors"]
    assert isinstance(errors, list)

    inside = git(source, "rev-parse", "--is-inside-work-tree")
    if inside.returncode or inside.stdout.strip() != "true":
        errors.append("source is not a Git worktree")
        return finish(report, args.json, 1)

    remote = git(source, "remote", "get-url", "origin")
    report["origin"] = remote.stdout.strip()
    if remote.returncode or normalized_remote(remote.stdout) != "github.com/eve-horizon/eve-horizon":
        errors.append("source origin must be github.com/eve-horizon/eve-horizon")

    baseline = str(state.get("last_synced_commit", ""))
    if not baseline:
        errors.append("last_synced_commit is missing")
        return finish(report, args.json, 1)
    baseline_check = git(source, "cat-file", "-e", f"{baseline}^{{commit}}")
    if baseline_check.returncode:
        errors.append("recorded source commit is not reachable in this checkout")
        return finish(report, args.json, 2)

    head_result = git(source, "rev-parse", f"{args.head}^{{commit}}")
    if head_result.returncode:
        errors.append(f"source head cannot be resolved: {args.head}")
        return finish(report, args.json, 1)
    head = head_result.stdout.strip()
    report["head"] = head

    ancestor = git(source, "merge-base", "--is-ancestor", baseline, head)
    if ancestor.returncode:
        errors.append("recorded source commit is not an ancestor of source head")
        return finish(report, args.json, 2)

    references = sync_map.get("reference_docs", {})
    triggers = sync_map.get("skill_triggers", {})
    composites = sync_map.get("composite_triggers", {})
    watch_paths = sync_map.get("watch_paths", [])
    if not isinstance(references, dict) or not isinstance(triggers, dict) or not isinstance(composites, dict) or not isinstance(watch_paths, list):
        errors.append("sync map sections have invalid types")
        return finish(report, args.json, 1)

    declared_sources: set[str] = set()
    for key in references:
        declared_sources.update(source_parts(str(key)))
    declared_sources.update(str(key) for key in triggers)
    for source_path in sorted(declared_sources):
        exists = git(source, "cat-file", "-e", f"{head}:{source_path.rstrip('/')}")
        if exists.returncode:
            errors.append(f"declared source path does not exist at head: {source_path}")

    diff = git(source, "diff", "--name-only", "--diff-filter=ACDMRTUXB", f"{baseline}..{head}")
    if diff.returncode:
        errors.append("unable to compute source diff")
        return finish(report, args.json, 1)
    changed = sorted(line for line in diff.stdout.splitlines() if line)
    watched = sorted(path for path in changed if any(path_matches(path, str(pattern)) for pattern in watch_paths))
    report["changed"] = watched

    dirty = git(source, "status", "--porcelain=v1", "-z")
    dirty_watched: list[str] = []
    if dirty.returncode:
        errors.append("unable to inspect source worktree status")
    else:
        entries = [entry for entry in dirty.stdout.split("\0") if entry]
        index = 0
        while index < len(entries):
            entry = entries[index]
            status = entry[:2]
            path = entry[3:] if len(entry) > 3 else ""
            if status[0] in {"R", "C"} and index + 1 < len(entries):
                index += 1
                path = entries[index]
            if path and any(path_matches(path, str(pattern)) for pattern in watch_paths):
                dirty_watched.append(path)
            index += 1
    report["dirty_watched"] = sorted(set(dirty_watched))

    mapped: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for changed_path in watched:
        targets: set[str] = set()
        for source_key, target in references.items():
            if any(path_matches(changed_path, candidate) for candidate in source_parts(str(source_key))):
                targets.add(str(target))
        for source_key, trigger_targets in triggers.items():
            if path_matches(changed_path, str(source_key)) and isinstance(trigger_targets, list):
                targets.update(str(target) for target in trigger_targets)
        for target, config in composites.items():
            if isinstance(config, dict):
                patterns = config.get("watch_sources", [])
                if isinstance(patterns, list) and any(path_matches(changed_path, str(pattern)) for pattern in patterns):
                    targets.add(str(target))
        if targets:
            mapped[changed_path] = sorted(targets)
        else:
            unmapped.append(changed_path)
    report["mapped"] = mapped
    report["unmapped"] = unmapped

    if errors:
        return finish(report, args.json, 1)
    if watched or dirty_watched:
        return finish(report, args.json, 3)
    report["ok"] = True
    return finish(report, args.json, 0)


def finish(report: dict[str, object], as_json: bool, code: int) -> int:
    report["ok"] = code == 0
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"source sync check: {'PASS' if code == 0 else 'FAIL'}")
        print(f"baseline: {report.get('baseline')}")
        print(f"head: {report.get('head', '<unresolved>')}")
        changed = report.get("changed", [])
        dirty = report.get("dirty_watched", [])
        print(f"committed watched drift: {len(changed) if isinstance(changed, list) else 0}")
        print(f"dirty watched drift: {len(dirty) if isinstance(dirty, list) else 0}")
        for error in report.get("errors", []):
            print(f"error: {error}")
        for path in changed if isinstance(changed, list) else []:
            print(f"drift: {path}")
        for path in dirty if isinstance(dirty, list) else []:
            print(f"dirty: {path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
