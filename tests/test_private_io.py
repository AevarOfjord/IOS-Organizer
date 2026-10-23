"""Permission regressions use only fictional data and temporary directories."""

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from ios_organizer.backup import load_backup, save_opaque_store, write_backup
from ios_organizer.private_io import open_private, secure_private_tree, write_private


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@contextmanager
def umask(value):
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.mark.parametrize("mask", [0o000, 0o022])
def test_backup_and_opaque_are_private_at_permissive_umask(tmp_path, mask):
    with umask(mask):
        rec = write_backup([[], []], "Example phone", "TEST-PHONE", "test", backup_dir=tmp_path / "nested/backups")
        save_opaque_store({"abc": {"iconType": "custom"}}, tmp_path / "objects")
    assert load_backup(rec.path)[0] == [[], []]
    assert mode(rec.path) == mode(rec.meta_path) == 0o600
    assert mode(rec.path.parent) == mode(tmp_path / "nested") == 0o700
    assert mode(tmp_path / "objects/abc.json") == 0o600


def test_rewrite_tightens_file_without_changing_shared_ancestor(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    target = shared / "inventory.md"
    target.write_text("old")
    target.chmod(0o644)
    write_private(target, "new")
    assert target.read_text() == "new"
    assert mode(target) == 0o600
    assert mode(shared) == 0o755


def test_migration_secures_untouched_history_without_changing_bytes(tmp_path):
    root = tmp_path / "workspace"
    old = root / "old-frame/screen.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"fictional screenshot bytes")
    root.chmod(0o755)
    old.parent.chmod(0o755)
    old.chmod(0o644)
    secure_private_tree(root)
    assert old.read_bytes() == b"fictional screenshot bytes"
    assert mode(root) == mode(old.parent) == 0o700
    assert mode(old) == 0o600


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_unsafe_alias_is_rejected_without_changing_target(tmp_path, alias):
    original = tmp_path / "original"
    original.write_text("keep")
    original.chmod(0o644)
    target = tmp_path / "alias"
    if alias == "symlink":
        target.symlink_to(original)
    else:
        target.hardlink_to(original)
    with pytest.raises(OSError):
        write_private(target, "overwrite")
    assert original.read_text() == "keep"
    assert mode(original) == 0o644


def test_private_tree_never_follows_symlinked_descendants(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "keep.txt"
    source.write_text("keep")
    source.chmod(0o644)
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError, match="Unsafe entry"):
        secure_private_tree(root)
    assert mode(source) == 0o644
    assert source.read_text() == "keep"


def test_foreign_owner_is_rejected_before_chmod_or_truncation(tmp_path, monkeypatch):
    target = tmp_path / "foreign"
    target.write_text("keep")
    target.chmod(0o644)
    monkeypatch.setattr(os, "geteuid", lambda: target.stat().st_uid + 1)
    with pytest.raises(PermissionError, match="another account"):
        write_private(target, "overwrite")
    assert target.read_text() == "keep"
    assert mode(target) == 0o644


def test_exclusive_creation_remains_exclusive(tmp_path):
    path = tmp_path / "frame.used"
    with open_private(path, exclusive=True) as stream:
        stream.write("reserved")
    with pytest.raises(FileExistsError):
        open_private(path, exclusive=True)
    assert path.read_text() == "reserved"
    assert mode(path) == 0o600


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS extended ACL regression")
def test_macos_inherited_and_existing_acl_grants_are_removed(tmp_path):
    def acl_entries(path):
        listing = subprocess.check_output(["/bin/ls", "-lde", str(path)], text=True)
        return [line.strip() for line in listing.splitlines()[1:]]
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    entry = "group:everyone allow read,execute,file_inherit,directory_inherit"
    subprocess.run(["/bin/chmod", "+a", entry, str(shared)], check=True)
    before = acl_entries(shared)
    assert before and " allow " in before[0]
    target = shared / "new-inventory.md"
    write_private(target, "fictional app list")
    assert mode(target) == 0o600
    assert not acl_entries(target)
    assert acl_entries(shared) == before
    old = shared / "workspace"
    old.mkdir()
    file = old / "old.md"
    file.write_text("keep")
    assert acl_entries(file)
    secure_private_tree(old)
    assert not acl_entries(old)
    assert not acl_entries(file)
    assert file.read_text() == "keep"


def test_storage_migration_failure_blocks_all_phone_access(tmp_path, monkeypatch):
    import ios_organizer.cli as cli
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "external"
    outside.mkdir()
    Path("workspace").symlink_to(outside, target_is_directory=True)
    def forbidden(*args, **kwargs):
        pytest.fail("Unsafe storage must be blocked before phone access")
    monkeypatch.setattr(cli, "fetch_device_snapshot", forbidden)
    monkeypatch.setattr(cli, "set_raw_icon_state", forbidden)
    result = CliRunner().invoke(cli.app, ["push", "--yes"])
    assert result.exit_code == 1
    assert "Private storage error" in result.output


def test_pull_preserves_edited_baseline_but_repairs_its_permissions(tmp_path, monkeypatch):
    import ios_organizer.cli as cli
    from ios_organizer.device import DeviceInfo
    monkeypatch.chdir(tmp_path)
    workspace = Path("workspace")
    workspace.mkdir()
    workspace.chmod(0o755)
    baseline = workspace / "layout.yaml"
    baseline.write_text("my edited baseline\n")
    baseline.chmod(0o644)
    info = DeviceInfo("Example", "Test", "Test", "TEST-PHONE", "test", "test")
    monkeypatch.setattr(cli, "fetch_device_snapshot", lambda **kwargs: (info, [[], []], {}))
    result = CliRunner().invoke(cli.app, ["pull"])
    assert result.exit_code == 0, result.output
    assert baseline.read_text() == "my edited baseline\n"
    assert mode(workspace) == 0o700
    assert mode(baseline) == mode(workspace / "layout.new.yaml") == 0o600


@pytest.mark.parametrize("command,extension", [("preview", "html"), ("preview", "png"), ("inventory", "md")])
def test_cli_custom_output_is_private_without_chmod_of_its_shared_parent(tmp_path, monkeypatch, command, extension):
    from ios_organizer.cli import app
    monkeypatch.chdir(tmp_path)
    source = Path("layout.yaml")
    source.write_text("schema_version: 1\ndock: []\npages: []\n")
    shared = Path("shared")
    shared.mkdir()
    shared.chmod(0o755)
    output = shared / f"preview.{extension}"
    with umask(0o022):
        result = CliRunner().invoke(app, [command, "--layout", str(source), "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert mode(output) == 0o600
    assert mode(shared) == 0o755
