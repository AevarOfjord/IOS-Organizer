"""Two-way SpringBoard icon state <-> YAML DSL compiler.

Pure functions only: no filesystem I/O and no pymobiledevice3 imports.
"""

from __future__ import annotations

import base64
import copy
import datetime
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

SCHEMA_VERSION = 1
FOLDER_MAX_APPS_PER_PAGE = 9

WIDGET_GRID_SLOTS: dict[str, int] = {
    "small": 4,
    "medium": 8,
    "large": 16,
    "extraLarge": 24,
}

_PRIMARY_APP_ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "bundleIdentifier",
        "displayIdentifier",
        "displayName",
        "bundleVersion",
        "iconModDate",
    }
)

_OPAQUE_RE = re.compile(r"^@(widget|webclip):([A-Za-z0-9_-]+)(?::(.*))?$", re.DOTALL)


@dataclass(frozen=True)
class OpaqueRef:
    """Parsed reference to an opaque element token (@widget:... or @webclip:...)."""

    kind: str
    token: str
    description: str


@dataclass
class LayoutDiff:
    """Human-readable diff between two SpringBoard icon states."""

    has_changes: bool
    dock_changes: list[str] = field(default_factory=list)
    page_changes: list[str] = field(default_factory=list)
    folder_changes: list[str] = field(default_factory=list)
    moved_apps: list[str] = field(default_factory=list)

    @property
    def lines(self) -> list[str]:
        out: list[str] = []
        out.extend(self.dock_changes)
        out.extend(self.moved_apps)
        out.extend(self.folder_changes)
        out.extend(self.page_changes)
        return out


def to_json_compatible(obj: Any) -> Any:
    """Recursively convert plist types (datetime, bytes) into JSON-safe objects."""
    if isinstance(obj, dict):
        if obj.get("__plist_type__") in ("datetime", "bytes"):
            return {k: to_json_compatible(v) for k, v in obj.items()}
        return {str(k): to_json_compatible(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_json_compatible(x) for x in obj]
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return {"__plist_type__": "datetime", "iso": obj.isoformat()}
    if isinstance(obj, bytes):
        return {
            "__plist_type__": "bytes",
            "base64": base64.b64encode(obj).decode("ascii"),
        }
    return obj


def from_json_compatible(obj: Any) -> Any:
    """Recursively restore plist types (datetime, bytes) from JSON-safe objects."""
    if isinstance(obj, dict):
        plist_type = obj.get("__plist_type__")
        if plist_type == "datetime" and "iso" in obj and len(obj) == 2:
            return datetime.datetime.fromisoformat(str(obj["iso"]))
        if plist_type == "bytes" and "base64" in obj and len(obj) == 2:
            return base64.b64decode(str(obj["base64"]))
        return {k: from_json_compatible(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [from_json_compatible(x) for x in obj]
    return obj


def sanitize_display_name(name: str) -> str:
    """Remove C0/C1 control characters and normalize NBSP so YAML remains printable."""
    cleaned = "".join(
        " " if c == "\xa0" else c
        for c in str(name)
        if not (ord(c) < 32 or 127 <= ord(c) <= 159)
    ).strip()
    return cleaned


def compute_opaque_token(raw_dict: dict[str, Any]) -> str:
    """Compute a deterministic 12-char hex token from a raw dictionary."""
    canonical = json.dumps(
        to_json_compatible(raw_dict),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def format_app_entry(display_name: str, bundle_id: str) -> str:
    """Format an app entry as 'Display Name (bundle.id)'."""
    clean_id = bundle_id.strip()
    clean_name = sanitize_display_name(display_name) or clean_id
    return f"{clean_name} ({clean_id})"


def is_opaque_entry(entry: Any) -> bool:
    """Return True if the entry is an @widget:... or @webclip:... string."""
    return isinstance(entry, str) and entry.strip().startswith("@")


def parse_opaque_entry(entry: str) -> OpaqueRef:
    """Parse '@widget:<token>:<desc>' or '@webclip:<token>:<title>'."""
    if not isinstance(entry, str):
        raise ValueError(f"Expected string for opaque token, got {type(entry).__name__}")
    s = entry.strip()
    match = _OPAQUE_RE.match(s)
    if not match:
        raise ValueError(
            f"Invalid opaque token format: {entry!r}. "
            "Expected '@widget:<token>:<description>' or '@webclip:<token>:<title>'."
        )
    kind, token, desc = match.group(1), match.group(2), (match.group(3) or "").strip()
    return OpaqueRef(kind=kind, token=token, description=desc)


def parse_app_entry(entry: str) -> tuple[str, str]:
    """Parse an app string into (display_name, bundle_id).

    Supports:
      - 'App Name (com.example.app)'
      - 'Foo (Beta) (com.foo)'
      - 'com.example.app' (bare bundle ID)
      - Surrounding whitespace
    """
    if not isinstance(entry, str):
        raise ValueError(f"App entry must be a string, got {type(entry).__name__}")
    s = entry.strip()
    if not s:
        raise ValueError("App entry cannot be empty")
    if s.startswith("@"):
        raise ValueError(f"Opaque token cannot be parsed as an app entry: {entry!r}")

    if s.endswith(")") and "(" in s:
        last_open = s.rfind("(")
        bundle_candidate = s[last_open + 1 : -1].strip()
        name_candidate = s[:last_open].strip()
        if (
            bundle_candidate
            and not any(ch.isspace() for ch in bundle_candidate)
            and "(" not in bundle_candidate
            and ")" not in bundle_candidate
        ):
            clean_name = sanitize_display_name(name_candidate) or bundle_candidate
            return clean_name, bundle_candidate

    # Bare bundle identifier (no spaces or parentheses)
    if not any(ch.isspace() for ch in s) and "(" not in s and ")" not in s:
        return s, s

    raise ValueError(f"Malformed app entry: {entry!r}")


def is_primary_app_dict(item: Any) -> bool:
    """Return True if `item` represents a primary application icon in raw state."""
    if isinstance(item, str):
        return bool(item.strip()) and not item.strip().startswith("@")
    if not isinstance(item, dict):
        return False
    if item.get("listType") == "folder":
        return False
    if "webClipURL" in item:
        return False
    if item.get("iconType") in ("custom", "app"):
        return False
    if not set(item.keys()).issubset(_PRIMARY_APP_ALLOWED_KEYS):
        return False
    bid = item.get("bundleIdentifier") or item.get("displayIdentifier")
    if not isinstance(bid, str) or not bid.strip():
        return False
    # If both bundleIdentifier and displayIdentifier are present, they must match
    # for a primary app icon (secondary bookmarks have a UUID displayIdentifier).
    if "bundleIdentifier" in item and "displayIdentifier" in item:
        if item["bundleIdentifier"] != item["displayIdentifier"]:
            return False
    return True


def _extract_primary_app_id_and_name(
    item: str | dict[str, Any],
    installed_apps: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Return (display_name, bundle_id) for a primary app item in raw state."""
    if isinstance(item, str):
        bundle_id = item.strip()
        raw_name = ""
    else:
        bundle_id = str(item.get("bundleIdentifier") or item.get("displayIdentifier")).strip()
        raw_name = str(item.get("displayName") or "").strip()

    app_meta = (installed_apps or {}).get(bundle_id)
    if app_meta and app_meta.get("name") and app_meta["name"] != bundle_id:
        display_name = str(app_meta["name"])
    elif raw_name:
        display_name = raw_name
    elif app_meta and app_meta.get("name"):
        display_name = str(app_meta["name"])
    else:
        display_name = bundle_id

    return sanitize_display_name(display_name) or bundle_id, bundle_id


def _describe_widget(
    item: dict[str, Any],
    installed_apps: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Build a human-friendly description for a widget, Smart Stack, or unknown dict."""
    if item.get("iconType") == "app":
        bid = str(item.get("bundleIdentifier") or item.get("displayIdentifier") or "unknown")
        app_name = (
            (installed_apps or {}).get(bid, {}).get("name")
            or item.get("displayName")
            or bid
        )
        clean_name = sanitize_display_name(str(app_name)) or bid
        return f"App Bookmark - {clean_name} ({bid})"

    grid_size = item.get("gridSize")
    elements = item.get("elements")
    if isinstance(elements, list) and elements:
        names: list[str] = []
        for elem in elements:
            if not isinstance(elem, dict):
                continue
            container_bid = str(elem.get("containerBundleIdentifier") or "")
            widget_id = str(elem.get("widgetIdentifier") or elem.get("bundleIdentifier") or "")
            label = ""
            if container_bid and installed_apps and container_bid in installed_apps:
                label = str(installed_apps[container_bid].get("name") or "")
            if not label and container_bid:
                label = container_bid.split(".")[-1]
            if not label and widget_id:
                label = widget_id.split(".")[-1]
            label = sanitize_display_name(label) or "Widget"
            if label not in names:
                names.append(label)
        joined = ", ".join(names) if names else "Widget"
        prefix = f"Smart Stack [{joined}]" if len(elements) > 1 else joined
        if isinstance(grid_size, str) and grid_size:
            return f"{prefix} ({grid_size})"
        return prefix

    fallback = (
        item.get("displayName")
        or item.get("widgetIdentifier")
        or item.get("iconType")
        or item.get("listType")
        or "Opaque Element"
    )
    clean_fallback = sanitize_display_name(str(fallback)) or "Opaque Element"
    if isinstance(grid_size, str) and grid_size:
        return f"{clean_fallback} ({grid_size})"
    return clean_fallback


def chunk_folder_apps(
    apps: list[Any],
    page_size: int = FOLDER_MAX_APPS_PER_PAGE,
) -> list[list[Any]]:
    """Chunk a flat list of folder items into pages of at most `page_size` (default 9)."""
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    if not apps:
        return []
    return [apps[i : i + page_size] for i in range(0, len(apps), page_size)]


def folder_icon_lists(
    apps: list[Any], page_sizes: Any = None, page_size: int = FOLDER_MAX_APPS_PER_PAGE,
) -> list[list[Any]]:
    """Preserve explicit folder boundaries, or paginate newly created folders."""
    if page_sizes is None:
        return chunk_folder_apps(apps, page_size)
    if (
        not isinstance(page_sizes, list)
        or not page_sizes
        or any(type(size) is not int or not 0 <= size <= page_size for size in page_sizes)
        or sum(page_sizes) != len(apps)
    ):
        raise ValueError(
            f"Folder page_sizes must be a non-empty list of integers from 0 to {page_size} "
            "whose sum equals the number of apps. Update or remove page_sizes after editing apps."
        )
    pages = []
    offset = 0
    for size in page_sizes:
        pages.append(apps[offset:offset + size])
        offset += size
    return pages


def _decompile_item(
    item: Any,
    opaque_store: dict[str, dict[str, Any]],
    installed_apps: dict[str, dict[str, Any]] | None,
    placed_bundle_ids: set[str],
) -> str | dict[str, Any]:
    """Decompile a single raw SpringBoard element into its YAML DSL representation."""
    if is_primary_app_dict(item):
        display_name, bundle_id = _extract_primary_app_id_and_name(item, installed_apps)
        placed_bundle_ids.add(bundle_id)
        return format_app_entry(display_name, bundle_id)

    if isinstance(item, dict) and item.get("listType") == "folder":
        folder_name = sanitize_display_name(str(item.get("displayName") or "Folder")) or "Folder"
        icon_lists = item.get("iconLists") or []
        flat_apps: list[Any] = []
        for subpage in icon_lists:
            if isinstance(subpage, list):
                for subitem in subpage:
                    flat_apps.append(
                        _decompile_item(subitem, opaque_store, installed_apps, placed_bundle_ids)
                    )

        extra_keys = {
            k: v
            for k, v in item.items()
            if k not in {"displayName", "iconLists", "listType"}
        }
        folder_entry: dict[str, Any] = {"folder": folder_name}
        if extra_keys:
            tok = compute_opaque_token(extra_keys)
            opaque_store[tok] = to_json_compatible(copy.deepcopy(extra_keys))
            folder_entry["token"] = tok
        folder_entry["apps"] = flat_apps
        page_sizes = [len(page) for page in icon_lists if isinstance(page, list)]
        if page_sizes:
            folder_entry["page_sizes"] = page_sizes
        return folder_entry

    if isinstance(item, dict) and "webClipURL" in item:
        tok = compute_opaque_token(item)
        opaque_store[tok] = to_json_compatible(copy.deepcopy(item))
        title = sanitize_display_name(
            str(item.get("displayName") or item.get("webClipURL") or "WebClip")
        ) or "WebClip"
        return f"@webclip:{tok}:{title}"

    if isinstance(item, dict):
        if item.get("iconType") == "app" and isinstance(item.get("bundleIdentifier"), str):
            placed_bundle_ids.add(item["bundleIdentifier"])
        tok = compute_opaque_token(item)
        opaque_store[tok] = to_json_compatible(copy.deepcopy(item))
        desc = _describe_widget(item, installed_apps)
        return f"@widget:{tok}:{desc}"

    raise ValueError(f"Unsupported raw icon state element: {item!r}")


def decompile(
    raw_state: list[Any],
    installed_apps: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Decompile a raw SpringBoard icon state into (layout_dict, opaque_store).

    Args:
        raw_state: Raw list returned by SpringBoardServicesService.get_icon_state("2").
        installed_apps: Optional mapping of installed apps from InstallationProxyService.

    Returns:
        (layout_dict, opaque_store) where opaque_store maps deterministic tokens
        to JSON-compatible raw dicts for widgets, web clips, bookmarks, and folder extras.
    """
    if not isinstance(raw_state, list):
        raise ValueError("raw_state must be a list of icon lists")

    opaque_store: dict[str, dict[str, Any]] = {}
    placed_bundle_ids: set[str] = set()

    raw_dock = raw_state[0] if len(raw_state) > 0 and isinstance(raw_state[0], list) else []
    dock_items = [
        _decompile_item(item, opaque_store, installed_apps, placed_bundle_ids)
        for item in raw_dock
    ]

    pages: list[dict[str, Any]] = []
    for page_num, raw_page in enumerate(raw_state[1:], start=1):
        if not isinstance(raw_page, list):
            continue
        page_items = [
            _decompile_item(item, opaque_store, installed_apps, placed_bundle_ids)
            for item in raw_page
        ]
        pages.append({"page": page_num, "items": page_items})

    unassigned_apps: list[str] = []
    if installed_apps:
        unassigned_pairs: list[tuple[str, str]] = []
        for bundle_id, info in installed_apps.items():
            if bundle_id not in placed_bundle_ids:
                name = sanitize_display_name(str(info.get("name") or bundle_id)) or bundle_id
                unassigned_pairs.append((name, bundle_id))
        unassigned_pairs.sort(key=lambda pair: (pair[0].casefold(), pair[1]))
        unassigned_apps = [format_app_entry(name, bid) for name, bid in unassigned_pairs]

    layout_dict: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dock": dock_items,
        "pages": pages,
        "unassigned_apps": unassigned_apps,
    }
    return layout_dict, opaque_store


def _compile_item(
    entry: Any,
    opaque_store: dict[str, dict[str, Any]],
    installed_apps: dict[str, dict[str, Any]] | None,
    folder_page_size: int,
) -> dict[str, Any] | None:
    """Compile a single YAML DSL item back into a raw SpringBoard dictionary."""
    if isinstance(entry, dict) and set(entry) & {'row', 'column', 'position', 'empty', 'widget'}:
        raise ValueError('Exact cells and new widgets require a spatial plan and the native UI workflow.')
    if isinstance(entry, dict) and "folder" in entry:
        folder_name = str(entry["folder"])
        raw_apps = entry.get("apps") or []
        if not isinstance(raw_apps, list):
            raise ValueError(f"Folder {folder_name!r} 'apps' must be a list")

        compiled_apps: list[dict[str, Any]] = []
        for sub in raw_apps:
            compiled_sub = _compile_item(sub, opaque_store, installed_apps, folder_page_size)
            if compiled_sub is not None:
                compiled_apps.append(compiled_sub)

        icon_lists = folder_icon_lists(
            compiled_apps, entry.get("page_sizes"), page_size=folder_page_size,
        )
        if not compiled_apps:
            # Empty folders are dropped on compile
            return None

        folder_dict: dict[str, Any] = {}
        tok = entry.get("token")
        if tok is not None:
            tok_str = str(tok)
            if tok_str not in opaque_store:
                raise KeyError(f"Unknown opaque folder token: {tok_str}")
            folder_dict.update(from_json_compatible(copy.deepcopy(opaque_store[tok_str])))

        folder_dict["displayName"] = folder_name
        folder_dict["listType"] = "folder"
        folder_dict["iconLists"] = icon_lists
        return folder_dict

    if isinstance(entry, str):
        if is_opaque_entry(entry):
            ref = parse_opaque_entry(entry)
            if ref.token not in opaque_store:
                raise KeyError(f"Unknown opaque token: {ref.token}")
            return from_json_compatible(copy.deepcopy(opaque_store[ref.token]))

        display_name, bundle_id = parse_app_entry(entry)
        if installed_apps and bundle_id in installed_apps and display_name == bundle_id:
            display_name = str(installed_apps[bundle_id].get("name") or bundle_id)
        return {
            "bundleIdentifier": bundle_id,
            "displayIdentifier": bundle_id,
            "displayName": display_name,
        }

    raise ValueError(f"Unsupported DSL entry during compile: {entry!r}")


def compile_layout(
    layout_data: dict[str, Any],
    opaque_store: dict[str, dict[str, Any]] | None = None,
    installed_apps: dict[str, dict[str, Any]] | None = None,
    folder_page_size: int = FOLDER_MAX_APPS_PER_PAGE,
) -> list[Any]:
    """Compile a parsed YAML layout dict into the raw structure set_icon_state() expects."""
    if not isinstance(layout_data, dict):
        raise ValueError("layout_data must be a mapping")
    if set(layout_data) & {'spatial_version', 'spatial', 'screen_designs'}:
        raise ValueError('Spatial designs cannot be sent through USB push, even with --force. Use ui plan and the native UI workflow.')

    store = opaque_store or {}

    dock_entries = layout_data.get("dock") or []
    if not isinstance(dock_entries, list):
        raise ValueError("'dock' must be a list")

    compiled_dock: list[Any] = []
    for entry in dock_entries:
        item = _compile_item(entry, store, installed_apps, folder_page_size)
        if item is not None:
            compiled_dock.append(item)

    pages_entries = layout_data.get("pages") or []
    if not isinstance(pages_entries, list):
        raise ValueError("'pages' must be a list")

    compiled_pages: list[list[Any]] = []
    for page_obj in pages_entries:
        if not isinstance(page_obj, dict):
            raise ValueError(f"Page entry must be a mapping, got {page_obj!r}")
        raw_items = page_obj.get("items") or []
        if not isinstance(raw_items, list):
            raise ValueError(f"Page {page_obj.get('page')} 'items' must be a list")
        compiled_page: list[Any] = []
        for entry in raw_items:
            item = _compile_item(entry, store, installed_apps, folder_page_size)
            if item is not None:
                compiled_page.append(item)
        compiled_pages.append(compiled_page)

    compiled_state = [compiled_dock, *compiled_pages]
    # This invariant is enforced even when the CLI's --force option is used.
    seen_identifiers: set[str] = set()

    def check_identifiers(items: list[Any]) -> None:
        for item in items:
            identifier = item.get("displayIdentifier")
            if isinstance(identifier, str) and identifier:
                if identifier in seen_identifiers:
                    raise ValueError(f"Duplicate displayIdentifier: {identifier!r}")
                seen_identifiers.add(identifier)
            if item.get("listType") == "folder":
                for page in item["iconLists"]:
                    check_identifiers(page)

    for page in compiled_state:
        check_identifiers(page)
    return compiled_state


# Alias `compile` and `decompile_layout` for convenience
compile = compile_layout
decompile_layout = decompile


def _normalize_element(
    item: Any,
    preserve_folder_pages: bool = True,
) -> Any:
    """Normalize a single raw icon element for semantic equivalence comparison."""
    if is_primary_app_dict(item):
        _, bundle_id = _extract_primary_app_id_and_name(item)
        return {
            "bundleIdentifier": bundle_id,
            "displayIdentifier": bundle_id,
        }

    if isinstance(item, dict) and item.get("listType") == "folder":
        raw_lists = item.get("iconLists") or []
        if preserve_folder_pages:
            norm_lists = [
                [_normalize_element(sub, preserve_folder_pages=True) for sub in subpage]
                for subpage in raw_lists
                if isinstance(subpage, list)
            ]
        else:
            flat_norm = [
                _normalize_element(sub, preserve_folder_pages=False)
                for subpage in raw_lists
                if isinstance(subpage, list)
                for sub in subpage
            ]
            norm_lists = chunk_folder_apps(flat_norm, page_size=FOLDER_MAX_APPS_PER_PAGE)

        norm_folder: dict[str, Any] = {}
        for k, v in item.items():
            if k not in {"displayName", "iconLists", "listType"}:
                norm_folder[k] = to_json_compatible(v)
        norm_folder["displayName"] = sanitize_display_name(str(item.get("displayName") or "Folder")) or "Folder"
        norm_folder["listType"] = "folder"
        norm_folder["iconLists"] = norm_lists
        return norm_folder

    return to_json_compatible(item)


def normalize_icon_state(
    raw_state: list[Any],
    preserve_folder_pages: bool = True,
) -> list[Any]:
    """Normalize a raw SpringBoard icon state by stripping transient fields iOS ignores.

    Transient fields stripped on primary app icons:
      - iconModDate
      - bundleVersion
      - displayName (cosmetic; SpringBoard resolves from bundleIdentifier/displayIdentifier)
    """
    if not isinstance(raw_state, list) or not raw_state:
        return [[]]

    norm_dock = [
        _normalize_element(item, preserve_folder_pages=preserve_folder_pages)
        for item in (raw_state[0] if isinstance(raw_state[0], list) else [])
    ]
    norm_pages: list[list[Any]] = []
    for page in raw_state[1:]:
        if not isinstance(page, list):
            continue
        norm_page = [
            _normalize_element(item, preserve_folder_pages=preserve_folder_pages)
            for item in page
        ]
        norm_pages.append(norm_page)
    return [norm_dock, *norm_pages]


def estimate_item_slots(
    entry: Any,
    opaque_store: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, bool]:
    """Return (slot_count, size_is_known) for a single page entry."""
    if isinstance(entry, dict) and "folder" in entry:
        return 1, True
    if isinstance(entry, str) and is_opaque_entry(entry):
        try:
            ref = parse_opaque_entry(entry)
        except ValueError:
            return 1, False
        if ref.kind == "webclip":
            return 1, True
        raw_obj = (opaque_store or {}).get(ref.token)
        if not isinstance(raw_obj, dict):
            return 4, False
        if raw_obj.get("iconType") == "app":
            return 1, True
        grid_size = raw_obj.get("gridSize")
        if isinstance(grid_size, str) and grid_size in WIDGET_GRID_SLOTS:
            return WIDGET_GRID_SLOTS[grid_size], True
        return 4, False
    return 1, True


def dump_layout_yaml(
    layout_data: dict[str, Any],
    backup_filename: str | None = None,
    device_name: str | None = None,
    ios_version: str | None = None,
    categories: dict[str, str] | None = None,
    installed_apps: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Render `layout_data` into the clean, human- and LLM-friendly layout.yaml format."""
    lines: list[str] = []
    if backup_filename:
        lines.append(f"# Generated by ios-organizer from {backup_filename}")
    else:
        lines.append("# Generated by ios-organizer")
    if device_name or ios_version:
        dev_label = device_name or "iPhone"
        ver_label = f" (iOS {ios_version})" if ios_version else ""
        lines.append(f"# Device: {dev_label}{ver_label}")

    schema_ver = layout_data.get("schema_version", SCHEMA_VERSION)
    lines.append(f"schema_version: {schema_ver}")
    lines.append("")

    # Dock
    dock = layout_data.get("dock") or []
    if not dock:
        lines.append("dock: []")
    else:
        lines.append("dock:")
        for item in dock:
            lines.extend(_format_yaml_item(item, indent=2))
    lines.append("")

    # Pages
    pages = layout_data.get("pages") or []
    if not pages:
        lines.append("pages: []")
    else:
        lines.append("pages:")
        for page_obj in pages:
            page_num = page_obj.get("page", 1)
            lines.append(f"  - page: {page_num}")
            page_items = page_obj.get("items") or []
            if not page_items:
                lines.append("    items: []")
            else:
                lines.append("    items:")
                for item in page_items:
                    lines.extend(_format_yaml_item(item, indent=6))
    lines.append("")

    # Unassigned apps
    lines.append("# Installed apps currently only in the App Library.")
    lines.append("# Warning: pushing may place omitted apps onto the Home Screen; App Library-only placement is not preserved.")
    unassigned = layout_data.get("unassigned_apps") or []
    if not unassigned:
        lines.append("unassigned_apps: []")
    else:
        lines.append("unassigned_apps:")
        for entry in unassigned:
            quoted = _yaml_double_quote(str(entry))
            comment = ""
            try:
                _, bid = parse_app_entry(str(entry))
                cat = (categories or {}).get(bid) or (installed_apps or {}).get(bid, {}).get("genre")
                if cat:
                    comment = f"   # category: {cat}"
            except ValueError:
                pass
            lines.append(f"  - {quoted}{comment}")

    lines.append("")
    return "\n".join(lines)


def _yaml_double_quote(val: str) -> str:
    """Format a string as a safe double-quoted YAML scalar."""
    escaped = (
        val.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _format_yaml_item(item: Any, indent: int) -> list[str]:
    pad = " " * indent
    if isinstance(item, dict) and "folder" in item:
        out = [f"{pad}- folder: {_yaml_double_quote(str(item['folder']))}"]
        if "token" in item and item["token"] is not None:
            out.append(f"{pad}  token: {_yaml_double_quote(str(item['token']))}")
        if "page_sizes" in item:
            out.append(f"{pad}  page_sizes: {json.dumps(item['page_sizes'])}")
        apps = item.get("apps") or []
        if not apps:
            out.append(f"{pad}  apps: []")
        else:
            out.append(f"{pad}  apps:")
            for app in apps:
                out.append(f"{pad}    - {_yaml_double_quote(str(app))}")
        return out
    return [f"{pad}- {_yaml_double_quote(str(item))}"]


def parse_layout_yaml(yaml_text: str) -> tuple[dict[str, Any], dict[str, str | None]]:
    """Parse layout.yaml text and extract header metadata comments."""
    header_meta: dict[str, str | None] = {"backup_file": None, "device": None}
    for line in yaml_text.splitlines()[:10]:
        stripped = line.strip()
        if not stripped.startswith("#"):
            break
        if "Generated by ios-organizer from " in stripped:
            header_meta["backup_file"] = stripped.split("Generated by ios-organizer from ", 1)[1].strip()
        elif stripped.startswith("# Device:"):
            header_meta["device"] = stripped.split("# Device:", 1)[1].strip()

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed YAML: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Malformed YAML: top-level document must be a mapping")

    return parsed, header_meta


def _index_layout_locations(
    layout_data: dict[str, Any],
) -> tuple[dict[str, str], list[tuple[str, str, list[str]]]]:
    """Index item locations and folder contents for human-readable diffing."""
    item_locations: dict[str, str] = {}
    folders: list[tuple[str, str, list[str]]] = []

    def _key_for_entry(entry: str) -> str:
        if is_opaque_entry(entry):
            try:
                ref = parse_opaque_entry(entry)
                return f"@{ref.kind}:{ref.token} ({ref.description})"
            except ValueError:
                return entry
        try:
            name, bid = parse_app_entry(entry)
            return f"{name} ({bid})"
        except ValueError:
            return str(entry)

    for idx, entry in enumerate(layout_data.get("dock") or [], start=1):
        if isinstance(entry, str):
            item_locations[_key_for_entry(entry)] = f"Dock [{idx}]"

    for page_obj in layout_data.get("pages") or []:
        if not isinstance(page_obj, dict):
            continue
        pnum = page_obj.get("page", "?")
        for idx, entry in enumerate(page_obj.get("items") or [], start=1):
            if isinstance(entry, dict) and "folder" in entry:
                fname = str(entry["folder"])
                fapps = [_key_for_entry(str(a)) for a in (entry.get("apps") or [])]
                folders.append((f"Page {pnum}", fname, fapps))
                for f_idx, app_key in enumerate(fapps, start=1):
                    item_locations[app_key] = f"Page {pnum} > Folder '{fname}' [{f_idx}]"
            elif isinstance(entry, str):
                item_locations[_key_for_entry(entry)] = f"Page {pnum} [{idx}]"

    return item_locations, folders


def diff_icon_states(
    current_raw: list[Any],
    target_raw: list[Any],
    installed_apps: dict[str, dict[str, Any]] | None = None,
) -> LayoutDiff:
    """Compute a human-readable diff between `current_raw` and `target_raw`."""
    norm_current = normalize_icon_state(current_raw)
    norm_target = normalize_icon_state(target_raw)
    if norm_current == norm_target:
        return LayoutDiff(has_changes=False)

    curr_layout, _ = decompile(current_raw, installed_apps=installed_apps)
    targ_layout, _ = decompile(target_raw, installed_apps=installed_apps)

    dock_changes: list[str] = []
    if curr_layout.get("dock") != targ_layout.get("dock"):
        dock_changes.append(
            f"Dock changed: {curr_layout.get('dock')} -> {targ_layout.get('dock')}"
        )

    page_changes: list[str] = []
    curr_pages = curr_layout.get("pages") or []
    targ_pages = targ_layout.get("pages") or []
    if len(curr_pages) != len(targ_pages):
        page_changes.append(f"Page count changed: {len(curr_pages)} -> {len(targ_pages)}")

    max_p = max(len(curr_pages), len(targ_pages))
    for i in range(max_p):
        if i < len(curr_pages) and i < len(targ_pages):
            c_items = curr_pages[i].get("items") or []
            t_items = targ_pages[i].get("items") or []
            if c_items != t_items:
                page_changes.append(
                    f"Page {i + 1}: {len(c_items)} item(s) -> {len(t_items)} item(s)"
                )
        elif i >= len(curr_pages):
            t_items = targ_pages[i].get("items") or []
            page_changes.append(f"Page {i + 1}: added ({len(t_items)} item(s))")
        else:
            c_items = curr_pages[i].get("items") or []
            page_changes.append(f"Page {i + 1}: removed (had {len(c_items)} item(s))")

    curr_locs, curr_folders = _index_layout_locations(curr_layout)
    targ_locs, targ_folders = _index_layout_locations(targ_layout)

    moved_apps: list[str] = []
    all_keys = sorted(set(curr_locs.keys()) | set(targ_locs.keys()))
    for key in all_keys:
        c_loc = curr_locs.get(key)
        t_loc = targ_locs.get(key)
        if c_loc == t_loc:
            continue
        if c_loc is None:
            moved_apps.append(f"+ Placed {key}: App Library -> {t_loc}")
        elif t_loc is None:
            moved_apps.append(f"- Removed {key}: {c_loc} -> App Library")
        else:
            moved_apps.append(f"~ Moved {key}: {c_loc} -> {t_loc}")

    folder_changes: list[str] = []
    for index, (current_page, target_page) in enumerate(zip(curr_pages, targ_pages), start=1):
        current_folders = [item for item in current_page["items"] if isinstance(item, dict)]
        target_folders = [item for item in target_page["items"] if isinstance(item, dict)]
        for current_folder, target_folder in zip(current_folders, target_folders):
            if (
                current_folder.get("folder") == target_folder.get("folder")
                and current_folder.get("page_sizes") != target_folder.get("page_sizes")
            ):
                folder_changes.append(
                    f"~ Folder '{current_folder['folder']}' on Page {index} pagination: "
                    f"{current_folder.get('page_sizes')} -> {target_folder.get('page_sizes')}"
                )
    curr_folder_names = {(loc, name): apps for loc, name, apps in curr_folders}
    targ_folder_names = {(loc, name): apps for loc, name, apps in targ_folders}
    for (loc, name), apps in targ_folder_names.items():
        if (loc, name) not in curr_folder_names:
            folder_changes.append(f"+ Folder '{name}' on {loc} ({len(apps)} item(s))")
        elif curr_folder_names[(loc, name)] != apps:
            before_len = len(curr_folder_names[(loc, name)])
            folder_changes.append(
                f"~ Folder '{name}' on {loc}: {before_len} item(s) -> {len(apps)} item(s)"
            )
    for (loc, name), apps in curr_folder_names.items():
        if (loc, name) not in targ_folder_names:
            folder_changes.append(f"- Folder '{name}' removed from {loc} (had {len(apps)} item(s))")

    return LayoutDiff(
        has_changes=True,
        dock_changes=dock_changes,
        page_changes=page_changes,
        folder_changes=folder_changes,
        moved_apps=moved_apps,
    )
