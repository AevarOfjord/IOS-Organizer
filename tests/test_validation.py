"""Unit tests for ios_organizer.validation."""

from __future__ import annotations

from ios_organizer.validation import validate_layout


def _sample_installed_apps() -> dict[str, dict]:
    return {
        "com.apple.mobilephone": {"name": "Phone", "type": "System", "genre": "Utilities"},
        "com.apple.MobileSMS": {"name": "Messages", "type": "System", "genre": "Social"},
        "com.apple.mobilesafari": {"name": "Safari", "type": "System", "genre": "Utilities"},
        "com.apple.Music": {"name": "Music", "type": "System", "genre": "Music"},
        "com.apple.camera": {"name": "Camera", "type": "System", "genre": "Photography"},
        "com.apple.mobilenotes": {"name": "Notes", "type": "System", "genre": "Productivity"},
        "com.example.extra": {"name": "Extra App", "type": "User", "genre": None},
    }


def test_valid_layout_passes_with_no_errors_or_warnings() -> None:
    installed = _sample_installed_apps()
    layout = {
        "schema_version": 1,
        "dock": [
            "Phone (com.apple.mobilephone)",
            "Messages (com.apple.MobileSMS)",
        ],
        "pages": [
            {
                "page": 1,
                "items": [
                    "Safari (com.apple.mobilesafari)",
                    "Camera (com.apple.camera)",
                    {
                        "folder": "Work",
                        "apps": ["Notes (com.apple.mobilenotes)"],
                    },
                ],
            }
        ],
        "unassigned_apps": [
            "Music (com.apple.Music)",
            "Extra App (com.example.extra)",
        ],
    }
    res = validate_layout(layout, installed_apps=installed, opaque_store={})
    assert res.is_valid
    assert res.errors == []
    assert res.warnings == []


def test_duplicate_bundle_ids_raise_error() -> None:
    installed = _sample_installed_apps()
    layout = {
        "schema_version": 1,
        "dock": ["Phone (com.apple.mobilephone)"],
        "pages": [
            {
                "page": 1,
                "items": [
                    "Camera (com.apple.camera)",
                    {
                        "folder": "DupFolder",
                        "apps": ["Phone (com.apple.mobilephone)"],
                    },
                ],
            }
        ],
        "unassigned_apps": [
            "Messages (com.apple.MobileSMS)",
            "Safari (com.apple.mobilesafari)",
            "Music (com.apple.Music)",
            "Notes (com.apple.mobilenotes)",
            "Extra App (com.example.extra)",
        ],
    }
    res = validate_layout(layout, installed_apps=installed)
    assert not res.is_valid
    assert any("Duplicate bundle ID 'com.apple.mobilephone'" in e for e in res.errors)


def test_uninstalled_bundle_id_raises_error() -> None:
    installed = _sample_installed_apps()
    layout = {
        "schema_version": 1,
        "dock": [],
        "pages": [
            {
                "page": 1,
                "items": ["Ghost App (com.nonexistent.ghost)"],
            }
        ],
        "unassigned_apps": [],
    }
    res = validate_layout(layout, installed_apps=installed)
    assert not res.is_valid
    assert any("com.nonexistent.ghost" in e and "not installed" in e for e in res.errors)


def test_unknown_opaque_token_raises_error() -> None:
    layout = {
        "schema_version": 1,
        "dock": [],
        "pages": [
            {
                "page": 1,
                "items": ["@widget:deadbeef0000:Missing Widget (small)"],
            }
        ],
        "unassigned_apps": [],
    }
    res = validate_layout(layout, opaque_store={})
    assert not res.is_valid
    assert any("Unknown opaque token 'deadbeef0000'" in e for e in res.errors)


def test_dock_overflow_raises_error() -> None:
    layout = {
        "schema_version": 1,
        "dock": [
            "App 1 (com.example.a1)",
            "App 2 (com.example.a2)",
            "App 3 (com.example.a3)",
            "App 4 (com.example.a4)",
            "App 5 (com.example.a5)",
        ],
        "pages": [],
        "unassigned_apps": [],
    }
    res = validate_layout(layout, dock_capacity=4)
    assert not res.is_valid
    assert any("Dock exceeds maximum capacity" in e for e in res.errors)


def test_schema_version_mismatch_raises_error() -> None:
    layout = {
        "schema_version": 99,
        "dock": [],
        "pages": [],
        "unassigned_apps": [],
    }
    res = validate_layout(layout)
    assert not res.is_valid
    assert any("schema_version" in e for e in res.errors)


def test_capacity_and_missing_apps_and_empty_containers_raise_warnings() -> None:
    installed = _sample_installed_apps()
    opaque_store = {
        "w_large": {"iconType": "custom", "gridSize": "large"},  # 16 slots
        "w_unknown": {"iconType": "custom"},  # uncertain gridSize -> 4 slots + warning
    }
    layout = {
        "schema_version": 1,
        "dock": [],
        "pages": [
            {
                "page": 1,
                "items": [
                    "@widget:w_large:Large Widget (large)",
                    "@widget:w_unknown:Mystery Widget",
                    "Phone (com.apple.mobilephone)",
                    "Messages (com.apple.MobileSMS)",
                    "Safari (com.apple.mobilesafari)",
                    "Music (com.apple.Music)",
                    "Camera (com.apple.camera)",
                    {"folder": "EmptyFolder", "apps": []},
                ],
            },
            {
                "page": 2,
                "items": [],
            },
        ],
        "unassigned_apps": [],  # Missing Notes & Extra App!
    }
    res = validate_layout(
        layout,
        installed_apps=installed,
        opaque_store=opaque_store,
        page_capacity=24,
    )
    assert res.is_valid  # Warnings do not block push!
    assert any("uncertain grid size" in w for w in res.warnings)
    assert any("exceed grid capacity" in w for w in res.warnings)
    assert any("Empty folder 'EmptyFolder'" in w for w in res.warnings)
    assert any("Page 2 is empty" in w for w in res.warnings)
    assert any("missing from both Home Screen pages and unassigned_apps" in w for w in res.warnings)


def test_stale_yaml_warning() -> None:
    backup_raw = [[{"bundleIdentifier": "com.apple.mobilephone", "displayIdentifier": "com.apple.mobilephone"}]]
    current_raw = [[{"bundleIdentifier": "com.apple.MobileSMS", "displayIdentifier": "com.apple.MobileSMS"}]]
    layout = {
        "schema_version": 1,
        "dock": ["Phone (com.apple.mobilephone)"],
        "pages": [],
        "unassigned_apps": [],
    }
    res = validate_layout(
        layout,
        source_backup_raw=backup_raw,
        current_device_raw=current_raw,
    )
    assert res.is_valid
    assert any("stale" in w for w in res.warnings)
