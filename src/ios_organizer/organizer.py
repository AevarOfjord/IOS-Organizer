"""Auto-sort and category grouping helpers for ios-organizer.

Pure functions only: no filesystem I/O and no pymobiledevice3 imports.
"""

from __future__ import annotations

import copy
from typing import Any

import yaml

from ios_organizer.compiler import (
    SCHEMA_VERSION,
    estimate_item_slots,
    format_app_entry,
    is_opaque_entry,
    parse_app_entry,
)


def _app_sort_key(
    entry: str,
    installed_apps: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Return (casefolded_display_name, bundle_id) for deterministic alphabetical sorting."""
    try:
        name, bid = parse_app_entry(entry)
    except ValueError:
        return (entry.casefold(), entry)
    if installed_apps and bid in installed_apps and name == bid:
        name = str(installed_apps[bid].get("name") or bid)
    return (name.casefold(), bid)


def _canonicalize_app_entry(
    entry: str,
    installed_apps: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Normalize an app entry string to 'Display Name (bundle.id)'."""
    name, bid = parse_app_entry(entry)
    if installed_apps and bid in installed_apps and name == bid:
        name = str(installed_apps[bid].get("name") or bid)
    return format_app_entry(name, bid)


def sort_folder_items(
    apps: list[Any],
    installed_apps: dict[str, dict[str, Any]] | None = None,
) -> list[Any]:
    """Sort apps within a folder alphabetically while keeping any opaque tokens at the start."""
    opaque_items: list[Any] = []
    regular_apps: list[str] = []
    for item in apps:
        if isinstance(item, str) and not is_opaque_entry(item):
            regular_apps.append(_canonicalize_app_entry(item, installed_apps))
        else:
            opaque_items.append(copy.deepcopy(item))
    regular_apps.sort(key=lambda s: _app_sort_key(s, installed_apps))
    return [*opaque_items, *regular_apps]


def resolve_app_category(
    bundle_id: str,
    categories: dict[str, str] | None = None,
    installed_apps: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Resolve category for `bundle_id` in priority order: categories.yaml -> device genre -> 'Other'."""
    if categories:
        cat = categories.get(bundle_id)
        if isinstance(cat, str) and cat.strip():
            return cat.strip()
    if installed_apps and bundle_id in installed_apps:
        genre = installed_apps[bundle_id].get("genre")
        if isinstance(genre, str) and genre.strip():
            return genre.strip()
    return "Other"


def auto_sort_layout(
    layout_data: dict[str, Any],
    mode: str = "category",
    categories: dict[str, str] | None = None,
    installed_apps: dict[str, dict[str, Any]] | None = None,
    opaque_store: dict[str, dict[str, Any]] | None = None,
    include_unassigned: bool = False,
    reorganize_folders: bool = False,
    page_capacity: int = 24,
) -> dict[str, Any]:
    """Produce a deterministically sorted layout proposal dictionary.

    Args:
        layout_data: Parsed layout.yaml dictionary.
        mode: 'category' or 'alpha'.
        categories: Optional bundle_id -> category mapping from categories.yaml.
        installed_apps: Optional installed app metadata from device.
        opaque_store: Optional opaque token dictionary for widget slot calculation.
        include_unassigned: When True, also pull `unassigned_apps` onto Home Screen pages.
        reorganize_folders: When True, dissolve existing folders and re-sort their apps.
        page_capacity: Max grid slots per Home Screen page (default 24).
    """
    if mode not in ("category", "alpha"):
        raise ValueError(f"Unsupported auto-sort mode: {mode!r} (expected 'category' or 'alpha')")
    if page_capacity <= 0:
        raise ValueError("page_capacity must be positive")

    dock = copy.deepcopy(layout_data.get("dock") or [])
    raw_pages = layout_data.get("pages") or []
    raw_unassigned = layout_data.get("unassigned_apps") or []

    pinned_by_page: dict[int, list[Any]] = {}
    loose_apps: list[str] = []

    for p_idx, page_obj in enumerate(raw_pages):
        pinned_by_page.setdefault(p_idx, [])
        items = page_obj.get("items") or [] if isinstance(page_obj, dict) else []
        for entry in items:
            if isinstance(entry, str) and is_opaque_entry(entry):
                pinned_by_page[p_idx].append(entry)
            elif isinstance(entry, dict) and "folder" in entry:
                fapps = entry.get("apps") or []
                if reorganize_folders:
                    for sub in fapps:
                        if isinstance(sub, str) and not is_opaque_entry(sub):
                            loose_apps.append(_canonicalize_app_entry(sub, installed_apps))
                        else:
                            pinned_by_page[p_idx].append(copy.deepcopy(sub))
                else:
                    folder_copy = copy.deepcopy(entry)
                    folder_copy["apps"] = sort_folder_items(fapps, installed_apps)
                    if folder_copy["apps"]:
                        pinned_by_page[p_idx].append(folder_copy)
            elif isinstance(entry, str):
                loose_apps.append(_canonicalize_app_entry(entry, installed_apps))

    if include_unassigned:
        for entry in raw_unassigned:
            if isinstance(entry, str) and not is_opaque_entry(entry):
                loose_apps.append(_canonicalize_app_entry(entry, installed_apps))
        remaining_unassigned: list[str] = []
    else:
        remaining_unassigned = [
            _canonicalize_app_entry(entry, installed_apps)
            for entry in raw_unassigned
            if isinstance(entry, str) and not is_opaque_entry(entry)
        ]
        remaining_unassigned.sort(key=lambda s: _app_sort_key(s, installed_apps))

    # Build movable items list according to mode
    movable_items: list[Any] = []
    if mode == "alpha":
        loose_apps.sort(key=lambda s: _app_sort_key(s, installed_apps))
        movable_items = list(loose_apps)
    else:
        grouped: dict[str, list[str]] = {}
        for app_entry in loose_apps:
            _, bid = parse_app_entry(app_entry)
            cat = resolve_app_category(bid, categories=categories, installed_apps=installed_apps)
            grouped.setdefault(cat, []).append(app_entry)

        sorted_categories = sorted(
            grouped.keys(),
            key=lambda c: (c == "Other", c.casefold(), c),
        )
        for cat in sorted_categories:
            apps_in_cat = sorted(grouped[cat], key=lambda s: _app_sort_key(s, installed_apps))
            movable_items.append({"folder": cat, "apps": apps_in_cat})

    # Initialize page buckets with pinned items (widgets, webclips, and kept folders)
    num_initial_pages = max(pinned_by_page.keys(), default=-1) + 1
    page_buckets: list[list[Any]] = []
    page_slots: list[int] = []

    for p_idx in range(num_initial_pages):
        pinned_items = pinned_by_page.get(p_idx, [])
        if not pinned_items:
            page_buckets.append([])
            page_slots.append(0)
            continue
        for p_item in pinned_items:
            slots, _ = estimate_item_slots(p_item, opaque_store)
            # Place on page p_idx if it fits or if page p_idx is currently empty
            while len(page_buckets) <= p_idx:
                page_buckets.append([])
                page_slots.append(0)
            if page_slots[p_idx] == 0 or page_slots[p_idx] + slots <= page_capacity:
                page_buckets[p_idx].append(p_item)
                page_slots[p_idx] += slots
            else:
                # Overflow to a new page bucket
                page_buckets.append([p_item])
                page_slots.append(slots)

    # Pack movable_items into the first page bucket with available capacity
    for item in movable_items:
        slots, _ = estimate_item_slots(item, opaque_store)
        placed = False
        for idx in range(len(page_buckets)):
            if page_slots[idx] + slots <= page_capacity:
                page_buckets[idx].append(item)
                page_slots[idx] += slots
                placed = True
                break
        if not placed:
            page_buckets.append([item])
            page_slots.append(slots)

    non_empty_pages = [bucket for bucket in page_buckets if bucket]
    pages_out = [
        {"page": idx, "items": bucket}
        for idx, bucket in enumerate(non_empty_pages, start=1)
    ]

    return {
        "schema_version": layout_data.get("schema_version", SCHEMA_VERSION),
        "dock": dock,
        "pages": pages_out,
        "unassigned_apps": remaining_unassigned,
    }


def dump_categories_yaml(
    installed_apps: dict[str, dict[str, Any]],
    existing_categories: dict[str, str] | None = None,
) -> str:
    """Render a starter `categories.yaml` listing all installed apps."""
    lines: list[str] = [
        "# ios-organizer category mapping (bundle_id -> Folder Category Name)",
        "# Fill in empty strings (\"\") with your desired folder names for `auto-sort --mode category`.",
        "# Apps left blank will fall back to device-reported genre (if any) or 'Other'.",
        "",
        "categories:",
    ]
    existing = existing_categories or {}

    sorted_items = sorted(
        installed_apps.items(),
        key=lambda kv: (str(kv[1].get("name") or kv[0]).casefold(), kv[0]),
    )
    for bundle_id, info in sorted_items:
        name = str(info.get("name") or bundle_id).replace("\n", " ")
        app_type = str(info.get("type") or "User")
        genre = info.get("genre")
        assigned = existing.get(bundle_id, "")
        escaped_id = bundle_id.replace('"', '\\"')
        escaped_val = str(assigned or "").replace('"', '\\"')
        genre_hint = f", genre: {genre}" if genre else ""
        lines.append(
            f'  "{escaped_id}": "{escaped_val}"  # {name} [{app_type}{genre_hint}]'
        )

    lines.append("")
    return "\n".join(lines)


def parse_categories_yaml(yaml_text: str) -> dict[str, str]:
    """Parse `categories.yaml` into a `{bundle_id: category}` dictionary."""
    if not yaml_text.strip():
        return {}
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        return {}
    raw_map = data.get("categories") if isinstance(data.get("categories"), dict) else data
    out: dict[str, str] = {}
    for k, v in raw_map.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            out[k.strip()] = v.strip()
    return out
