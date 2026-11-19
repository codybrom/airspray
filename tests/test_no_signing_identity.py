"""Guard: no Apple Developer team ID or signing identity may be committed.

The team ID belongs in macos/Config/Local.xcconfig, which is gitignored. Xcode writes
DEVELOPMENT_TEAM into the project file as soon as a team is picked under
Signing & Capabilities, so this fails loudly before that reaches a commit.
"""

import re
import subprocess
from pathlib import Path
from typing import List

REPO = Path(__file__).resolve().parent.parent

# Apple team IDs are ten upper-case alphanumerics. A signing identity carries
# one in parentheses, e.g. "Apple Development: Name (ABCDE12345)".
IDENTITY = re.compile(r"(Apple|Developer ID)[A-Za-z ]*: [^\"'\n]*\([A-Z0-9]{10}\)")
TEAM_ASSIGNMENT = re.compile(r"DEVELOPMENT_TEAM\"?\s*[:=]\s*\"?[A-Z0-9]{10}\b")


def _tracked_files() -> List[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line]


def test_no_team_id_or_signing_identity_in_committable_files() -> None:
    offenders: List[str] = []
    for path in _tracked_files():
        if not path.is_file() or path == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError, OSError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if IDENTITY.search(line) or TEAM_ASSIGNMENT.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert (
        not offenders
    ), "Signing identity or team ID found in files git would commit: " + ", ".join(
        offenders
    )


def test_local_signing_config_is_ignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "macos/Config/Local.xcconfig"], cwd=REPO
    )
    assert result.returncode == 0, "macos/Config/Local.xcconfig must be gitignored"
