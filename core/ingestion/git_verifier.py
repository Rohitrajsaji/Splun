"""Git-Diff & Commit Verification Engine.

Captures commit diffs or raw patch text, registers verbatim GIT_DIFF evidence
in the evidence vault, extracts referenced scenario IDs, and upgrades scenario
statuses deterministically to 'Verified' backed by immutable proof.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Optional

from core.engine.chat_processor import extract_scenario_nums
from core.storage.db import ProjectMemoryDB

DIFF_FILE_RE = re.compile(r"^(?:---|\+\+\+)\s+[ab]/(.*)$", re.MULTILINE)
GIT_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$", re.MULTILINE)


def extract_changed_files(diff_text: str) -> list[str]:
    """Parse unified diff text to extract list of modified file paths."""
    files: list[str] = []
    for m in GIT_DIFF_HEADER_RE.finditer(diff_text):
        f = m.group(2)
        if f not in files:
            files.append(f)
    if not files:
        for m in DIFF_FILE_RE.finditer(diff_text):
            f = m.group(1).strip()
            if f and f != "/dev/null" and f not in files:
                files.append(f)
    return files


def verify_scenarios_from_git(
    db: ProjectMemoryDB,
    project_id: str,
    repo_path: Optional[str] = None,
    diff_text: Optional[str] = None,
    commit_sha: str = "HEAD",
    scenario_ids: Optional[list[str]] = None,
) -> dict:
    """Verify scenarios using a Git commit diff or supplied patch text.

    - If repo_path is provided and diff_text is None, executes `git show`
      to extract the commit message, stats, and patch.
    - Automatically discovers referenced scenarios (e.g., 'SC-004', 'SC-005')
      from commit message and diff text, or applies explicit scenario_ids.
    - Records verbatim diff into evidence_vault (source_type='GIT_DIFF').
    - Upgrades each scenario to 'Verified' with verification_evidence_id set.
    """
    db.require_project(project_id)

    raw_diff = diff_text
    actual_sha = commit_sha

    # 1. Capture from git if repo_path provided and diff_text is omitted
    if raw_diff is None:
        if not repo_path:
            raise ValueError("Either 'diff_text' or 'repo_path' must be provided")
        rp = Path(repo_path).resolve()
        if not rp.is_dir():
            raise NotADirectoryError(f"Git repo path does not exist: {repo_path}")

        # Resolve exact SHA if possible
        rev_proc = subprocess.run(
            ["git", "rev-parse", commit_sha],
            cwd=str(rp),
            capture_output=True,
            text=True,
        )
        if rev_proc.returncode == 0 and rev_proc.stdout.strip():
            actual_sha = rev_proc.stdout.strip()[:12]

        show_proc = subprocess.run(
            ["git", "show", "--stat", "--patch", commit_sha],
            cwd=str(rp),
            capture_output=True,
            text=True,
        )
        if show_proc.returncode != 0:
            raise RuntimeError(f"git show failed on {commit_sha}: {show_proc.stderr.strip()}")
        raw_diff = show_proc.stdout

    if not raw_diff or not raw_diff.strip():
        raise ValueError("Diff content is empty; cannot verify scenarios without proof")

    # 2. Extract referenced scenarios
    target_nums: list[int] = []
    if scenario_ids:
        for ref in scenario_ids:
            nums = extract_scenario_nums(str(ref))
            if nums:
                target_nums.extend(nums)
            elif str(ref).isdigit():
                target_nums.append(int(ref))
    else:
        target_nums = extract_scenario_nums(raw_diff)

    # De-duplicate while preserving order
    unique_nums = list(dict.fromkeys(target_nums))

    # 3. Store verbatim diff into evidence_vault
    diff_bytes = raw_diff.encode("utf-8")
    digest = hashlib.sha256(diff_bytes).hexdigest()[:12]
    evidence_id = f"DIFF-{actual_sha[:8]}-{digest}"
    source_ref = f"git:{actual_sha}"

    with db.transaction():
        db.add_evidence(
            project_id=project_id,
            evidence_id=evidence_id,
            source_type="GIT_DIFF",
            source_reference=source_ref,
            content=raw_diff,
            byte_range=f"0-{len(diff_bytes)}",
        )

        verified_scenarios: list[dict] = []
        for num in unique_nums:
            sc = db.get_scenario(project_id, num)
            if sc:
                db.upsert_scenario(
                    project_id,
                    {
                        "id": sc["id"],
                        "scenario_num": sc["scenario_num"],
                        "status": "Verified",
                        "verification_evidence_id": evidence_id,
                        # Clear blockers when verified by implementation proof
                        "blockers": None if sc["status"] == "Blocked" else sc.get("blockers"),
                    },
                )
                updated_sc = db.get_scenario(project_id, num)
                if updated_sc:
                    verified_scenarios.append(updated_sc)

    changed_files = extract_changed_files(raw_diff)

    return {
        "evidence_id": evidence_id,
        "commit_sha": actual_sha,
        "verified_scenarios": [s["id"] for s in verified_scenarios],
        "scenarios": verified_scenarios,
        "changed_files": changed_files,
    }
