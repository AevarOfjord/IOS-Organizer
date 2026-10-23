"""Deterministic phone-style previews, drawn locally from actual layout data.

Symbols are illustrations, not fetched app artwork. Widget content is explicitly
sample content; neither the renderer nor its inputs grant device access.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ios_organizer.compiler import is_opaque_entry, parse_app_entry, parse_opaque_entry


@dataclass(frozen=True)
class PhoneGrid:
    columns: int = 4
    rows: int = 6
    cell: int = 76
    gap: int = 14
    padding: int = 24
    top: int = 74

    @property
    def width(self):
        return self.padding * 2 + self.columns * self.cell + (self.columns - 1) * self.gap

    @property
    def height(self):
        return self.top + self.rows * (self.cell + self.gap) - self.gap + 152

    def rectangle(self, row, column, width=1, height=1):
        return (self.padding + column * (self.cell + self.gap),
                self.top + row * (self.cell + self.gap),
                width * self.cell + (width - 1) * self.gap,
                height * self.cell + (height - 1) * self.gap)


class Painter:
    def __init__(self, image, scale):
        self.image, self.scale = image, scale
        self.draw = ImageDraw.Draw(image)
        self._fonts = {}

    def box(self, box):
        return tuple(round(v * self.scale) for v in box)

    def font(self, size):
        pixels = round(size * self.scale)
        if pixels not in self._fonts:
            self._fonts[pixels] = ImageFont.load_default(size=pixels)
        return self._fonts[pixels]

    def text(self, xy, value, size=12, fill='#f3f5fc', anchor=None):
        self.draw.text(self.box(xy), str(value), font=self.font(size), fill=fill, anchor=anchor)

    def text_width(self, text, size):
        return self.draw.textlength(str(text), font=self.font(size)) / self.scale

    def rounded(self, box, radius=12, fill=None, outline=None, width=1):
        self.draw.rounded_rectangle(self.box(box), radius=round(radius * self.scale),
                                   fill=fill, outline=outline, width=max(1, round(width * self.scale)))

    def ellipse(self, box, fill=None, outline=None, width=1):
        self.draw.ellipse(self.box(box), fill=fill, outline=outline, width=max(1, round(width * self.scale)))

    def line(self, points, fill='#ffffff', width=1.6):
        self.draw.line([self.box(p) for p in points], fill=fill, width=max(1, round(width * self.scale)), joint='curve')

    def polygon(self, points, fill):
        self.draw.polygon([self.box(p) for p in points], fill=fill)

    def arc(self, box, start, end, fill, width=1.6):
        self.draw.arc(self.box(box), start, end, fill=fill, width=max(1, round(width * self.scale)))

    def gradient(self, box, top, bottom, radius=12):
        x1, y1, x2, y2 = self.box(box)
        width, height = x2 - x1, y2 - y1
        patch = Image.new('RGB', (width, height))
        draw = ImageDraw.Draw(patch)
        first, last = tuple(bytes.fromhex(top.lstrip('#'))), tuple(bytes.fromhex(bottom.lstrip('#')))
        for y in range(height):
            ratio = y / max(1, height - 1)
            draw.line((0, y, width, y), fill=tuple(round(a + (b - a) * ratio) for a, b in zip(first, last)))
        mask = Image.new('L', (width, height))
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=round(radius * self.scale), fill=255)
        self.image.paste(patch, (x1, y1), mask)

    def label(self, x, y, name, width=76, size=11):
        text = ' '.join(str(name).split())
        while len(text) > 1 and self.text_width(text, size) > width:
            text = text[:-2] + '…'
        self.text((x + width / 2, y), text, size, anchor='mt')


def _symbol(p, kind, x, y, size, color='#ffffff'):
    """Original line illustrations in a normalized 32-unit square."""
    unit = size / 32
    def pt(a, b):
        return x + a * unit, y + b * unit
    def box(a, b, c, d):
        return (*pt(a, b), *pt(c, d))
    def line(points, width=1.7):
        p.line([pt(*point) for point in points], color, width * unit)
    def circle(a, b, c, d, filled=False):
        p.ellipse(box(a, b, c, d), fill=color if filled else None, outline=None if filled else color, width=1.7 * unit)
    if kind in {'mail', 'gmail'}:
        p.rounded(box(3, 7, 29, 26), radius=2 * unit, outline=color, width=1.8 * unit)
        line([(3, 9), (16, 18), (29, 9)])
    elif kind == 'maps':
        line([(4, 6), (12, 3), (21, 7), (28, 4), (28, 26), (21, 29), (12, 25), (4, 28), (4, 6)])
        line([(12, 3), (12, 25)]); line([(21, 7), (21, 29)])
    elif kind in {'messages', 'messenger'}:
        p.arc(box(3, 3, 29, 28), 135, 450, color, 1.8 * unit)
        line([(6, 23), (4, 30), (13, 26)])
    elif kind == 'phone':
        line([(7, 3), (3, 5), (4, 13), (10, 23), (19, 29), (27, 29), (29, 23), (21, 20), (18, 24), (13, 20), (8, 13), (12, 10), (10, 3), (7, 3)], 2)
    elif kind == 'safari':
        circle(3, 3, 29, 29)
        line([(11, 22), (14, 13), (23, 9), (19, 19), (11, 22)])
        line([(14, 13), (19, 19)])
    elif kind == 'podcasts':
        circle(4, 2, 28, 26); circle(9, 7, 23, 21); circle(13, 11, 19, 17, True)
        p.rounded(box(13, 20, 19, 32), radius=3 * unit, fill=color)
    elif kind == 'spotify':
        for i, height in enumerate((8, 16, 24, 13, 20, 9)):
            xx = 5 + i * 4.4
            line([(xx, 16 - height / 2), (xx, 16 + height / 2)], 2)
    elif kind == 'facebook':
        circle(7, 3, 17, 13)
        p.arc(box(18, 3, 28, 14), 270, 90, color, 1.8 * unit)
        p.rounded(box(3, 17, 22, 30), radius=5 * unit, outline=color, width=1.8 * unit)
        p.arc(box(21, 17, 30, 31), 270, 90, color, 1.8 * unit)
    elif kind in {'instagram', 'camera'}:
        p.rounded(box(3, 4, 29, 29), radius=6 * unit, outline=color, width=1.8 * unit)
        circle(10, 11, 22, 23); circle(23, 8, 25, 10, True)
    elif kind == 'snapchat':
        p.arc(box(6, 2, 26, 26), 180, 360, color, 1.8 * unit)
        line([(6, 13), (6, 27), (10, 24), (13, 28), (17, 25), (21, 28), (26, 25), (26, 13)])
        circle(11, 12, 13, 14, True); circle(19, 12, 21, 14, True)
    elif kind == 'facetime':
        p.rounded(box(2, 7, 21, 26), radius=4 * unit, outline=color, width=1.8 * unit)
        line([(23, 12), (30, 7), (30, 26), (23, 21), (23, 12)])
    elif kind == 'music':
        line([(12, 25), (12, 6), (26, 3), (26, 22)])
        line([(12, 10), (26, 7)])
        p.ellipse(box(3, 22, 13, 29), fill=color); p.ellipse(box(17, 19, 27, 26), fill=color)
    elif kind in {'notes', 'reminders'}:
        for row in (8, 16, 24):
            if kind == 'reminders':
                circle(2, row - 2, 6, row + 2)
            line([(10 if kind == 'reminders' else 4, row), (29, row)])
    elif kind == 'books':
        line([(2, 5), (10, 4), (16, 8), (22, 4), (30, 5), (30, 27), (23, 26), (16, 30), (9, 26), (2, 27), (2, 5)])
        line([(16, 8), (16, 30)])
    elif kind == 'photos':
        for index, fill in enumerate(('#f5c83d', '#f79b3b', '#ee6476', '#be69d6', '#719fe8', '#66c7cd', '#83c26b', '#c6ce50')):
            angle = math.pi * index / 4
            cx, cy = 16 + 8 * math.cos(angle), 16 + 8 * math.sin(angle)
            p.ellipse(box(cx - 5, cy - 5, cx + 5, cy + 5), fill=fill)
    elif kind == 'calendar':
        p.text(pt(16, 15), '22', size=20 * unit, fill=color, anchor='mm')
    elif kind == 'weather':
        circle(14, 1, 25, 12)
        for a in range(0, 360, 60):
            rad = math.radians(a)
            line([(19.5 + 8 * math.cos(rad), 6.5 + 8 * math.sin(rad)),
                  (19.5 + 11 * math.cos(rad), 6.5 + 11 * math.sin(rad))], 1.2)
        p.rounded(box(2, 15, 24, 26), radius=5 * unit, outline=color, width=1.6 * unit)
    else:
        return False
    return True


def _app_style(name, bid):
    key = name.casefold().removeprefix('example ').strip()
    known = {'phone': ('#32bf59', '#10913b', 'phone', '#ffffff'),
             'messages': ('#32c45b', '#0f9945', 'messages', '#ffffff'),
             'safari': ('#2d79ee', '#1858c8', 'safari', '#ffffff'),
             'gmail': ('#edf2f8', '#e5edf5', 'gmail', '#d5392c'),
             'mail': ('#edf2f8', '#e5edf5', 'mail', '#d5392c'),
             'maps': ('#d5edda', '#cce4d3', 'maps', '#1766d0'),
             'podcasts': ('#923bc3', '#7427a7', 'podcasts', '#ffffff'),
             'spotify': ('#092018', '#071910', 'spotify', '#20d071'),
             'facebook': ('#256de3', '#1b56c0', 'facebook', '#ffffff'),
             'messenger': ('#3881ed', '#7133d0', 'messenger', '#ffffff'),
             'instagram': ('#a72db0', '#e79b32', 'instagram', '#ffffff'),
             'snapchat': ('#f6e444', '#e6d32b', 'snapchat', '#172432'),
             'camera': ('#e7ebee', '#b8c1cb', 'camera', '#26303b'),
             'facetime': ('#46d276', '#22ad4c', 'facetime', '#ffffff'),
             'calendar': ('#ffffff', '#e9eef5', 'calendar', '#273346'),
             'music': ('#f95c76', '#db274f', 'music', '#ffffff'),
             'notes': ('#fff6bc', '#ece8ce', 'notes', '#9a8741'),
             'reminders': ('#f6f7fb', '#dfe7f2', 'reminders', '#4185db'),
             'photos': ('#f9fafc', '#e5eaf1', 'photos', '#ffffff'),
             'books': ('#ffab47', '#ef701a', 'books', '#ffffff')}
    ids = {'com.google.gmail': 'gmail', 'com.google.maps': 'maps', 'com.apple.maps': 'maps',
           'com.spotify.client': 'spotify', 'com.facebook.facebook': 'facebook',
           'com.facebook.messenger': 'messenger', 'com.burbn.instagram': 'instagram',
           'com.toyopagroup.picaboo': 'snapchat', 'com.apple.mobilephone': 'phone',
           'com.apple.mobilesms': 'messages', 'com.apple.mobilesafari': 'safari',
           'com.apple.mobilecal': 'calendar', 'com.apple.mobilenotes': 'notes',
           'com.apple.mobileslideshow': 'photos', 'com.apple.mobilemail': 'mail',
           'com.apple.ibooks': 'books'}
    key = ids.get(bid.casefold(), key)
    if key in known:
        return known[key]
    colors = ['#416dd7', '#9b4fae', '#df8340', '#2a9b89', '#6175c1', '#b75b81']
    color = colors[int(hashlib.sha256(bid.encode()).hexdigest()[:4], 16) % len(colors)]
    return color, color, None, '#ffffff'


def _draw_app(p, entry, x, y, *, label=True, icon_size=60):
    if is_opaque_entry(entry):
        ref = parse_opaque_entry(entry)
        name, bid = ref.description or ref.kind, ref.token
    else:
        name, bid = parse_app_entry(entry)
    top, bottom, kind, ink = _app_style(name, bid)
    left = x + (76 - icon_size) / 2
    p.gradient((left, y, left + icon_size, y + icon_size), top, bottom, 16)
    if not kind or not _symbol(p, kind, left + icon_size * .3, y + icon_size * .3, icon_size * .4, ink):
        initials = ''.join(word[0] for word in name.split()[:2]).upper() or '?'
        p.text((left + icon_size / 2, y + icon_size / 2), initials, 20, anchor='mm')
    if label:
        p.label(x, y + icon_size + 5, name)


def _draw_widget(p, name, x, y, width, height, *, stack=False):
    key = name.casefold()
    weather = 'weather' in key and not stack
    p.gradient((x, y, x + width, y + height), '#174573' if weather else '#232e3d',
               '#285e8c' if weather else '#222c3a', 26)
    if weather:
        p.text((x + 16, y + 17), 'Weather', 15)
        p.line([(x + 18, y + 80), (x + 53, y + 80)], '#f3f5fc', 3)
        p.text((x + 55, y + 45), '°', 44)
        p.text((x + 16, y + height - 62), 'Your location', 12)
        p.text((x + 16, y + height - 33), 'Sample content', 11, '#d7e2f0')
        _symbol(p, 'weather', x + width - 35, y + height - 67, 19, '#ffda75')
    elif 'calendar' in key and not stack:
        p.text((x + 16, y + 20), 'THURSDAY', 12, '#ff7b79')
        p.text((x + 16, y + 43), '22', 44)
        p.line([(x + 17, y + height - 67), (x + 17, y + height - 31)], '#aaa0ff', 3)
        p.text((x + 28, y + height - 67), 'Your next event', 12)
        p.text((x + 28, y + height - 48), 'Calendar preview', 10, '#b8c2d2')
    elif 'reminders' in key and not stack:
        p.text((x + 16, y + 17), 'Reminders', 15, '#ffb54c')
        _symbol(p, 'reminders', x + width - 33, y + 18, 16, '#ffb54c')
        for index, text in enumerate(('Your first reminder', 'Your next reminder', 'Another reminder')):
            yy = y + 58 + index * 31
            if yy + 16 > y + height - 12:
                break
            p.ellipse((x + 16, yy - 1, x + 31, yy + 14), outline='#b5bfce', width=.7)
            p.text((x + 42, yy - 1), text, 12)
            if index < 2:
                p.line([(x + 16, yy + 23), (x + width - 16, yy + 23)], '#46505e', .5)
    else:
        p.label(x + 14, y + 24, name, width - 28, 14)
        p.text((x + 16, y + height - 33), 'Existing stack' if stack else 'Widget preview', 11, '#bcc7d7')
        if stack:
            for i in range(3):
                p.ellipse((x + width / 2 - 10 + i * 8, y + height - 15, x + width / 2 - 7 + i * 8, y + height - 12), fill='#a0acbf')


def _placements(page, store, grid, spatial):
    occupied, cursor = set(), 0
    for item in page.get('items') or []:
        width, height = 1, 1
        if spatial:
            if 'widget' in item:
                from ios_organizer.spatial import WIDGET_SPANS
                width, height = WIDGET_SPANS[item['widget']['size']]
            else:
                width, height = item.get('width', 1), item.get('height', 1)
            row, column = item['row'] - 1, item['column'] - 1
        else:
            if isinstance(item, dict) and not item.get('apps'):
                continue
            if isinstance(item, str) and is_opaque_entry(item):
                ref = parse_opaque_entry(item)
                raw = store.get(ref.token, {})
                if ref.kind == 'widget' and raw.get('iconType') != 'app':
                    width, height = {'small': (2, 2), 'medium': (4, 2), 'large': (4, 4), 'extraLarge': (4, 6)}.get(raw.get('gridSize'), (2, 2))
                    width = min(width, grid.columns)
            while True:
                row, column = divmod(cursor, grid.columns)
                cells = {(r, c) for r in range(row, row + height) for c in range(column, column + width)}
                if column + width <= grid.columns and not cells & occupied:
                    occupied |= cells
                    cursor += width
                    break
                cursor += 1
            if row + height > grid.rows:
                raise ValueError('Layout overflows the preview grid; validate capacity and widget packing.')
        yield item, grid.rectangle(row, column, width, height)


def render_layout_png(layout: dict[str, Any], store: dict, *, spatial: bool = False,
                      title: str = 'Your Home Screen', columns: int = 4, rows: int = 6,
                      dock: list | None = None) -> bytes:
    """Render exact spatial cells or approximate bulk packing with sample widgets."""
    if spatial:
        from ios_organizer.spatial import spatial_tasks
        spatial_tasks(layout)
        columns, rows = layout['grid']['columns'], layout['grid']['rows']
    if not 1 <= columns <= 12 or not 1 <= rows <= 12:
        raise ValueError('Grid dimensions must be from 1 to 12.')
    pages = layout.get('pages') or [{'items': []}]
    if len(pages) > 40:
        raise ValueError('PNG preview supports at most 40 pages; split a larger design.')
    grid = PhoneGrid(columns, rows)
    per_row = min(2, len(pages))
    width = 64 + per_row * grid.width + (per_row - 1) * 30
    height = 154 + math.ceil(len(pages) / per_row) * (grid.height + 56) + 36
    if width * height > 20_000_000:
        raise ValueError('PNG preview is too large; split this design into fewer pages.')
    scale = 2 if width * height <= 5_000_000 else 1
    image = Image.new('RGB', (width * scale, height * scale), '#151719')
    p = Painter(image, scale)
    for yy in range(12, height, 18):
        for xx in range(12, width, 18):
            p.ellipse((xx, yy, xx + 1, yy + 1), fill='#303438')
    p.text((32, 24), title, 26)
    p.text((32, 64), 'LAYOUT PREVIEW · ILLUSTRATIVE ICONS · SAMPLE WIDGET CONTENT', 10, '#aeb8c7')
    for number, page in enumerate(pages):
        ox = 32 + number % per_row * (grid.width + 30)
        oy = 154 + number // per_row * (grid.height + 56)
        p.text((ox + 4, oy - 30), f'Page {page.get("page", number + 1):02d}', 15, '#d4dce9')
        p.rounded((ox - 3, oy + 4, ox + grid.width + 3, oy + grid.height + 7), 50, '#080b10')
        p.rounded((ox, oy, ox + grid.width, oy + grid.height), 49, '#82909e')
        p.gradient((ox + 6, oy + 6, ox + grid.width - 6, oy + grid.height - 6), '#2c4357', '#2c2c45', 44)
        p.text((ox + 30, oy + 27), '9:41', 15)
        p.rounded((ox + grid.width / 2 - 56, oy + 20, ox + grid.width / 2 + 56, oy + 50), 16, '#050b10')
        status_x = ox + grid.width - 83
        for i in range(4):
            p.line([(status_x + i * 4, oy + 37), (status_x + i * 4, oy + 33 - i * 3)], '#eef3fb', 1.3)
        p.arc((status_x + 20, oy + 26, status_x + 36, oy + 40), 210, 330, '#eef3fb', 1.3)
        p.arc((status_x + 24, oy + 30, status_x + 32, oy + 40), 215, 325, '#eef3fb', 1.3)
        p.rounded((status_x + 43, oy + 28, status_x + 56, oy + 36), 1.5, outline='#eef3fb', width=1)
        p.line([(status_x + 46, oy + 30), (status_x + 52, oy + 30)], '#eef3fb', 2)
        for item, (x, y, item_w, item_h) in _placements(page, store, grid, spatial):
            x, y = x + ox, y + oy
            if isinstance(item, dict) and 'empty' in item:
                continue
            if isinstance(item, dict) and 'widget' in item:
                _draw_widget(p, parse_app_entry(item['widget']['app'])[0], x, y, item_w, item_h)
            elif isinstance(item, dict) and 'folder' in item:
                p.rounded((x + 8, y, x + 68, y + 60), 16, '#718097')
                for index, entry in enumerate(item.get('apps', [])[:9]):
                    if not isinstance(entry, str):
                        continue
                    name, bid = (parse_opaque_entry(entry).description, entry) if is_opaque_entry(entry) else parse_app_entry(entry)
                    top, bottom, _, _ = _app_style(name, bid)
                    left, top_y = x + 15 + index % 3 * 15, y + 9 + index // 3 * 15
                    p.gradient((left, top_y, left + 11, top_y + 11), top, bottom, 3)
                p.label(x, y + 65, item['folder'])
            else:
                entry = item['app'] if isinstance(item, dict) else item
                if is_opaque_entry(entry):
                    ref = parse_opaque_entry(entry)
                    raw = store.get(ref.token, {})
                    if ref.kind == 'widget' and raw.get('iconType') != 'app':
                        _draw_widget(p, ref.description or 'Widget', x, y, item_w, item_h, stack=len(raw.get('elements') or []) > 1)
                        continue
                _draw_app(p, entry, x, y)
        pager_y = oy + grid.height - 132
        visible_dots = min(7, len(pages))
        for index in range(visible_dots):
            cx = ox + grid.width / 2 + (index - (visible_dots - 1) / 2) * 12
            p.ellipse((cx - 2.5, pager_y - 2.5, cx + 2.5, pager_y + 2.5), fill='#e2e8f3' if index == number % visible_dots else '#737d91')
        dock_y = oy + grid.height - 114
        p.rounded((ox + 24, dock_y, ox + grid.width - 24, dock_y + 86), 30, '#3b485e')
        entries = dock if spatial else layout.get('dock') or []
        if entries:
            span = (grid.width - 70) / len(entries)
            for index, entry in enumerate(entries):
                _draw_app(p, entry, ox + 35 + span * index + (span - 76) / 2, dock_y + 12, label=False)
        else:
            p.text((ox + grid.width / 2, dock_y + 42), 'Dock unchanged' if spatial else 'Empty dock', 12, '#c0cadb', anchor='mm')
        p.rounded((ox + grid.width / 2 - 54, oy + grid.height - 15, ox + grid.width / 2 + 54, oy + grid.height - 11), 3, '#f5f6fb')
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()
