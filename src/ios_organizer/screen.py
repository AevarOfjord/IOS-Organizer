"""Screen geometry and input semantics, independent of device transport."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import struct
from typing import Any


def png_size(image: bytes) -> tuple[int, int]:
    if len(image) < 33 or image[:8] != b'\x89PNG\r\n\x1a\n' or image[12:16] != b'IHDR':
        raise ValueError('Device did not return a PNG screenshot.')
    width, height = struct.unpack('>II', image[16:24])
    if not 1 <= width <= 16384 or not 1 <= height <= 16384:
        raise ValueError('Screenshot dimensions are invalid.')
    return width, height


def touch_point(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    if type(x) is not int or type(y) is not int or not 0 <= x < width or not 0 <= y < height:
        raise ValueError('Coordinates must be pixels inside the referenced screenshot.')
    return round(x * 65535 / max(width - 1, 1)), round(y * 65535 / max(height - 1, 1))


@dataclass(frozen=True)
class ScreenAction:
    kind: str
    x: int | None = None
    y: int | None = None
    to_x: int | None = None
    to_y: int | None = None
    hold: float = 0.8
    duration: float = 0.6
    text: str | None = None

    def validate(self, width: int, height: int) -> None:
        if self.kind not in {'tap', 'hold', 'drag', 'type', 'home'}:
            raise ValueError('Action must be tap, hold, drag, type, or home.')
        if not all(math.isfinite(v) and 0 <= v <= 5 for v in (self.hold, self.duration)):
            raise ValueError('Hold and duration must be finite values between 0 and 5 seconds.')
        if self.kind in {'tap', 'hold', 'drag'}:
            touch_point(self.x, self.y, width, height)
        elif self.x is not None or self.y is not None:
            raise ValueError('Coordinates are only valid for touch actions.')
        if self.kind == 'drag':
            touch_point(self.to_x, self.to_y, width, height)
        elif self.to_x is not None or self.to_y is not None:
            raise ValueError('Destination coordinates are only valid for a drag.')
        if self.kind == 'type':
            if not isinstance(self.text, str) or not 1 <= len(self.text) <= 200:
                raise ValueError('Typing requires 1–200 characters.')
            if any(not (' ' <= c <= '~' or c in '\n\t') for c in self.text):
                raise ValueError('Native keyboard currently supports printable ASCII, newline, and tab only.')
        elif self.text is not None:
            raise ValueError('Text is only valid for a type action.')


async def send_touch(hid: Any, action: ScreenAction, width: int, height: int) -> None:
    """Always lift a contact, including cancellation or a failed intermediate report."""
    action.validate(width, height)
    start = touch_point(action.x, action.y, width, height)
    point = start
    try:
        await hid.send_touchscreen(0xC2, *start)
        await asyncio.sleep(0.05 if action.kind == 'tap' else action.hold)
        if action.kind == 'drag':
            end = touch_point(action.to_x, action.to_y, width, height)
            steps = max(1, round(action.duration * 60))
            for i in range(1, steps + 1):
                point = tuple(round(a + (b - a) * i / steps) for a, b in zip(start, end))
                await hid.send_touchscreen(0xC2, *point)
                await asyncio.sleep(action.duration / steps)
    finally:
        await hid.send_touchscreen(0x02, *point)
