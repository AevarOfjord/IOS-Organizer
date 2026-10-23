import pytest

from ios_organizer.validation import validate_layout


@pytest.mark.parametrize("second_token", ["a", "b"])
def test_duplicate_opaque_references_or_identifiers_are_blocked(second_token):
    store = {key: {"iconType": "custom", "displayIdentifier": "same-uuid", "gridSize": "small"} for key in ("a", "b")}
    layout = {"schema_version": 1, "dock": [], "pages": [{"items": ["@widget:a:One", f"@widget:{second_token}:Two"]}]}
    result = validate_layout(layout, opaque_store=store)
    assert not result.is_valid
    assert any("Duplicate" in error for error in result.errors)


def test_distinct_app_bookmarks_for_same_bundle_are_allowed():
    store = {key: {"iconType": "app", "displayIdentifier": key, "bundleIdentifier": "com.example.a"} for key in ("a", "b")}
    layout = {"schema_version": 1, "dock": ["com.example.a"], "pages": [{"items": ["@widget:a:One", "@widget:b:Two"]}]}
    assert validate_layout(layout, opaque_store=store).is_valid


@pytest.mark.parametrize("sizes", [[2], [-1, 2], [True], "1", [10]])
def test_invalid_folder_page_sizes_are_blocked(sizes):
    layout = {"schema_version": 1, "pages": [{"items": [{"folder": "Work", "page_sizes": sizes, "apps": ["com.example.a"]}]}]}
    result = validate_layout(layout)
    assert not result.is_valid
    assert any("page_sizes" in error for error in result.errors)


def test_decompiled_empty_folder_remains_valid_with_drop_warning():
    from ios_organizer.compiler import decompile
    layout, store = decompile([[], [{"listType": "folder", "displayName": "Empty", "iconLists": []}]])
    result = validate_layout(layout, opaque_store=store)
    assert result.is_valid
    assert any("Empty folder" in warning for warning in result.warnings)
