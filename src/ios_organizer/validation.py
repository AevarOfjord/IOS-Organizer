"""Layout validation rules producing blocking errors and non-blocking warnings.

Pure functions only: no filesystem I/O and no pymobiledevice3 imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ios_organizer.compiler import (
    SCHEMA_VERSION,
    estimate_item_slots,
    folder_icon_lists,
    is_opaque_entry,
    normalize_icon_state,
    parse_app_entry,
    parse_opaque_entry,
)


@dataclass
class ValidationResult:
    """Result of validating a layout dictionary."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def validate_layout(
    layout_data: Any,
    installed_apps: dict[str, dict[str, Any]] | None = None,
    opaque_store: dict[str, dict[str, Any]] | None = None,
    dock_capacity: int = 4,
    page_capacity: int = 24,
    source_backup_raw: list[Any] | None = None,
    current_device_raw: list[Any] | None = None,
    folder_page_capacity: int = 9,
) -> ValidationResult:
    """Validate a parsed layout YAML dictionary against device and schema rules."""
    result = ValidationResult()
    store = opaque_store or {}

    if not isinstance(layout_data, dict):
        result.errors.append("Malformed layout: root YAML element must be a mapping.")
        return result
    if set(layout_data) & {'spatial_version', 'spatial', 'screen_designs'}:
        result.errors.append('Spatial designs require ui plan and native UI actions; USB push would lose exact placement.')

    schema_ver = layout_data.get("schema_version")
    if schema_ver != SCHEMA_VERSION:
        result.errors.append(
            f"Unsupported or missing schema_version: {schema_ver!r} (expected {SCHEMA_VERSION})."
        )

    dock = layout_data.get("dock")
    if dock is None:
        dock = []
    elif not isinstance(dock, list):
        result.errors.append("Malformed layout: 'dock' must be a list.")
        dock = []

    pages = layout_data.get("pages")
    if pages is None:
        pages = []
    elif not isinstance(pages, list):
        result.errors.append("Malformed layout: 'pages' must be a list.")
        pages = []

    unassigned = layout_data.get("unassigned_apps")
    if unassigned is None:
        unassigned = []
    elif not isinstance(unassigned, list):
        result.errors.append("Malformed layout: 'unassigned_apps' must be a list.")
        unassigned = []

    # 1. Check dock capacity
    if len(dock) > dock_capacity:
        result.errors.append(
            f"Dock exceeds maximum capacity: {len(dock)} items (max {dock_capacity})."
        )

    seen_bundle_locations: dict[str, str] = {}
    referenced_in_bookmarks: set[str] = set()
    seen_opaque_tokens: dict[str, str] = {}
    seen_identifiers: dict[str, str] = {}

    def _register_identifier(identifier: Any, location: str) -> None:
        if isinstance(identifier, str) and identifier:
            if identifier in seen_identifiers:
                result.errors.append(
                    f"Duplicate displayIdentifier '{identifier}': found at "
                    f"{seen_identifiers[identifier]} and {location}."
                )
            else:
                seen_identifiers[identifier] = location

    def _validate_entry(entry: Any, location: str, allow_folder: bool) -> None:
        if isinstance(entry, dict):
            if set(entry) & {'row', 'column', 'position', 'empty', 'widget'}:
                result.errors.append(f'Exact placement or widget creation at {location} requires the native UI workflow.')
            if not allow_folder or "folder" not in entry:
                result.errors.append(f"Malformed item at {location}: {entry!r}")
                return
            fname = entry.get("folder")
            if not isinstance(fname, str) or not fname.strip():
                result.errors.append(f"Folder at {location} must have a non-empty string name.")
            tok = entry.get("token")
            if tok is not None:
                tok_str = str(tok)
                if tok_str not in store:
                    result.errors.append(
                        f"Unknown opaque folder token '{tok_str}' on folder '{fname}' at {location}."
                    )
                else:
                    _register_identifier(store[tok_str].get("displayIdentifier"), location)
            fapps = entry.get("apps")
            if not isinstance(fapps, list):
                result.errors.append(
                    f"Folder '{fname}' at {location} must have an 'apps' list."
                )
                return
            try:
                folder_icon_lists(fapps, entry.get("page_sizes"), folder_page_capacity)
            except ValueError as exc:
                result.errors.append(f"Folder '{fname}' at {location}: {exc}")
            if len(fapps) == 0:
                result.warnings.append(
                    f"Empty folder '{fname}' at {location} will be dropped on compile."
                )
            for idx, sub in enumerate(fapps, start=1):
                _validate_entry(
                    sub,
                    f"{location} > Folder '{fname}' [{idx}]",
                    allow_folder=False,
                )
            return

        if not isinstance(entry, str):
            result.errors.append(f"Invalid item type at {location}: {entry!r}")
            return

        if is_opaque_entry(entry):
            try:
                ref = parse_opaque_entry(entry)
            except ValueError as exc:
                result.errors.append(f"{exc} (at {location})")
                return
            if ref.token not in store:
                result.errors.append(
                    f"Unknown opaque token '{ref.token}' in '{entry}' at {location} "
                    "(no matching entry in .opaque/)."
                )
            else:
                raw_obj = store[ref.token]
                if ref.token in seen_opaque_tokens:
                    result.errors.append(
                        f"Duplicate opaque token '{ref.token}': found at "
                        f"{seen_opaque_tokens[ref.token]} and {location}."
                    )
                seen_opaque_tokens[ref.token] = location
                _register_identifier(raw_obj.get("displayIdentifier"), location)
                if isinstance(raw_obj, dict) and raw_obj.get("iconType") == "app":
                    bid = raw_obj.get("bundleIdentifier")
                    if isinstance(bid, str) and bid:
                        referenced_in_bookmarks.add(bid)
                        if installed_apps is not None and bid not in installed_apps:
                            result.errors.append(
                                f"Bundle ID '{bid}' (in bookmark token '{ref.token}' at {location}) "
                                "is not installed on the device."
                            )
            return

        try:
            _, bundle_id = parse_app_entry(entry)
        except ValueError as exc:
            result.errors.append(f"{exc} (at {location})")
            return

        if bundle_id in seen_bundle_locations:
            prev_loc = seen_bundle_locations[bundle_id]
            result.errors.append(
                f"Duplicate bundle ID '{bundle_id}': found at {prev_loc} and {location}."
            )
        else:
            seen_bundle_locations[bundle_id] = location
        _register_identifier(bundle_id, location)

        if installed_apps is not None and bundle_id not in installed_apps:
            result.errors.append(
                f"Bundle ID '{bundle_id}' at {location} is not installed on the device."
            )

    # Validate dock items
    for idx, item in enumerate(dock, start=1):
        _validate_entry(item, f"Dock [{idx}]", allow_folder=False)

    # Validate pages
    for page_idx, page_obj in enumerate(pages, start=1):
        if not isinstance(page_obj, dict):
            result.errors.append(f"Page entry #{page_idx} must be a mapping.")
            continue
        page_num = page_obj.get("page", page_idx)
        items = page_obj.get("items")
        if items is None:
            items = []
        elif not isinstance(items, list):
            result.errors.append(f"Page {page_num} 'items' must be a list.")
            continue

        if len(items) == 0:
            result.warnings.append(f"Page {page_num} is empty; its position will be preserved on compile.")
            continue

        total_slots = 0
        uncertain_widgets: list[str] = []
        for idx, item in enumerate(items, start=1):
            _validate_entry(item, f"Page {page_num} [{idx}]", allow_folder=True)
            slots, known = estimate_item_slots(item, store)
            total_slots += slots
            if not known:
                uncertain_widgets.append(str(item))

        if uncertain_widgets:
            result.warnings.append(
                f"Page {page_num} contains widget(s) with uncertain grid size "
                f"({', '.join(uncertain_widgets)}); estimated {total_slots}/{page_capacity} slots."
            )
        if total_slots > page_capacity:
            result.warnings.append(
                f"Page {page_num} may exceed grid capacity: estimated {total_slots} slots "
                f"(grid limit is {page_capacity})."
            )

    # Validate unassigned_apps and check for missing installed apps
    unassigned_bundle_ids: set[str] = set()
    for idx, entry in enumerate(unassigned, start=1):
        if not isinstance(entry, str):
            result.errors.append(f"Invalid entry in unassigned_apps [{idx}]: {entry!r}")
            continue
        try:
            _, bid = parse_app_entry(entry)
            unassigned_bundle_ids.add(bid)
        except ValueError as exc:
            result.errors.append(f"Malformed entry in unassigned_apps [{idx}]: {exc}")

    if installed_apps is not None:
        placed_or_bookmarked = set(seen_bundle_locations.keys()) | referenced_in_bookmarks
        accounted_for = placed_or_bookmarked | unassigned_bundle_ids
        missing_apps = sorted(set(installed_apps.keys()) - accounted_for)
        if missing_apps:
            sample = ", ".join(
                f"{installed_apps[b].get('name', b)} ({b})" for b in missing_apps[:5]
            )
            more = f" (+{len(missing_apps) - 5} more)" if len(missing_apps) > 5 else ""
            result.warnings.append(
                f"{len(missing_apps)} installed app(s) missing from both Home Screen pages "
                f"and unassigned_apps: {sample}{more}."
            )

    # Check staleness if both backup and current device states are provided
    if source_backup_raw is not None and current_device_raw is not None:
        norm_backup = normalize_icon_state(source_backup_raw, preserve_folder_pages=True)
        norm_current = normalize_icon_state(current_device_raw, preserve_folder_pages=True)
        if norm_backup != norm_current:
            result.warnings.append(
                "layout.yaml is stale: the device's current Home Screen layout has changed "
                "since the backup this YAML was generated from."
            )

    return result
