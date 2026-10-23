"""Self-contained, offline HTML previews. No device access or remote assets."""

from __future__ import annotations

import hashlib
from html import escape
from typing import Any

from ios_organizer.compiler import folder_icon_lists, is_opaque_entry, parse_app_entry, parse_opaque_entry


_CSS = """
:root { color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  color: #22392f; background: #f5f4ef; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-inline-size: 1200px; margin-inline: auto; padding: 40px 24px 64px; }
header { display: flex; justify-content: space-between; align-items: start; gap: 20px; flex-wrap: wrap; }
.eyebrow { font-size: 11px; letter-spacing: .16em; font-weight: 700; text-transform: uppercase; color: #506c5b; }
h1 { font-size: clamp(2rem, 1.5rem + 2vw, 3.2rem); letter-spacing: -.045em; margin: 10px 0 12px; }
h2 { font-size: 20px; letter-spacing: -.02em; }
p { line-height: 1.6; }
.subtitle { color: #5c675e; margin: 0; max-inline-size: 720px; font-size: 14px; }
.badge { border: 1px solid #c3d0c6; border-radius: 99px; padding: 8px 12px; font-size: 12px; white-space: nowrap; }
nav { display: flex; gap: 8px; flex-wrap: wrap; margin-block: 24px; }
nav a { background: white; border: 1px solid #d8dfd7; border-radius: 8px; padding: 9px 14px; font-size: 13px; }
a { color: inherit; text-underline-offset: 3px; }
a:focus-visible, summary:focus-visible { outline: 3px solid #2767b2; outline-offset: 4px; }
.notice { background: #fff5dc; border: 1px solid #dac48b; padding: 14px 18px; border-radius: 12px;
  color: #624b19; font-size: 13px; margin-block: 20px; }
.notice p { margin: 6px 0; }
.pages { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 330px), 1fr)); gap: 24px; }
.page { min-inline-size: 0; scroll-margin-block-start: 20px; }
.page-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.page-heading span { color: #627165; font-size: 12px; }
.screen { border-radius: 24px; border: 1px solid #c9d3c7; padding: 20px 14px 14px;
  background: linear-gradient(160deg, #dde7d7, #c7d8ce 60%, #b9c9bc); }
.grid { display: grid; grid-template-columns: repeat(var(--columns), minmax(0, 1fr));
  grid-auto-rows: 68px; grid-template-rows: repeat(var(--rows), 68px); gap: 12px 8px;
  align-content: start; margin-block-end: 16px; }
.tile { min-inline-size: 0; display: flex; flex-direction: column; align-items: center; gap: 5px;
  text-align: center; text-decoration: none; font-size: 10px; line-height: 1.2; }
.icon { display: grid; place-items: center; inline-size: 43px; aspect-ratio: 1; border-radius: 12px;
  background: var(--icon-color, #496e5b); color: #fff; font-size: 17px; font-weight: 650; }
.label { overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical;
  -webkit-line-clamp: 2; line-clamp: 2; overflow: clip; }
.folder-icon { display: grid; grid-template-columns: repeat(3, 1fr); gap: 3px; padding: 5px; background: #f3f4ead9; }
.mini { inline-size: 8px; block-size: 8px; border-radius: 2px; background: #658275; }
.widget { background: #f4f8efe8; border: 1px solid #f7fff5; border-radius: 16px; padding: 14px;
  display: flex; flex-direction: column; justify-content: space-between; gap: 8px; min-inline-size: 0;
  overflow-wrap: anywhere; color: #2b4d3c; }
.widget strong { font-size: 14px; line-height: 1.35; }
.widget small { font-size: 10px; color: #4e6858; }
.dock { display: flex; justify-content: space-evenly; flex-wrap: wrap; gap: 10px;
  min-block-size: 79px; padding: 10px; border-radius: 18px; background: #f8fbf27d; }
.dock .tile { flex: 0 1 52px; }
.empty { color: #4e6556; font-size: 13px; padding: 20px 0; }
.folders { margin-block-start: 32px; }
details { scroll-margin-block-start: 20px; border: 1px solid #d6ddd2; border-radius: 12px;
  padding: 14px 18px; background: #fffefa; margin-block: 10px; }
summary { cursor: pointer; font-size: 14px; font-weight: 600; }
.folder-page { border-block-start: 1px solid #e4e8de; margin-block-start: 14px; padding-block-start: 4px; }
.folder-page h3 { font-size: 12px; color: #5b705f; }
.app-list { list-style: none; padding: 0; display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr)); gap: 12px; }
.app-list li { min-inline-size: 0; font-size: 13px; overflow-wrap: anywhere; }
.app-list small { display: block; margin-block-start: 3px; color: #697569; font-size: 11px; }
footer { margin-block-start: 36px; color: #687668; font-size: 12px; line-height: 1.7; }
@media (max-width: 500px) { main { padding: 24px 14px 40px; } .pages { gap: 20px; } }
@media print { body { background: white; } nav { display: none; } .page { break-inside: avoid; } }
"""


def render_layout_preview(
    layout: dict[str, Any], opaque_store: dict[str, dict[str, Any]], *,
    title: str = "Home Screen", columns: int = 4, rows: int = 6,
    warnings: list[str] | None = None,
) -> str:
    """Render a validated layout; positioning is an approximation of SpringBoard."""
    if not 1 <= columns <= 12 or not 1 <= rows <= 12:
        raise ValueError("Preview columns and rows must be between 1 and 12.")
    folders: list[str] = []

    def app_label(entry: str) -> tuple[str, str]:
        if is_opaque_entry(entry):
            ref = parse_opaque_entry(entry)
            return ref.description or ref.kind.title(), f"{ref.kind} · {ref.token}"
        return parse_app_entry(entry)

    def app_list(items: list[str]) -> str:
        return '<ul class="app-list">' + "".join(
            f"<li>{escape(name)}<small>{escape(identifier)}</small></li>"
            for name, identifier in map(app_label, items)
        ) + "</ul>"

    def tile(entry: Any, location: str) -> str:
        if isinstance(entry, dict):
            name = str(entry["folder"])
            anchor = f"folder-{location}"
            apps = entry.get("apps") or []
            pages = folder_icon_lists(apps, entry.get("page_sizes"))
            contents = "".join(
                f'<section class="folder-page"><h3>Folder page {index} · {len(page)} items</h3>'
                + (app_list(page) if page else '<p class="empty">Empty folder page</p>') + "</section>"
                for index, page in enumerate(pages, 1)
            )
            folders.append(
                f'<details id="{anchor}"><summary>{escape(name)} · {len(apps)} items · '
                f'{len(pages)} pages</summary>{contents}</details>'
            )
            mini = '<span class="mini"></span>' * min(len(apps), 9)
            return (f'<a class="tile" href="#{anchor}" title="View {escape(name, quote=True)} folder contents">'
                    f'<span class="icon folder-icon" aria-hidden="true">{mini}</span>'
                    f'<span class="label">{escape(name)}</span></a>')
        name, identifier = app_label(entry)
        if is_opaque_entry(entry):
            ref = parse_opaque_entry(entry)
            raw = opaque_store.get(ref.token, {})
            if ref.kind == "widget" and raw.get("iconType") != "app":
                size = raw.get("gridSize")
                width, height = {"small": (2, 2), "medium": (4, 2), "large": (4, 4), "extraLarge": (4, 6)}.get(size, (2, 2))
                certainty = str(size) if size in ("small", "medium", "large", "extraLarge") else "Unknown size · estimated"
                return (f'<div class="widget" style="grid-column:span {min(width, columns)};grid-row:span {height}">'
                        f'<small>WIDGET / BOOKMARK</small><strong>{escape(name)}</strong>'
                        f'<small>{escape(certainty)}</small></div>')
        colors = ["#426f60", "#466c8a", "#805477", "#97622f", "#616c38", "#7e5852"]
        color = colors[int(hashlib.sha256(identifier.encode()).hexdigest()[:4], 16) % len(colors)]
        initials = "".join(word[0] for word in name.split()[:2]).upper() or "?"
        return (f'<div class="tile" title="{escape(name + " · " + identifier, quote=True)}">'
                f'<span class="icon" style="--icon-color:{color}" aria-hidden="true">{escape(initials)}</span>'
                f'<span class="label">{escape(name)}</span></div>')

    pages = layout.get("pages") or []
    dock = layout.get("dock") or []
    sections = []
    for index, page in enumerate(pages, 1):
        items = [entry for entry in (page.get("items") or [])
                 if not (isinstance(entry, dict) and not entry.get("apps"))]
        tiles = "".join(tile(entry, f"{index}-{position}") for position, entry in enumerate(items))
        dock_tiles = "".join(tile(entry, f"dock-{position}") for position, entry in enumerate(dock))
        sections.append(
            f'<section class="page" id="page-{index}" aria-label="Page {index}">'
            f'<div class="page-heading"><h2>Page {index:02d}</h2><span>{len(items)} items</span></div>'
            f'<div class="screen"><div class="grid" style="--columns:{columns};--rows:{rows}">'
            + (tiles or '<p class="empty">Empty page · preserved</p>')
            + f'</div><div class="dock" aria-label="Dock">{dock_tiles or "<span class=empty>Empty dock</span>"}</div></div></section>'
        )
    unassigned = layout.get("unassigned_apps") or []
    if not pages and dock:
        dock_tiles = "".join(tile(entry, f"dock-{position}") for position, entry in enumerate(dock))
        sections.append(f'<section class="page"><h2>Dock</h2><div class="dock" aria-label="Dock">{dock_tiles}</div></section>')
    library = ""
    if unassigned:
        library = (f'<aside class="notice"><strong>App Library warning · {len(unassigned)} listed apps omitted</strong>'
                   '<p>On the tested iOS version, pushing places omitted apps into available Home Screen slots. '
                   'App Library-only placement cannot be preserved.</p></aside>'
                   f'<details><summary>Apps listed as unassigned ({len(unassigned)})</summary>{app_list(unassigned)}</details>')
    validation = ""
    if warnings:
        validation = '<aside class="notice"><strong>Layout warnings</strong><ul>' + "".join(
            f"<li>{escape(warning)}</li>" for warning in warnings
        ) + "</ul></aside>"
    navigation = "".join(f'<a href="#page-{index}">Page {index:02d}</a>' for index in range(1, len(pages) + 1))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        f'<title>{escape(title)} · iOS Organizer</title><style>{_CSS}</style></head><body><main>'
        '<header><div><div class="eyebrow">iOS Organizer / Layout review</div><h1>Your Home Screen, mapped.</h1>'
        f'<p class="subtitle">{escape(title)} · {len(pages)} pages · {len(folders)} folders · {columns} × {rows} grid. '
        'Review page order, open folder contents, and check widgets before pushing.</p></div>'
        '<span class="badge">Local preview · no device changes</span></header>'
        f'<nav aria-label="Home Screen pages">{navigation}</nav>{library}{validation}'
        '<p class="subtitle">Positions are approximate. Icons use initials; widget content, hidden/Focus status, '
        'and iOS placement adjustments are not available in this preview.</p>'
        f'<div class="pages">{"".join(sections) or "<p>No Home Screen pages.</p>"}</div>'
        f'<section class="folders"><h2>Inside your folders</h2>{"".join(folders) or "<p>No folders in this layout.</p>"}</section>'
        '<footer>Generated entirely from local layout files. Device push verification checks page order, folder '
        'pagination, and opaque objects; transient app metadata is ignored.</footer></main></body></html>'
    )
