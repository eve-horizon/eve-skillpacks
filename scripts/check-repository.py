#!/usr/bin/env python3
"""Deterministic, read-only checks for the public eve-skillpacks repository."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


def git_files(repo: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return [repo / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def github_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    in_fence = False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        heading = re.sub(r"<[^>]+>", "", match.group(1)).strip().lower()
        heading = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE)
        anchor = re.sub(r"\s", "-", heading)
        index = counts.get(anchor, 0)
        counts[anchor] = index + 1
        anchors.add(anchor if index == 0 else f"{anchor}-{index}")
    return anchors


def markdown_without_code(text: str) -> str:
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(kept)


def line_for(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root_result = run(["git", "rev-parse", "--show-toplevel"], args.repo.resolve())
    if root_result.returncode:
        print(root_result.stderr.strip(), file=sys.stderr)
        return 1
    repo = Path(root_result.stdout.strip()).resolve()
    diagnostics: list[dict[str, object]] = []

    def add(path: Path | str, line: int, code: str, message: str) -> None:
        rel = str(path if isinstance(path, str) else path.relative_to(repo))
        diagnostics.append({"path": rel, "line": line, "code": code, "message": message})

    try:
        files = git_files(repo)
    except RuntimeError as error:
        add(".", 1, "GIT001", str(error).strip())
        files = []

    skill_names: dict[str, Path] = {}
    for path in sorted(p for p in files if p.name == "SKILL.md"):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            add(path, 1, "SKILL001", "SKILL.md must start with YAML frontmatter")
            continue
        try:
            end = next(index for index, value in enumerate(lines[1:], start=1) if value.strip() == "---")
        except StopIteration:
            add(path, 1, "SKILL002", "SKILL.md frontmatter has no closing delimiter")
            continue
        fields: dict[str, str] = {}
        for index, line in enumerate(lines[1:end], start=2):
            match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
            if match:
                fields[match.group(1)] = match.group(2).strip("\"'")
        for key in ("name", "description"):
            if not fields.get(key):
                add(path, 1, "SKILL003", f"frontmatter requires non-empty {key}")
        name = fields.get("name")
        if name:
            if name != path.parent.name:
                add(path, 2, "SKILL004", f"name {name!r} must match directory {path.parent.name!r}")
            if name in skill_names:
                add(path, 2, "SKILL005", f"duplicate skill name also used by {skill_names[name].relative_to(repo)}")
            else:
                skill_names[name] = path
        words = len(re.findall(r"\S+", text))
        if words >= 5000:
            add(path, 1, "SKILL006", f"SKILL.md has {words} words; limit is strictly below 5000")

    markdown_files = sorted(p for p in files if p.suffix.lower() == ".md")
    anchor_cache: dict[Path, set[str]] = {}
    link_pattern = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
    for path in markdown_files:
        original = path.read_text(encoding="utf-8")
        searchable = markdown_without_code(original)
        for match in link_pattern.finditer(searchable):
            destination = match.group(1).strip()
            if destination.startswith("<") and destination.endswith(">"):
                destination = destination[1:-1]
            destination = destination.split(maxsplit=1)[0]
            if not destination or re.match(r"^(https?://|mailto:|tel:)", destination):
                continue
            decoded = unquote(destination)
            target_part, separator, anchor = decoded.partition("#")
            target = path if not target_part else (path.parent / target_part).resolve()
            if not target.exists():
                add(path, line_for(searchable, match.start()), "LINK001", f"target does not exist: {target_part}")
                continue
            if separator and anchor and target.is_file() and target.suffix.lower() == ".md":
                anchors = anchor_cache.setdefault(target, github_anchors(target.read_text(encoding="utf-8")))
                if anchor.lower() not in anchors:
                    add(path, line_for(searchable, match.start()), "LINK002", f"anchor does not exist: #{anchor}")

    state_path = repo / ".sync-state.json"
    map_path = repo / ".sync-map.json"
    state: dict[str, object] = {}
    sync_map: dict[str, object] = {}
    for path, label in ((state_path, "state"), (map_path, "map")):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("top level must be an object")
            if label == "state":
                state = parsed
            else:
                sync_map = parsed
        except (OSError, ValueError, json.JSONDecodeError) as error:
            add(path, 1, "JSON001", f"invalid sync {label}: {error}")

    log = state.get("sync_log", [])
    if not isinstance(log, list):
        add(state_path, 1, "STATE001", "sync_log must be an array")
    else:
        if len(log) > 10:
            add(state_path, 1, "STATE002", f"sync_log has {len(log)} entries; maximum is 10")
        timestamps: list[datetime] = []
        for index, entry in enumerate(log):
            try:
                timestamps.append(datetime.fromisoformat(str(entry["synced_at"]).replace("Z", "+00:00")))
            except (KeyError, TypeError, ValueError):
                add(state_path, 1, "STATE003", f"sync_log[{index}] has invalid synced_at")
        if timestamps and timestamps != sorted(timestamps, reverse=True):
            add(state_path, 1, "STATE004", "sync_log must be newest first")
        if log and isinstance(log[0], dict):
            if log[0].get("commit") != state.get("last_synced_commit"):
                add(state_path, 1, "STATE005", "top sync_log commit must equal last_synced_commit")
            if log[0].get("synced_at") != state.get("last_synced_at"):
                add(state_path, 1, "STATE006", "top sync_log timestamp must equal last_synced_at")

    references = sync_map.get("reference_docs", {})
    if isinstance(references, dict):
        for target in references.values():
            target_path = repo / str(target)
            if not target_path.is_file():
                add(map_path, 1, "MAP001", f"reference target does not exist: {target}")
    else:
        add(map_path, 1, "MAP002", "reference_docs must be an object")
    triggers = sync_map.get("skill_triggers", {})
    if isinstance(triggers, dict):
        for targets in triggers.values():
            if not isinstance(targets, list):
                add(map_path, 1, "MAP003", "skill trigger targets must be arrays")
                continue
            for target in targets:
                if not (repo / str(target) / "SKILL.md").is_file():
                    add(map_path, 1, "MAP004", f"skill trigger target does not exist: {target}")
    else:
        add(map_path, 1, "MAP005", "skill_triggers must be an object")

    text_files = [p for p in files if p.suffix.lower() in {".md", ".yaml", ".yml", ".json", ".txt"}]
    forbidden = [
        (re.compile(r"eve\s+project\s+sync[^\n`]*--dry-run"), "SAFE001", "project sync has no safe --dry-run mode"),
        (re.compile(r"EVE_WORKER_VARIANT\s*=\s*full"), "STATE101", "obsolete full worker variant"),
        (re.compile(r"public\.ecr\.aws/[^\s`]+/worker-(?:base|full|python|rust|java|kotlin)"), "STATE102", "unsupported public worker variant image"),
        (re.compile(r"api\.eh1\.incept5\.dev|api\.corf\.ai|/Users/adam/"), "SCOPE001", "private runtime value in public content"),
    ]
    for path in sorted(text_files):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, code, message in forbidden:
            for match in pattern.finditer(text):
                add(path, line_for(text, match.start()), code, message)
        if path.name == "AGENTS.md":
            continue
        for match in re.finditer(r"(?:github\.com/)?Incept5/eve-(?:skillpacks|horizon)", text):
            add(path, line_for(text, match.start()), "SCOPE002", "legacy Incept5 repository outside retirement guardrail")

    risky_names = re.compile(r"(^|/)(?:\.env(?:\..*)?|credentials(?:\.json)?|kubeconfig|.*\.pem|.*\.key|terraform\.tfvars)$")
    for path in files:
        rel = path.relative_to(repo).as_posix()
        if risky_names.search(rel):
            add(path, 1, "SECRET001", "credential-like file must not be tracked")

    state_checker = repo / "private-skills/sync-horizon/scripts/check-state-today.sh"
    result = run(["bash", str(state_checker)], repo)
    if result.returncode:
        summary = (result.stdout + result.stderr).strip().replace("\n", "; ")
        add(state_checker, 1, "STATE201", f"state-today checker failed: {summary}")

    diagnostics.sort(key=lambda item: (str(item["path"]), int(item["line"]), str(item["code"])))
    if args.json:
        print(json.dumps({"ok": not diagnostics, "diagnostics": diagnostics}, indent=2))
    else:
        for item in diagnostics:
            print(f"{item['path']}:{item['line']}: [{item['code']}] {item['message']}")
        print(f"repository checks: {'PASS' if not diagnostics else 'FAIL'} ({len(diagnostics)} diagnostics)")
    return 1 if diagnostics else 0


if __name__ == "__main__":
    raise SystemExit(main())
