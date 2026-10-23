"""Unit tests for ios_organizer.compiler (round-trip, chunking, opaque preservation, parser)."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from ios_organizer.compiler import (
    compile_layout,
    compute_opaque_token,
    decompile,
    diff_icon_states,
    dump_layout_yaml,
    from_json_compatible,
    normalize_icon_state,
    parse_app_entry,
    parse_layout_yaml,
    parse_opaque_entry,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> list:
    raw_json = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
    return from_json_compatible(raw_json)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "complex_synthetic_state.json",
        "synthetic_edge_cases.json",
    ],
)
def test_round_trip_raw_to_yaml_to_raw(fixture_name: str) -> None:
    """raw -> decompile -> YAML text -> parse_layout_yaml -> compile == raw (normalized)."""
    raw_state = _load_fixture(fixture_name)

    layout_dict, opaque_store = decompile(raw_state)
    yaml_text = dump_layout_yaml(
        layout_dict,
        backup_filename="backup_20261021_093000.json",
        device_name="Test iPhone",
        ios_version="TEST-OS",
    )

    parsed_layout, header_meta = parse_layout_yaml(yaml_text)
    assert header_meta["backup_file"] == "backup_20261021_093000.json"
    assert header_meta["device"] == "Test iPhone (iOS TEST-OS)"

    recompiled_raw = compile_layout(parsed_layout, opaque_store=opaque_store)

    assert normalize_icon_state(recompiled_raw) == normalize_icon_state(raw_state)
    diff = diff_icon_states(raw_state, recompiled_raw)
    assert not diff.has_changes


def test_folder_chunking_25_and_9_apps() -> None:
    """A 25-app flat folder compiles to pages of [9, 9, 7]; a 9-app folder to [9]; decompile flattens back to 25."""
    apps_25 = [f"App {i:02d} (com.example.app{i:02d})" for i in range(1, 26)]
    apps_9 = [f"Nine {i:02d} (com.example.nine{i:02d})" for i in range(1, 10)]

    layout = {
        "schema_version": 1,
        "dock": [],
        "pages": [
            {
                "page": 1,
                "items": [
                    {"folder": "TwentyFive", "apps": apps_25},
                    {"folder": "NineOnly", "apps": apps_9},
                ],
            }
        ],
        "unassigned_apps": [],
    }

    compiled = compile_layout(layout)
    page_1 = compiled[1]
    folder_25 = page_1[0]
    folder_9 = page_1[1]

    assert folder_25["listType"] == "folder"
    assert folder_25["displayName"] == "TwentyFive"
    assert [len(p) for p in folder_25["iconLists"]] == [9, 9, 7]

    assert folder_9["listType"] == "folder"
    assert folder_9["displayName"] == "NineOnly"
    assert [len(p) for p in folder_9["iconLists"]] == [9]

    decompiled, _ = decompile(compiled)
    dec_folder_25 = decompiled["pages"][0]["items"][0]
    dec_folder_9 = decompiled["pages"][0]["items"][1]

    assert dec_folder_25["apps"] == apps_25
    assert len(dec_folder_25["apps"]) == 25
    assert dec_folder_9["apps"] == apps_9


def test_opaque_widget_moved_to_another_page_preserves_raw_dict_exactly() -> None:
    """Moving a widget token to another page reproduces its raw dict byte-for-byte."""
    raw_state = _load_fixture("synthetic_edge_cases.json")
    original_widget_dict = raw_state[1][0]
    original_unknown_dict = raw_state[2][2]

    layout_dict, opaque_store = decompile(raw_state)

    # Move widget from page 1 (index 0) to page 2 (end of items)
    widget_token_entry = layout_dict["pages"][0]["items"].pop(0)
    assert widget_token_entry.startswith("@widget:")
    layout_dict["pages"][1]["items"].append(widget_token_entry)

    recompiled = compile_layout(layout_dict, opaque_store=opaque_store)
    moved_widget_dict = recompiled[2][-1]
    unknown_dict_after = recompiled[2][-2]

    assert moved_widget_dict == original_widget_dict
    assert unknown_dict_dict_equal(unknown_dict_after, original_unknown_dict)
    # Also confirm plist bytes and datetime types were restored
    webclip_after = recompiled[2][0]
    assert isinstance(webclip_after["iconModDate"], datetime.datetime)
    assert isinstance(unknown_dict_after["customPayload"]["rawBlob"], bytes)
    assert unknown_dict_after["customPayload"]["rawBlob"] == b"ios-organizer"


def unknown_dict_dict_equal(a: dict, b: dict) -> bool:
    return a == b


def test_deterministic_opaque_tokens() -> None:
    """Repeated pulls/decompiles of the same raw state yield identical tokens."""
    raw_state = _load_fixture("synthetic_edge_cases.json")
    layout1, store1 = decompile(raw_state)
    layout2, store2 = decompile(raw_state)

    assert layout1 == layout2
    assert store1 == store2
    for tok, payload in store1.items():
        assert compute_opaque_token(payload) == tok


@pytest.mark.parametrize(
    ("raw_input", "expected_name", "expected_bundle_id"),
    [
        ("Phone (com.apple.mobilephone)", "Phone", "com.apple.mobilephone"),
        ("com.apple.mobilephone", "com.apple.mobilephone", "com.apple.mobilephone"),
        ("pinterest", "pinterest", "pinterest"),
        ("Pinterest (pinterest)", "Pinterest", "pinterest"),
        ("Foo (Beta) (com.foo)", "Foo (Beta)", "com.foo"),
        ("App (v2) (Test) (io.example.app-v2)", "App (v2) (Test)", "io.example.app-v2"),
        ("   Foo (Beta) (com.foo)   ", "Foo (Beta)", "com.foo"),
        ("   com.bare.id   ", "com.bare.id", "com.bare.id"),
    ],
)
def test_parse_app_entry_cases(
    raw_input: str,
    expected_name: str,
    expected_bundle_id: str,
) -> None:
    name, bid = parse_app_entry(raw_input)
    assert name == expected_name
    assert bid == expected_bundle_id


@pytest.mark.parametrize(
    "bad_input",
    [
        "",
        "   ",
        "@widget:abc123:Weather",
        "App Without Closing Paren (com.foo",
        "Bare Words With Spaces And No Bundle ID",
        "Name (invalid bundle id with spaces)",
    ],
)
def test_parse_app_entry_rejects_malformed(bad_input: str) -> None:
    with pytest.raises(ValueError):
        parse_app_entry(bad_input)


def test_parse_opaque_entry() -> None:
    ref = parse_opaque_entry("  @widget:a1b2c3d4e5f6:Weather (medium)  ")
    assert ref.kind == "widget"
    assert ref.token == "a1b2c3d4e5f6"
    assert ref.description == "Weather (medium)"

    wref = parse_opaque_entry("@webclip:123456abcdef:Open Clock")
    assert wref.kind == "webclip"
    assert wref.token == "123456abcdef"
    assert wref.description == "Open Clock"


def test_backup_and_opaque_store_persistence(tmp_path: Path) -> None:
    from ios_organizer.backup import (
        list_backups,
        load_backup,
        load_opaque_store,
        save_opaque_store,
        write_backup,
    )

    raw_state = _load_fixture("synthetic_edge_cases.json")
    bdir = tmp_path / "backups"
    rec = write_backup(
        raw_state=raw_state,
        device_name="Test iPhone",
        udid="0000-1111",
        ios_version="TEST-OS",
        model="Example iPhone",
        product_type="EXAMPLE-PRODUCT",
        build_version="TEST-BUILD",
        backup_dir=bdir,
    )
    assert rec.path.exists()
    assert rec.meta_path.exists()

    listed = list_backups(bdir)
    assert len(listed) == 1
    assert listed[0].filename == rec.filename

    loaded_raw, loaded_rec = load_backup(rec.filename, bdir)
    assert loaded_rec.udid == "0000-1111"
    assert loaded_raw == raw_state

    _, opaque_store = decompile(raw_state)
    odir = tmp_path / ".opaque"
    save_opaque_store(opaque_store, odir)
    reloaded_store = load_opaque_store(odir)
    assert reloaded_store == opaque_store


def test_only_device_module_imports_pymobiledevice3() -> None:
    import ast

    src_dir = Path(__file__).parent.parent / "src" / "ios_organizer"
    for py_file in src_dir.glob("*.py"):
        if py_file.name == "device.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("pymobiledevice3"), (
                        f"{py_file.name} must not import pymobiledevice3"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("pymobiledevice3"), (
                    f"{py_file.name} must not import from pymobiledevice3"
                )


def test_round_trip_preserves_empty_pages_and_folder_page_boundaries() -> None:
    apps = [{"displayIdentifier": f"com.example.a{i}"} for i in range(10)]
    raw = [[], [], [{"listType": "folder", "displayName": "Work", "iconLists": [apps[:5], [], apps[5:]]}]]
    layout, store = decompile(raw)
    parsed, _ = parse_layout_yaml(dump_layout_yaml(layout))
    compiled = compile_layout(parsed, store)
    assert compiled[1] == []
    assert [len(page) for page in compiled[2][0]["iconLists"]] == [5, 0, 5]
    assert not diff_icon_states(raw, compiled).has_changes


def test_diff_detects_folder_repagination_and_empty_page_removal() -> None:
    apps = [{"displayIdentifier": f"com.example.a{i}"} for i in range(10)]
    original = [[], [{"listType": "folder", "displayName": "Work", "iconLists": [apps[:5], apps[5:]]}]]
    changed = [[], [{"listType": "folder", "displayName": "Work", "iconLists": [apps[:9], apps[9:]]}]]
    diff = diff_icon_states(original, changed)
    assert diff.has_changes
    assert any("pagination" in line.lower() for line in diff.lines)
    assert diff_icon_states([[], [], [apps[0]]], [[], [apps[0]]]).has_changes


def test_compile_rejects_duplicate_display_identifiers() -> None:
    layout = {"dock": [], "pages": [{"items": ["@widget:a:One", "@widget:b:Two"]}]}
    store = {key: {"iconType": "custom", "displayIdentifier": "same-uuid"} for key in ("a", "b")}
    with pytest.raises(ValueError, match="Duplicate displayIdentifier"):
        compile_layout(layout, store)


def test_compile_rejects_outdated_folder_page_sizes() -> None:
    layout = {"pages": [{"items": [{"folder": "Work", "page_sizes": [2], "apps": ["com.example.a"]}]}]}
    with pytest.raises(ValueError, match="page_sizes"):
        compile_layout(layout)
