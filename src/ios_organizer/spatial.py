"""Exact Home Screen design validation. Never compiles gaps into fake icons."""

from __future__ import annotations

from html import escape
from typing import Any

from ios_organizer.compiler import parse_app_entry, parse_opaque_entry
from ios_organizer.preview import _CSS


WIDGET_SPANS = {'small': (2, 2), 'medium': (4, 2), 'large': (4, 4)}


def validate_spatial_plan(plan: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict) or plan.get('spatial_version') != 1:
        return ['Expected spatial_version: 1. Spatial plans use the UI backend, not USB push.']
    if set(plan) - {'spatial_version', 'grid', 'pages', 'notes'}:
        errors.append('Unknown spatial plan fields. Bulk layouts and dock changes belong in a separate layout file.')
    grid = plan.get('grid')
    if not isinstance(grid, dict) or set(grid) != {'columns', 'rows'}:
        return errors + ['grid must contain columns and rows.']
    columns, rows = grid['columns'], grid['rows']
    if any(type(n) is not int or not 1 <= n <= 12 for n in (columns, rows)):
        return errors + ['Grid dimensions must be integers from 1 to 12.']
    pages = plan.get('pages')
    if not isinstance(pages, list) or not pages:
        return errors + ['pages must be a nonempty list.']
    page_numbers: set[int] = set()
    seen_apps: set[str] = set()
    seen_widgets: set[str] = set()
    for page in pages:
        if not isinstance(page, dict) or set(page) != {'page', 'items'}:
            errors.append('Each page must contain page and items.'); continue
        number = page['page']
        if type(number) is not int or number < 1 or number in page_numbers:
            errors.append('Page numbers must be unique positive integers.'); continue
        page_numbers.add(number)
        if not isinstance(page['items'], list):
            errors.append(f'Page {number}: items must be a list.'); continue
        occupied: set[tuple[int, int]] = set()
        for item in page['items']:
            if not isinstance(item, dict):
                errors.append(f'Page {number}: each item needs explicit row and column.'); continue
            kinds = set(item) & {'app', 'widget', 'empty'}
            if len(kinds) != 1 or set(item) - {'app', 'widget', 'empty', 'row', 'column', 'width', 'height'}:
                errors.append(f'Page {number}: specify exactly one of app, widget, empty.'); continue
            width, height = item.get('width', 1), item.get('height', 1)
            if 'widget' in item:
                widget = item['widget']
                if not isinstance(widget, dict) or set(widget) - {'id', 'app', 'size', 'configuration', 'reference'}:
                    errors.append(f'Page {number}: invalid widget request.'); continue
                if not all(isinstance(widget.get(key), str) and widget[key].strip() for key in ('id', 'app', 'size')):
                    errors.append(f'Page {number}: widgets require id, app, and size.'); continue
                if widget['id'] in seen_widgets:
                    errors.append(f"Duplicate widget request id: {widget['id']}")
                seen_widgets.add(widget['id'])
                if 'reference' in widget:
                    try:
                        if parse_opaque_entry(widget['reference']).kind != 'widget':
                            raise ValueError('Expected widget reference')
                    except (ValueError, TypeError, AttributeError):
                        errors.append('Existing widgets require a valid @widget reference.')
                if widget['size'] not in WIDGET_SPANS:
                    errors.append(f'Page {number}: widget size must be small, medium, or large.'); continue
                width, height = WIDGET_SPANS[widget['size']]
                if 'width' in item or 'height' in item:
                    errors.append('Widget spans are determined by size; omit width and height.')
                if 'configuration' in widget and not isinstance(widget['configuration'], dict):
                    errors.append('Widget configuration must be a mapping for the agent to configure through the UI.')
            elif 'app' in item:
                try:
                    _, bid = parse_app_entry(item['app'])
                    if bid in seen_apps:
                        errors.append(f'Duplicate app in spatial plan: {bid}')
                    seen_apps.add(bid)
                except (TypeError, ValueError, AttributeError):
                    errors.append(f'Page {number}: invalid app entry.')
                if width != 1 or height != 1:
                    errors.append('An app occupies exactly one cell.')
            elif item['empty'] is not True:
                errors.append('An empty region must use empty: true.')
            row, column = item.get('row'), item.get('column')
            if any(type(v) is not int or v < 1 for v in (row, column, width, height)):
                errors.append(f'Page {number}: row, column, width, height must be positive integers.'); continue
            if row + height - 1 > rows or column + width - 1 > columns:
                errors.append(f'Page {number}: item extends beyond the grid.'); continue
            cells = {(r, c) for r in range(row, row + height) for c in range(column, column + width)}
            if occupied & cells:
                errors.append(f'Page {number}: overlapping app, widget, or reserved empty region.')
            occupied |= cells
    return errors


def spatial_tasks(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Agent work items, not a blind coordinate script or a USB payload."""
    errors = validate_spatial_plan(plan)
    if errors:
        raise ValueError('; '.join(errors))
    tasks = []
    for page in plan['pages']:
        for item in page['items']:
            operation = 'keep_empty' if 'empty' in item else 'place_app'
            if 'widget' in item:
                operation = 'place_widget' if 'reference' in item['widget'] else 'ensure_widget'
            tasks.append({'page': page['page'], **item,
                          'operation': operation,
                          'verification': 'fresh screenshot after leaving Home Screen edit mode'})
    return tasks


def render_spatial_preview(plan: dict[str, Any]) -> str:
    spatial_tasks(plan)  # Validate before rendering.
    columns, rows = plan['grid']['columns'], plan['grid']['rows']
    sections = []
    for page in plan['pages']:
        tiles = []
        for item in page['items']:
            width, height = item.get('width', 1), item.get('height', 1)
            if 'widget' in item:
                widget = item['widget']; width, height = WIDGET_SPANS[widget['size']]
                name = f"{widget['app']} · {widget['size']}"
                content = f'<strong>{escape(name)}</strong><small>Native widget request · sample content</small>'
                cls = 'widget'
            elif 'empty' in item:
                content = '<small>Empty space</small>'; cls = 'spatial-empty'
            else:
                name, _ = parse_app_entry(item['app'])
                content = f'<span class="icon" aria-hidden="true">{escape(name[:2])}</span><span class="label">{escape(name)}</span>'
                cls = 'tile'
            style = f"grid-column:{item['column']} / span {width};grid-row:{item['row']} / span {height}"
            tiles.append(f'<div class="{cls}" style="{style}">{content}</div>')
        sections.append(f'<section class="page"><h2>Page {page["page"]:02d}</h2><div class="screen"><div class="grid" style="--columns:{columns};--rows:{rows}">{"".join(tiles)}</div></div></section>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            f'<title>Spatial Home Screen plan</title><style>{_CSS}.spatial-empty {{ display:grid; place-items:center; color:#5c675e; border:1px dashed #869b90; border-radius:12px; }}</style></head><body><main>'
            '<h1>Exact placement plan</h1><p>Preview only. Empty cells stay empty; widgets require the native gallery. '
            'Apply through the UI workflow after approval. USB push cannot apply this plan. Dock and unlisted apps are outside this plan.</p>'
            f'<div class="pages">{"".join(sections)}</div></main></body></html>')
