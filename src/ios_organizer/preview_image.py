"""Deterministic PNG previews from layout data. No browser, AI, or device access."""

from __future__ import annotations

from io import BytesIO
import hashlib
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ios_organizer.compiler import is_opaque_entry, parse_app_entry, parse_opaque_entry


def render_layout_png(layout: dict[str, Any], store: dict, *, spatial: bool = False,
                      title: str = 'Your Home Screen', columns: int = 4, rows: int = 6) -> bytes:
    if spatial:
        from ios_organizer.spatial import spatial_tasks
        spatial_tasks(layout)
        columns, rows = layout['grid']['columns'], layout['grid']['rows']
    if not 1 <= columns <= 12 or not 1 <= rows <= 12:
        raise ValueError('Grid dimensions must be from 1 to 12.')
    pages = layout.get('pages') or [{'items': []}]
    if len(pages) > 40:
        raise ValueError('PNG preview supports at most 40 pages; split a larger design.')
    cell, gap, padding = 72, 12, 20
    screen_w = padding * 2 + columns * cell + (columns - 1) * gap
    screen_h = padding * 2 + rows * cell + (rows - 1) * gap + 112
    per_row = min(3, len(pages))
    image = Image.new('RGB', (48 + per_row * (screen_w + 24), 150 + math.ceil(len(pages) / per_row) * (screen_h + 74)), '#f5f4ef')
    draw = ImageDraw.Draw(image)
    font = lambda size: ImageFont.load_default(size=size)
    draw.text((32, 24), title, fill='#22392f', font=font(30))
    draw.text((32, 68), 'LAYOUT PREVIEW  /  illustrative icons  /  no live widget content', fill='#506c5b', font=font(13))
    colors = ['#426f60', '#466c8a', '#805477', '#97622f', '#616c38', '#7e5852']

    def wrapped(text, x, y, max_width, *, size=12, lines=2, fill='#22392f'):
        words = str(text).split(); line = ''; output = []
        for word in words:
            candidate = f'{line} {word}'.strip()
            if draw.textlength(candidate, font=font(size)) > max_width and line:
                output.append(line); line = word
            else:
                line = candidate
        output.append(line)
        for index, line in enumerate(output[:lines]):
            while draw.textlength(line, font=font(size)) > max_width and len(line) > 1:
                line = line[:-2] + '…'
            draw.text((x, y + index * (size + 3)), line, fill=fill, font=font(size))

    def draw_item(item, x, y, width=1, height=1):
        box_w, box_h = width * cell + (width - 1) * gap, height * cell + (height - 1) * gap
        if isinstance(item, dict) and 'empty' in item:
            draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=15, outline='#a2b6a8', width=1)
            wrapped('Empty space', x + 10, y + 12, box_w - 20); return
        widget = isinstance(item, dict) and 'widget' in item
        if widget:
            data = item['widget']; name = parse_app_entry(data['app'])[0]
        elif isinstance(item, dict) and 'folder' in item:
            name = item['folder']
        else:
            entry = item['app'] if isinstance(item, dict) else item
            if is_opaque_entry(entry):
                ref = parse_opaque_entry(entry); name = ref.description or ref.kind
                widget = ref.kind == 'widget' and store.get(ref.token, {}).get('iconType') != 'app'
            else:
                name, _ = parse_app_entry(entry)
        if widget:
            draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=20, fill='#f4f8ef')
            wrapped(name, x + 14, y + 15, box_w - 28, size=18, lines=3)
            wrapped('Widget · preview', x + 14, y + box_h - 30, box_w - 28, size=11, lines=1)
        elif isinstance(item, dict) and 'folder' in item:
            draw.rounded_rectangle((x + 10, y, x + 62, y + 52), radius=13, fill='#eff4eb')
            for i in range(min(9, len(item.get('apps', [])))):
                a, b = x + 17 + i % 3 * 13, y + 7 + i // 3 * 13
                draw.rounded_rectangle((a, b, a + 8, b + 8), radius=2, fill='#658275')
            wrapped(name, x, y + 56, cell, size=11)
        else:
            color = colors[int(hashlib.sha256(str(name).encode()).hexdigest()[:4], 16) % len(colors)]
            draw.rounded_rectangle((x + 10, y, x + 62, y + 52), radius=13, fill=color)
            initials = ''.join(word[0] for word in name.split()[:2]).upper()
            draw.text((x + 21, y + 15), initials, fill='white', font=font(18))
            wrapped(name, x, y + 56, cell, size=11)

    for number, page in enumerate(pages):
        ox = 32 + number % per_row * (screen_w + 24)
        oy = 146 + number // per_row * (screen_h + 74)
        draw.text((ox + 4, oy - 30), f'Page {page.get("page", number + 1):02d}', fill='#22392f', font=font(18))
        draw.rounded_rectangle((ox, oy, ox + screen_w, oy + screen_h), radius=28, fill='#c7d8ce')
        occupied = set(); cursor = 0
        for item in page.get('items') or []:
            width, height = 1, 1
            if spatial:
                if 'widget' in item:
                    from ios_organizer.spatial import WIDGET_SPANS
                    width, height = WIDGET_SPANS[item['widget']['size']]
                else:
                    width, height = item.get('width', 1), item.get('height', 1)
                row, col = item['row'] - 1, item['column'] - 1
            else:
                if isinstance(item, dict) and not item.get('apps'):
                    continue
                if isinstance(item, str) and is_opaque_entry(item):
                    ref = parse_opaque_entry(item); raw = store.get(ref.token, {})
                    if ref.kind == 'widget' and raw.get('iconType') != 'app':
                        width, height = {'small': (2, 2), 'medium': (4, 2), 'large': (4, 4), 'extraLarge': (4, 6)}.get(raw.get('gridSize'), (2, 2))
                        width = min(width, columns)
                while True:
                    row, col = divmod(cursor, columns)
                    cells = {(r, c) for r in range(row, row + height) for c in range(col, col + width)}
                    if col + width <= columns and not cells & occupied:
                        occupied |= cells; cursor += width; break
                    cursor += 1
                if row + height > rows:
                    raise ValueError('Layout overflows the preview grid; validate capacity and widget packing.')
            draw_item(item, ox + padding + col * (cell + gap), oy + padding + row * (cell + gap), width, height)
        dock_y = oy + screen_h - 91
        draw.rounded_rectangle((ox + 14, dock_y, ox + screen_w - 14, dock_y + 76), radius=23, fill='#e8eee4')
        if spatial:
            draw.text((ox + 29, dock_y + 27), 'Dock unchanged', fill='#506c5b', font=font(14))
        else:
            dock = layout.get('dock') or []
            for index, item in enumerate(dock):
                dx = ox + (screen_w - len(dock) * (cell + 8)) // 2 + index * (cell + 8)
                draw_item(item, dx, dock_y + 5)
    output = BytesIO(); image.save(output, format='PNG')
    return output.getvalue()
