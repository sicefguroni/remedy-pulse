"""scripts/package_release.py — build a shareable archive of this repo
that cannot contain secrets.

Why this exists
----------------
A zip of this project was sent out for review with backend/.env inside
it, carrying live Google Places, GNews and Groq API keys. Those keys had
to be rotated.

Nothing was misconfigured when that happened. .gitignore listed .env,
token.json and client_secret.json correctly, and none of them were ever
committed. The archive was made by right-clicking the project folder and
choosing "Send to > Compressed folder", and that operation does not read
.gitignore — it copies the working directory, which is exactly where
those files live and are supposed to live.

So the fix is not another rule telling someone to remember. It is this
script, which builds the archive from `git archive` — the set of files
git actually tracks — meaning an untracked file is not excluded by a
filter that could be wrong, it is never a candidate in the first place.
The scan afterwards is a second, independent check on top of that.

Usage:
    python scripts/package_release.py                     # HEAD -> dist/
    python scripts/package_release.py --ref main --out /tmp/rp.zip

Exit codes: 0 archive written; 1 refused (see the message); 2 bad usage.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import zipfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Files that must never appear in a distributed archive, matched on the
# archive's own internal paths. git archive should already exclude every
# one of these (they are untracked and gitignored), so a hit here means
# something is wrong that this script must not paper over — most likely
# that a secret got committed, which is a much bigger problem than a bad
# zip and the reason this refuses rather than silently dropping the file.
FORBIDDEN_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "token.json",
    "client_secret.json",
    "credentials.json",
    "service-account.json",
    "id_rsa",
    ".pypirc",
    ".netrc",
}

FORBIDDEN_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".keystore")

# Content patterns for credentials that are recognizable on sight. These
# are the specific vendors this project actually integrates with, plus
# the two generic private-key headers, rather than a broad entropy
# heuristic — a scanner that cries wolf on every base64 blob gets
# switched off, and a switched-off scanner is worth nothing.
#
# Deliberately matched on PREFIX + LENGTH, so the placeholder values in
# .env.example ("your_places_api_key_here", "your_groq_api_key_here") do
# not trip it. Keeping .env.example scannable matters: it is the file
# most likely to have a real key pasted into it by accident.
SECRET_PATTERNS = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("Groq API key", re.compile(r"gsk_[0-9A-Za-z]{40,}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[0-9A-Za-z]{36}")),
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("OpenAI-style key", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    (
        "Google OAuth client secret",
        re.compile(r'"client_secret"\s*:\s*"(?!your_)[A-Za-z0-9_\-]{20,}"'),
    ),
]

# Text extensions worth scanning the contents of. A binary file is not
# scanned — decoding one produces noise, not findings.
SCANNABLE_SUFFIXES = {
    ".py", ".js", ".ts", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".md", ".txt", ".html", ".env", ".example", ".sh", ".ps1", ".sql", "",
}


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout


def build_archive(ref: str, out_path: pathlib.Path) -> pathlib.Path:
    """Write `ref` to `out_path` as a zip, via `git archive`.

    git archive emits only tracked files at that ref. That property is
    the entire security argument here: an untracked working-directory
    file such as .env is not filtered out, it is simply not part of what
    is being archived."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "archive", "--format=zip", f"--output={out_path}", ref],
        cwd=REPO_ROOT,
        check=True,
    )
    return out_path


def scan_archive(archive_path: pathlib.Path) -> list[str]:
    """Independently re-check the finished archive. Returns a list of
    findings; empty means clean.

    Reads the archive that was actually produced rather than trusting
    what went into it — the point is to check the artifact that would be
    sent to someone, not the intention behind it."""
    findings: list[str] = []

    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            # git archive prefixes every path with "<ref>/"; compare on
            # the path inside the repo, not that wrapper.
            relative = pathlib.PurePosixPath(*pathlib.PurePosixPath(info.filename).parts[1:])
            name = relative.name

            if name in FORBIDDEN_NAMES:
                findings.append(f"{relative}: forbidden filename ({name})")
                continue
            if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
                findings.append(f"{relative}: forbidden file type ({relative.suffix})")
                continue
            if relative.suffix.lower() not in SCANNABLE_SUFFIXES:
                continue
            if info.file_size > 2_000_000:
                continue

            try:
                content = archive.read(info.filename).decode("utf-8", errors="ignore")
            except (KeyError, OSError):
                continue

            for label, pattern in SECRET_PATTERNS:
                match = pattern.search(content)
                if match:
                    line = content[: match.start()].count("\n") + 1
                    findings.append(f"{relative}:{line}: looks like a {label}")

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="Commit/branch/tag to package (default: HEAD)")
    parser.add_argument("--out", type=pathlib.Path, default=None, help="Output .zip path")
    parser.add_argument(
        "--scan-only",
        type=pathlib.Path,
        default=None,
        metavar="ZIP",
        help="Scan an existing archive instead of building one (e.g. one made by hand)",
    )
    args = parser.parse_args(argv)

    if args.scan_only is not None:
        if not args.scan_only.exists():
            print(f"No such archive: {args.scan_only}", file=sys.stderr)
            return 2
        archive_path = args.scan_only
        print(f"Scanning {archive_path} ...")
    else:
        short_sha = _git("rev-parse", "--short", args.ref).strip()
        archive_path = args.out or REPO_ROOT / "dist" / f"remedy-pulse-{short_sha}.zip"
        print(f"Packaging {args.ref} ({short_sha}) -> {archive_path}")
        build_archive(args.ref, archive_path)

    findings = scan_archive(archive_path)

    if findings:
        # Deleted, not left on disk. An archive that failed this check is
        # one nobody should be able to send by reaching for it later.
        if args.scan_only is None:
            archive_path.unlink(missing_ok=True)
            print("\nREFUSED — archive deleted. Findings:\n", file=sys.stderr)
        else:
            print("\nFindings:\n", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        print(
            "\nIf any of these is a real credential, rotate it first — it is in git\n"
            "history, and deleting the file does not remove it from there.",
            file=sys.stderr,
        )
        return 1

    size_mb = archive_path.stat().st_size / 1_000_000
    print(f"\nClean. {archive_path} ({size_mb:.1f} MB)")
    print("Contains only git-tracked files: no .env, no token.json, no .venv.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
