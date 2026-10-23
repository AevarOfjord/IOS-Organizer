"""Failure paths run against a fake device; never connect to a phone."""

import copy
import json

import pytest
from typer.testing import CliRunner

import ios_organizer.cli as cli
from ios_organizer.backup import load_backup
from ios_organizer.device import DeviceInfo, IOSDeviceError


@pytest.fixture
def device(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initial = [[], [{"displayIdentifier": "com.example.a"}]]
    apps = {"com.example.a": {"name": "Alpha"}, "com.example.b": {"name": "Beta"}}
    info = DeviceInfo(name="Test iPhone", model="Test", product_type="Test", udid="TEST-DEVICE",
                      ios_version="test", build_version="test")
    writes = []
    monkeypatch.setattr(cli, "fetch_device_snapshot", lambda udid=None: (info, copy.deepcopy(initial), apps))
    monkeypatch.setattr(cli, "get_raw_icon_state", lambda udid=None: copy.deepcopy(initial))
    monkeypatch.setattr(cli, "set_raw_icon_state", lambda raw, udid=None: writes.append((raw, udid)))
    layout = tmp_path / "workspace" / "layout.yaml"
    layout.parent.mkdir()
    layout.write_text("schema_version: 1\ndock: []\npages:\n  - page: 1\n    items: [com.example.b]\n")
    return tmp_path, initial, writes


def test_push_validation_failure_does_not_write(device):
    root, initial, writes = device
    (root / "workspace/layout.yaml").write_text("schema_version: 99\n")
    result = CliRunner().invoke(cli.app, ["push", "--yes"])
    assert result.exit_code == 1
    assert not writes
    backups = list((root / "backups").glob("backup_*.json"))
    backup = next(path for path in backups if not path.name.endswith(".meta.json"))
    assert load_backup(backup)[0] == initial


def test_push_failure_keeps_recovery_backup(device, monkeypatch):
    root, initial, _ = device
    def fail(*args, **kwargs):
        raise IOSDeviceError("Disconnected during write")
    monkeypatch.setattr(cli, "set_raw_icon_state", fail)
    result = CliRunner().invoke(cli.app, ["push", "--yes"])
    assert result.exit_code == 1
    assert "Push Failed" in result.output
    assert "backup_" in result.output
    backup = next(path for path in (root / "backups").glob("*.json") if not path.name.endswith(".meta.json"))
    assert load_backup(backup)[0] == initial


def test_push_verification_mismatch_returns_nonzero_and_recovery(device):
    _, _, writes = device
    result = CliRunner().invoke(cli.app, ["push", "--yes"])
    assert result.exit_code == 2
    assert "verification mismatch" in result.output
    assert "Recover with:" in result.output
    assert "Verification passed" not in result.output
    assert writes[0][1] == "TEST-DEVICE"


def test_verification_read_failure_explains_write_was_sent(device, monkeypatch):
    def fail(udid=None):
        raise IOSDeviceError("Disconnected during verification")
    monkeypatch.setattr(cli, "get_raw_icon_state", fail)
    result = CliRunner().invoke(cli.app, ["push", "--yes"])
    assert result.exit_code == 1
    assert "was sent" in result.output
    assert "Recover with:" in result.output


def test_dry_run_warns_about_app_library_and_does_not_write(device):
    _, _, writes = device
    result = CliRunner().invoke(cli.app, ["push", "--dry-run"])
    assert result.exit_code == 0
    assert "App Library warning" in result.output
    assert "cannot be preserved" in result.output
    assert not writes


def test_restore_mismatch_is_not_success(device):
    root, _, writes = device
    backup = root / "restore.json"
    backup.write_text(json.dumps([[], [{"displayIdentifier": "com.example.b"}]]))
    result = CliRunner().invoke(cli.app, ["restore", str(backup), "--yes"])
    assert result.exit_code == 2
    assert "Restore verification mismatch" in result.output
    assert "Recover with:" in result.output
    assert writes[0][1] == "TEST-DEVICE"


def test_restore_write_failure_exposes_recovery_backup(device, monkeypatch):
    root, _, _ = device
    backup = root / "restore.json"
    backup.write_text("[[], []]")
    def fail(*args, **kwargs):
        raise IOSDeviceError("Disconnected")
    monkeypatch.setattr(cli, "set_raw_icon_state", fail)
    result = CliRunner().invoke(cli.app, ["restore", str(backup), "--yes"])
    assert result.exit_code == 1
    assert "Recover with:" in result.output


def test_duplicate_widget_is_blocked_even_with_force(device):
    root, _, writes = device
    (root / "workspace/layout.yaml").write_text(
        "schema_version: 1\npages:\n  - items: ['@widget:a:One', '@widget:a:Two']\n"
    )
    opaque = root / "workspace/.opaque"
    opaque.mkdir()
    (opaque / "a.json").write_text(json.dumps({"iconType": "custom", "displayIdentifier": "widget-uuid"}))
    result = CliRunner().invoke(cli.app, ["push", "--force", "--yes"])
    assert result.exit_code == 1
    assert "Duplicate displayIdentifier" in result.output
    assert not writes


def test_cancelling_push_does_not_write(device):
    _, _, writes = device
    result = CliRunner().invoke(cli.app, ["push"], input="n\n")
    assert result.exit_code == 0
    assert "cancelled" in result.output
    assert not writes


def test_backup_failure_prevents_device_write(device, monkeypatch):
    _, _, writes = device
    def fail(*args, **kwargs):
        raise OSError("Disk full")
    monkeypatch.setattr(cli, "write_backup", fail)
    result = CliRunner().invoke(cli.app, ["push", "--yes"])
    assert result.exit_code != 0
    assert not writes


def test_successful_push_verifies_same_device_as_snapshot(device, monkeypatch):
    _, _, writes = device
    read_udids = []
    def read(udid=None):
        read_udids.append(udid)
        return copy.deepcopy(writes[-1][0])
    monkeypatch.setattr(cli, "get_raw_icon_state", read)
    result = CliRunner().invoke(cli.app, ["push", "--yes"])
    assert result.exit_code == 0
    assert "Verification passed" in result.output
    assert "100%" not in result.output
    assert read_udids == ["TEST-DEVICE"]


def test_force_cannot_compact_a_spatial_design_into_usb_state(device):
    root, _, writes = device
    (root / 'workspace/layout.yaml').write_text(
        'spatial_version: 1\ngrid: {columns: 4, rows: 6}\npages: []\n')
    result = CliRunner().invoke(cli.app, ['push', '--force', '--yes'])
    assert result.exit_code == 1
    assert 'Spatial designs cannot' in result.output
    assert not writes
