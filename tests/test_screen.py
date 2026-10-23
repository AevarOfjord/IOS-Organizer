"""Input validation, normalized coordinates, pickup timing, and failure cleanup."""

import asyncio

import pytest

from ios_organizer.screen import ScreenAction, png_size, send_touch, touch_point


def test_coordinate_mapping_uses_actual_screenshot_edges():
    assert touch_point(0, 0, 1179, 2556) == (0, 0)
    assert touch_point(1178, 2555, 1179, 2556) == (65535, 65535)
    with pytest.raises(ValueError):
        touch_point(1179, 0, 1179, 2556)


@pytest.mark.parametrize('screen_action', [ScreenAction('tap', -1, 5), ScreenAction('drag', 1, 1),
    ScreenAction('hold', 1, 1, hold=float('nan')), ScreenAction('type', text='café'),
    ScreenAction('home', 1, 1), ScreenAction('tap', 1, 1, text='oops')])
def test_invalid_inputs_fail_before_transport(screen_action):
    with pytest.raises(ValueError):
        screen_action.validate(100, 200)


def test_drag_holds_before_moving_and_releases_on_failure(monkeypatch):
    events = []
    async def sleep(seconds): events.append(('sleep', seconds))
    monkeypatch.setattr('ios_organizer.screen.asyncio.sleep', sleep)
    class HID:
        async def send_touchscreen(self, state, x, y):
            events.append((state, x, y))
            if state == 0xC2 and x > 0:
                raise OSError('Disconnected mid-drag')
    with pytest.raises(OSError):
        asyncio.run(send_touch(HID(), ScreenAction('drag', 0, 0, 99, 199, hold=0.8, duration=0.1), 100, 200))
    assert events[:2] == [(0xC2, 0, 0), ('sleep', 0.8)]
    assert events[-1][0] == 0x02


def test_png_rejects_nonimage_and_corrupt_dimensions():
    with pytest.raises(ValueError): png_size(b'not a png')
