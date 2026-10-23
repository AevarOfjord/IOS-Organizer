"""Backup and opaque artifact persistence for ios-organizer."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ios_organizer import __version__
from ios_organizer.compiler import from_json_compatible, to_json_compatible


@dataclass(frozen=True)
class BackupRecord:
    """Metadata and file paths for a single timestamped backup."""

    path: Path
    meta_path: Path
    filename: str
    created_at: str
    device_name: str
    model: str
    product_type: str
    udid: str
    ios_version: str
    build_version: str
    tool_version: str


def write_backup(
    raw_state: list[Any],
    device_name: str,
    udid: str,
    ios_version: str,
    model: str = "iPhone",
    product_type: str = "Unknown",
    build_version: str = "Unknown",
    backup_dir: Path = Path("backups"),
    now: datetime.datetime | None = None,
) -> BackupRecord:
    """Write a timestamped raw icon state backup and its .meta.json sidecar."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = now or datetime.datetime.now().astimezone()
    stem = f"backup_{ts.strftime('%Y%m%d_%H%M%S')}"
    backup_path = backup_dir / f"{stem}.json"
    counter = 1
    while backup_path.exists():
        stem_candidate = f"{stem}_{counter}"
        backup_path = backup_dir / f"{stem_candidate}.json"
        counter += 1

    meta_path = backup_path.with_suffix(".meta.json")
    filename = backup_path.name
    created_at = ts.isoformat(timespec="seconds")

    json_safe_state = to_json_compatible(raw_state)
    backup_path.write_text(
        json.dumps(json_safe_state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    meta_dict = {
        "backup_file": filename,
        "created_at": created_at,
        "device_name": device_name,
        "model": model,
        "product_type": product_type,
        "udid": udid,
        "ios_version": ios_version,
        "build_version": build_version,
        "tool_version": __version__,
    }
    meta_path.write_text(
        json.dumps(meta_dict, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return BackupRecord(
        path=backup_path,
        meta_path=meta_path,
        filename=filename,
        created_at=created_at,
        device_name=device_name,
        model=model,
        product_type=product_type,
        udid=udid,
        ios_version=ios_version,
        build_version=build_version,
        tool_version=__version__,
    )


def _record_from_paths(backup_path: Path) -> BackupRecord:
    meta_path = backup_path.with_suffix(".meta.json")
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    stat_mtime = datetime.datetime.fromtimestamp(
        backup_path.stat().st_mtime, tz=datetime.timezone.utc
    ).astimezone()
    return BackupRecord(
        path=backup_path,
        meta_path=meta_path,
        filename=backup_path.name,
        created_at=str(meta.get("created_at") or stat_mtime.isoformat(timespec="seconds")),
        device_name=str(meta.get("device_name") or "Unknown"),
        model=str(meta.get("model") or "Unknown"),
        product_type=str(meta.get("product_type") or "Unknown"),
        udid=str(meta.get("udid") or "Unknown"),
        ios_version=str(meta.get("ios_version") or "Unknown"),
        build_version=str(meta.get("build_version") or "Unknown"),
        tool_version=str(meta.get("tool_version") or "Unknown"),
    )


def list_backups(backup_dir: Path = Path("backups")) -> list[BackupRecord]:
    """List all backups in `backup_dir`, sorted newest first."""
    if not backup_dir.exists():
        return []
    files = [
        p
        for p in backup_dir.glob("backup_*.json")
        if not p.name.endswith(".meta.json")
    ]
    files.sort(key=lambda p: (p.name, p.stat().st_mtime), reverse=True)
    return [_record_from_paths(p) for p in files]


def load_backup(
    backup_ref: str | Path,
    backup_dir: Path = Path("backups"),
) -> tuple[list[Any], BackupRecord]:
    """Load a raw backup state (restoring plist types) and its BackupRecord."""
    candidate = Path(backup_ref)
    if not candidate.exists():
        candidate = backup_dir / Path(backup_ref).name
    if not candidate.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_ref}")

    raw_json = json.loads(candidate.read_text(encoding="utf-8"))
    restored_state = from_json_compatible(raw_json)
    record = _record_from_paths(candidate)
    return restored_state, record


def save_opaque_store(
    opaque_store: dict[str, dict[str, Any]],
    opaque_dir: Path = Path("workspace/.opaque"),
) -> None:
    """Persist derived opaque element dicts to `workspace/.opaque/<token>.json`."""
    opaque_dir.mkdir(parents=True, exist_ok=True)
    for token, raw_dict in sorted(opaque_store.items()):
        target = opaque_dir / f"{token}.json"
        target.write_text(
            json.dumps(to_json_compatible(raw_dict), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def load_opaque_store(
    opaque_dir: Path = Path("workspace/.opaque"),
) -> dict[str, dict[str, Any]]:
    """Load all opaque element dicts from `workspace/.opaque/*.json`."""
    if not opaque_dir.exists():
        return {}
    store: dict[str, dict[str, Any]] = {}
    for p in sorted(opaque_dir.glob("*.json")):
        token = p.stem
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                store[token] = data
        except Exception:
            continue
    return store
