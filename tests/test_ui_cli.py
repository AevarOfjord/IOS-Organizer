"""Screen workflow tests use a fake phone only; never send native input."""

import base64
from dataclasses import asdict
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ios_organizer.ui_cli as ui
from ios_organizer.cli import app
from ios_organizer.device import DeviceInfo, IOSDeviceError


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN4sAAAAASUVORK5CYII=')
RUNNER = CliRunner()


@pytest.fixture
def phone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    capture = {'udid': 'TEST-PHONE', 'image': PNG, 'width': 1, 'height': 1}
    info = DeviceInfo('Test phone', 'Test', 'Test', 'TEST-PHONE', 'test', 'test')
    events = []
    monkeypatch.setattr(ui, 'observe_ui', lambda udid: dict(capture))
    def snapshot(udid=None):
        events.append(('backup-read', udid))
        return info, [[], []], {}
    def send(udid, action, width, height):
        assert list(Path('backups').glob('backup_*.json'))
        events.append(('input', udid, asdict(action)))
        return dict(capture)
    monkeypatch.setattr(ui, 'fetch_device_snapshot', snapshot)
    monkeypatch.setattr(ui, 'perform_ui_action', send)
    path = ui._save_frame(capture)
    return path, capture, events


def tap(path, *args, yes=True):
    return RUNNER.invoke(app, ['ui', 'action', 'tap', '--frame', str(path), '--x', '0', '--y', '0',
                               '--backend', 'coredevice',
                               *(['--yes'] if yes else []), *args], input='n\n' if not yes else None)


def test_observation_sends_no_input_and_saves_image(phone):
    _, _, events = phone
    result = RUNNER.invoke(app, ['ui', 'observe', '--udid', 'TEST-PHONE', '--backend', 'coredevice'])
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert Path(output['screenshot']).read_bytes() == PNG
    assert events == [] and output['outcome_verified'] is False


def test_action_backs_up_then_pins_device_and_returns_unverified_result_frame(phone):
    path, _, events = phone
    result = tap(path)
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert [e[0] for e in events] == ['backup-read', 'input']
    assert events[1][1] == 'TEST-PHONE'
    assert output['status'] == 'input_sent_visual_verification_required'
    assert output['outcome_verified'] is False
    assert Path(output['frame']).exists() and path.with_suffix('.used').exists()
    result = tap(path)
    assert result.exit_code == 1 and 'already consumed' in result.output
    assert len(events) == 2


@pytest.mark.parametrize('bad', ['stale', 'wrong-device', 'tampered', 'outside'])
def test_bad_frame_or_action_is_blocked_before_device_access(phone, bad):
    path, _, events = phone
    if bad == 'stale':
        frame = json.loads(path.read_text()); frame['created_at'] -= 121
        path.write_text(json.dumps(frame))
    elif bad == 'tampered':
        (path.parent / 'screen.png').write_bytes(b'changed')
    args = ['--udid', 'OTHER-PHONE'] if bad == 'wrong-device' else ['--x', '2'] if bad == 'outside' else []
    result = tap(path, *args)
    assert result.exit_code == 1, result.output
    assert not events and not path.with_suffix('.used').exists()


def test_cancel_keeps_frame_unused_and_sends_no_input(phone):
    path, _, events = phone
    result = tap(path, yes=False)
    assert result.exit_code == 0 and 'cancelled' in result.output
    assert not events and not path.with_suffix('.used').exists()


def test_backup_disk_failure_blocks_input(phone, monkeypatch):
    path, _, events = phone
    def fail(*a, **kw): raise OSError('Disk full')
    monkeypatch.setattr(ui, 'write_backup', fail)
    result = tap(path)
    assert result.exit_code == 1 and 'Disk full' in result.output
    assert all(event[0] != 'input' for event in events)
    assert not path.with_suffix('.used').exists()


def test_disconnect_is_uncertain_and_frame_cannot_be_replayed(phone, monkeypatch):
    path, _, events = phone
    calls = []
    def disconnect(*a, **kw):
        calls.append(a)
        raise IOSDeviceError('Disconnect after contact')
    monkeypatch.setattr(ui, 'perform_ui_action', disconnect)
    result = tap(path)
    assert result.exit_code == 2 and 'do not replay' in result.output
    assert json.loads((path.parent / 'action.json').read_text())['status'] == 'uncertain_observe_before_retry'
    assert tap(path).exit_code == 1 and len(calls) == 1


def test_second_frame_cannot_act_during_a_device_operation(phone):
    path, _, events = phone
    with ui._device_lock('TEST-PHONE'):
        result = tap(path)
    assert result.exit_code == 1 and 'already running' in result.output
    assert not events


def test_changed_result_capture_is_uncertain(phone, monkeypatch):
    path, capture, _ = phone
    monkeypatch.setattr(ui, 'perform_ui_action', lambda *a: {**capture, 'udid': 'OTHER-PHONE'})
    result = tap(path)
    assert result.exit_code == 2
    assert path.with_suffix('.used').exists()


def test_doctor_distinguishes_unprobed_features(phone, monkeypatch):
    monkeypatch.setattr(ui, 'get_ui_capabilities', lambda udid: {'developer_mode': False, 'touch_input': 'not_probed'})
    result = RUNNER.invoke(app, ['ui', 'doctor', '--udid', 'TEST-PHONE', '--backend', 'coredevice'])
    assert result.exit_code == 0
    assert json.loads(result.output)['touch_input'] == 'not_probed'


def test_default_doctor_does_not_probe_developer_mode_or_phone(phone, monkeypatch):
    def forbidden(*args): pytest.fail('Default workflow must not use developer services')
    monkeypatch.setattr(ui, 'get_ui_capabilities', forbidden)
    result = RUNNER.invoke(app, ['ui', 'doctor'])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data['developer_mode_required'] is False and data['backend'] == 'mirroring'


def test_default_actions_never_fall_back_to_developer_transport(phone, monkeypatch):
    path, _, events = phone
    def forbidden(*args): pytest.fail('Developer transport must be explicit opt-in')
    monkeypatch.setattr(ui, 'observe_ui', forbidden)
    monkeypatch.setattr(ui, 'perform_ui_action', forbidden)
    result = RUNNER.invoke(app, ['ui', 'observe', '--udid', 'TEST-PHONE'])
    assert result.exit_code == 1 and 'does not require Developer Mode' in result.output
    result = RUNNER.invoke(app, ['ui', 'action', 'tap', '--frame', str(path), '--x', '0', '--y', '0', '--yes'])
    assert result.exit_code == 1 and 'approved computer-use tools' in result.output
    assert not events and not path.with_suffix('.used').exists()
