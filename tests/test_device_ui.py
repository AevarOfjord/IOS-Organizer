"""Exercise the real adapter against mocked CoreDevice services, without a phone."""

import asyncio
from contextlib import asynccontextmanager

import pytest

import ios_organizer.device as device
from ios_organizer.screen import ScreenAction


def test_developer_mode_failure_prevents_opening_input_tunnel(monkeypatch):
    class Lockdown:
        udid = 'TEST-PHONE'
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get_developer_mode_status(self): return False
    async def open_lockdown(**kwargs): return Lockdown()
    monkeypatch.setattr(device, '_open_lockdown', open_lockdown)
    async def connect():
        async with device._ui_connection('TEST-PHONE'):
            pytest.fail('Developer Mode disabled must block the connection')
    with pytest.raises(device.IOSDeviceError, match='Developer Mode is not enabled'):
        asyncio.run(connect())


def test_tunnel_pins_target_and_disables_new_pairing(monkeypatch):
    import pymobiledevice3.remote.userspace_tunnel as tunnel
    class Lockdown:
        udid = 'TEST-PHONE'
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get_developer_mode_status(self): return True
    async def open_lockdown(**kwargs): return Lockdown()
    events = []
    class WrongTunnel:
        udid = 'WRONG-PHONE'
        def __init__(self, **kwargs): events.append(kwargs)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): events.append('closed')
    monkeypatch.setattr(device, '_open_lockdown', open_lockdown)
    monkeypatch.setattr(tunnel, 'UserspaceRsdTunnel', WrongTunnel)
    async def connect():
        async with device._ui_connection('TEST-PHONE'):
            pytest.fail('Wrong device must block capture and input')
    with pytest.raises(device.IOSDeviceError, match='different device'):
        asyncio.run(connect())
    assert events == [{'serial': 'TEST-PHONE', 'autopair': False, 'remotepairing_fallback': False}, 'closed']


def test_rotated_screen_blocks_input_before_hid_session(monkeypatch):
    @asynccontextmanager
    async def connection(udid): yield object(), udid
    async def capture(*args): return {'width': 200, 'height': 100}
    monkeypatch.setattr(device, '_ui_connection', connection)
    monkeypatch.setattr(device, '_capture_ui', capture)
    with pytest.raises(device.IOSDeviceError, match='dimensions changed'):
        asyncio.run(device._async_ui_action('TEST', ScreenAction('tap', 1, 1), 100, 200))


def test_typing_releases_modifier_and_keys_after_failure(monkeypatch):
    import pymobiledevice3.remote.core_device.hid_service as service
    @asynccontextmanager
    async def connection(udid): yield object(), udid
    async def capture(*args): return {'width': 100, 'height': 200}
    events = []
    class Keyboard:
        async def create_keyboard_service(self): return 42
        async def send_keyboard(self, identifier, usages):
            events.append((identifier, usages))
            if len(usages) == 2: raise OSError('Disconnected while Shift was down')
    @asynccontextmanager
    async def touch_session(rsd): yield Keyboard()
    monkeypatch.setattr(device, '_ui_connection', connection)
    monkeypatch.setattr(device, '_capture_ui', capture)
    monkeypatch.setattr(service, 'touch_session', touch_session)
    with pytest.raises(OSError):
        asyncio.run(device._async_ui_action('TEST', ScreenAction('type', text='A'), 100, 200))
    assert events[-1] == (42, ())


def test_invalid_action_cannot_reach_connection(monkeypatch):
    async def fail(*args): pytest.fail('Invalid input reached native transport')
    monkeypatch.setattr(device, '_async_ui_action', fail)
    with pytest.raises(ValueError):
        device.perform_ui_action('TEST', ScreenAction('tap', -1, 1), 100, 200)
