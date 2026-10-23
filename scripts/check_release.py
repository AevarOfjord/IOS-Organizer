"""Check public source and built archives for accidental private runtime files.

Uses the Git index when available, so run after staging release changes. This is
an additional check, not a guarantee that arbitrary personal data is detected.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {"workspace", "backups", "probe", ".venv", ".git", ".codex", ".agents", "__pycache__", ".pytest_cache"}
PATTERNS = {
    "access token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{35,})"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "device identifier": re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}\b"),
    "local user path": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
}


def check_name(name: str) -> list[str]:
    parts = Path(name).parts
    errors = []
    if any(part in PRIVATE_PARTS for part in parts) or Path(name).name.startswith(".env"):
        errors.append(f"Private runtime path: {name}")
    if Path(name).suffix in {".pem", ".key", ".plist", ".pyc"}:
        errors.append(f"Unexpected sensitive/generated file: {name}")
    return errors


def check_content(name: str, data: bytes) -> list[str]:
    if Path(name).suffix.lower() == ".png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return [f"Invalid PNG asset: {name}"]
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [f"Unexpected binary file requiring review: {name}"]
    return [f"Possible {label} in {name}" for label, pattern in PATTERNS.items() if pattern.search(text)]


def source_files() -> list[Path]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    if result.returncode == 0:
        return [ROOT / name for name in result.stdout.decode().split("\0") if name]
    paths = [p for p in ROOT.iterdir() if p.is_file() and p.name != ".DS_Store" and not p.name.startswith(".env")]
    for directory in ("src", "tests", "docs", "examples", "scripts", ".github"):
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and not set(p.relative_to(ROOT).parts) & PRIVATE_PARTS)
    return sorted(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path)
    args = parser.parse_args()
    errors = []
    files = source_files()
    for path in files:
        name = path.relative_to(ROOT).as_posix()
        errors += check_name(name)
        if not path.is_file():
            errors.append(f"Tracked file missing: {name}")
            continue
        errors += check_content(name, path.read_bytes())
        if path.suffix == ".md":
            for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", path.read_text()):
                if "://" in target or target.startswith("#"):
                    continue
                target = target.split("#", 1)[0]
                if target and not (path.parent / target).exists():
                    errors.append(f"Broken local link in {name}: {target}")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    version_text = (ROOT / "src/ios_organizer/__init__.py").read_text()
    if f'__version__ = "{project["version"]}"' not in version_text:
        errors.append("Package versions disagree")
    for filename, expected in (("CLAUDE.md", "@AGENTS.md"), ("GEMINI.md", "@./AGENTS.md")):
        if (ROOT / filename).read_text().strip() != expected:
            errors.append(f"Agent entry point changed: review {filename}")
    archives = 0
    if args.dist:
        for path in sorted(args.dist.iterdir()):
            if path.suffix == ".whl":
                archives += 1
                with zipfile.ZipFile(path) as archive:
                    for name in archive.namelist():
                        if not name.endswith("/"):
                            errors += check_name(name) + check_content(name, archive.read(name))
            elif path.name.endswith(".tar.gz"):
                archives += 1
                with tarfile.open(path) as archive:
                    for member in archive.getmembers():
                        if member.isfile():
                            errors += check_name(member.name) + check_content(member.name, archive.extractfile(member).read())
        if archives < 2:
            errors.append("Expected both a wheel and a source archive")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Public-file checks passed: {len(files)} source files" + (f", {archives} distribution archives" if args.dist else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
