"""Unit tests for ios_organizer.organizer (auto-sort & categories.yaml)."""

from __future__ import annotations

from ios_organizer.organizer import (
    auto_sort_layout,
    dump_categories_yaml,
    parse_categories_yaml,
)


def _sample_layout() -> dict:
    return {
        "schema_version": 1,
        "dock": [
            "Phone (com.apple.mobilephone)",
            "Safari (com.apple.mobilesafari)",
        ],
        "pages": [
            {
                "page": 1,
                "items": [
                    "@widget:w_med:Weather (medium)",
                    "Zebra (com.example.zebra)",
                    "Alpha (com.example.alpha)",
                    "Chase (com.chase.sig)",
                    {
                        "folder": "Existing",
                        "apps": [
                            "Delta (com.example.delta)",
                            "Beta (com.example.beta)",
                        ],
                    },
                ],
            }
        ],
        "unassigned_apps": [
            "Charlie (com.example.charlie)",
        ],
    }


def test_auto_sort_alpha_mode_is_deterministic_and_preserves_pinned_items() -> None:
    layout = _sample_layout()
    opaque_store = {"w_med": {"iconType": "custom", "gridSize": "medium"}}

    res1 = auto_sort_layout(layout, mode="alpha", opaque_store=opaque_store)
    res2 = auto_sort_layout(layout, mode="alpha", opaque_store=opaque_store)

    assert res1 == res2
    assert res1["dock"] == layout["dock"]
    assert res1["unassigned_apps"] == ["Charlie (com.example.charlie)"]

    page_1_items = res1["pages"][0]["items"]
    # Widget and Existing folder stay pinned first, with Existing's apps sorted alphabetically
    assert page_1_items[0] == "@widget:w_med:Weather (medium)"
    assert page_1_items[1] == {
        "folder": "Existing",
        "apps": ["Beta (com.example.beta)", "Delta (com.example.delta)"],
    }
    # Loose apps sorted alphabetically: Alpha, Chase, Zebra
    assert page_1_items[2:] == [
        "Alpha (com.example.alpha)",
        "Chase (com.chase.sig)",
        "Zebra (com.example.zebra)",
    ]


def test_auto_sort_category_mode_priority_and_determinism() -> None:
    layout = _sample_layout()
    categories = {
        "com.chase.sig": "Finance",
        "com.example.alpha": "Productivity",
    }
    installed_apps = {
        "com.chase.sig": {"name": "Chase", "type": "User", "genre": "Utilities"},
        "com.example.alpha": {"name": "Alpha", "type": "User", "genre": None},
        "com.example.zebra": {"name": "Zebra", "type": "System", "genre": "Games"},
        "com.example.charlie": {"name": "Charlie", "type": "User", "genre": None},
    }

    res1 = auto_sort_layout(
        layout,
        mode="category",
        categories=categories,
        installed_apps=installed_apps,
        include_unassigned=True,
    )
    res2 = auto_sort_layout(
        layout,
        mode="category",
        categories=categories,
        installed_apps=installed_apps,
        include_unassigned=True,
    )

    assert res1 == res2
    assert res1["unassigned_apps"] == []

    page_1_items = res1["pages"][0]["items"]
    # Pinned widget & Existing folder first
    assert page_1_items[0] == "@widget:w_med:Weather (medium)"
    assert page_1_items[1]["folder"] == "Existing"

    # Category folders in order: Finance, Games (from genre), Productivity, Other (last)
    created_folders = page_1_items[2:]
    assert created_folders == [
        {"folder": "Finance", "apps": ["Chase (com.chase.sig)"]},
        {"folder": "Games", "apps": ["Zebra (com.example.zebra)"]},
        {"folder": "Productivity", "apps": ["Alpha (com.example.alpha)"]},
        {"folder": "Other", "apps": ["Charlie (com.example.charlie)"]},
    ]


def test_auto_sort_reorganize_folders_and_page_packing() -> None:
    layout = _sample_layout()
    # Use a small page_capacity (e.g. 10 slots) where medium widget takes 8 slots,
    # leaving only 2 slots on page 1 so remaining items spill to page 2.
    opaque_store = {"w_med": {"iconType": "custom", "gridSize": "medium"}}  # 8 slots
    res = auto_sort_layout(
        layout,
        mode="alpha",
        opaque_store=opaque_store,
        reorganize_folders=True,
        page_capacity=10,
    )

    # All 5 apps (Alpha, Beta, Chase, Delta, Zebra) are loose and sorted alphabetically.
    # Page 1 has the 8-slot widget + first 2 apps (total 10 slots).
    # Page 2 has the remaining 3 apps.
    assert len(res["pages"]) == 2
    assert res["pages"][0]["items"] == [
        "@widget:w_med:Weather (medium)",
        "Alpha (com.example.alpha)",
        "Beta (com.example.beta)",
    ]
    assert res["pages"][1]["items"] == [
        "Chase (com.chase.sig)",
        "Delta (com.example.delta)",
        "Zebra (com.example.zebra)",
    ]


def test_categories_yaml_round_trip() -> None:
    installed_apps = {
        "com.chase.sig": {"name": "Chase", "type": "User", "genre": None},
        "com.apple.weather": {"name": "Weather", "type": "System", "genre": "Weather"},
    }
    text = dump_categories_yaml(
        installed_apps,
        existing_categories={"com.chase.sig": "Finance & Banking"},
    )
    parsed = parse_categories_yaml(text)
    assert parsed == {"com.chase.sig": "Finance & Banking"}


def test_cli_workflows_offline_with_mocked_device(tmp_path, monkeypatch) -> None:
    """Verify status, pull, layout.new.yaml protection, diff, push, restore, and auto-sort offline."""
    import copy
    from typer.testing import CliRunner
    import ios_organizer.cli as cli_mod
    from ios_organizer.device import DeviceInfo

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    mock_info = DeviceInfo(
        name="Mock iPhone",
        model="Example iPhone",
        product_type="EXAMPLE-PRODUCT",
        udid="MOCK-UDID-1234",
        ios_version="TEST-OS",
        build_version="TEST-BUILD",
    )
    mock_apps = {
        "com.apple.mobilephone": {"name": "Phone", "type": "System", "genre": "Utilities"},
        "com.apple.camera": {"name": "Camera", "type": "System", "genre": "Photography"},
        "com.apple.mobilecal": {"name": "Calendar", "type": "System", "genre": "Productivity"},
    }
    device_state = [
        [{"bundleIdentifier": "com.apple.mobilephone", "displayIdentifier": "com.apple.mobilephone", "displayName": "Phone"}],
        [
            {"bundleIdentifier": "com.apple.camera", "displayIdentifier": "com.apple.camera", "displayName": "Camera"},
            {"bundleIdentifier": "com.apple.mobilecal", "displayIdentifier": "com.apple.mobilecal", "displayName": "Calendar"},
        ],
    ]

    monkeypatch.setattr(
        cli_mod,
        "fetch_device_snapshot",
        lambda udid=None: (mock_info, copy.deepcopy(device_state), mock_apps),
    )
    monkeypatch.setattr(
        cli_mod,
        "get_raw_icon_state",
        lambda udid=None: copy.deepcopy(device_state),
    )
    monkeypatch.setattr(
        cli_mod,
        "get_installed_apps",
        lambda udid=None: mock_apps,
    )

    def _mock_set_state(new_state, udid=None):
        device_state.clear()
        device_state.extend(copy.deepcopy(new_state))

    monkeypatch.setattr(cli_mod, "set_raw_icon_state", _mock_set_state)

    # 1. status & pull
    res_status = runner.invoke(cli_mod.app, ["status"])
    assert res_status.exit_code == 0
    assert "Mock iPhone" in res_status.output

    res_pull = runner.invoke(cli_mod.app, ["pull"])
    assert res_pull.exit_code == 0
    layout_path = tmp_path / "workspace" / "layout.yaml"
    assert layout_path.exists()

    # 2. diff & push --dry-run on unmodified layout
    res_diff = runner.invoke(cli_mod.app, ["diff"])
    assert res_diff.exit_code == 0
    assert "No changes detected" in res_diff.output

    res_dry = runner.invoke(cli_mod.app, ["push", "--dry-run"])
    assert res_dry.exit_code == 0
    assert (tmp_path / "workspace" / "compiled_preview.json").exists()

    # 3. Swap two apps on page 1 -> pull writes layout.new.yaml instead of overwriting layout.yaml
    edited_text = layout_path.read_text(encoding="utf-8").replace(
        '      - "Camera (com.apple.camera)"\n      - "Calendar (com.apple.mobilecal)"',
        '      - "Calendar (com.apple.mobilecal)"\n      - "Camera (com.apple.camera)"',
    )
    layout_path.write_text(edited_text, encoding="utf-8")

    res_pull_protected = runner.invoke(cli_mod.app, ["pull"])
    assert res_pull_protected.exit_code == 0
    assert (tmp_path / "workspace" / "layout.new.yaml").exists()

    # 4. Push swapped layout -> verify device_state updated
    res_push_swap = runner.invoke(cli_mod.app, ["push", "--yes"])
    assert res_push_swap.exit_code == 0
    assert "Verification passed" in res_push_swap.output
    assert [it["bundleIdentifier"] for it in device_state[1]] == [
        "com.apple.mobilecal",
        "com.apple.camera",
    ]

    # 5. Restore original backup -> verify device_state restored to original order
    backups = sorted((tmp_path / "backups").glob("backup_*.json"))
    first_backup_name = [b.name for b in backups if not b.name.endswith(".meta.json")][0]
    res_restore = runner.invoke(cli_mod.app, ["restore", first_backup_name, "--yes"])
    assert res_restore.exit_code == 0
    assert "Restore verified" in res_restore.output
    assert [it["bundleIdentifier"] for it in device_state[1]] == [
        "com.apple.camera",
        "com.apple.mobilecal",
    ]
