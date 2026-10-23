import json
from pathlib import Path

from typer.testing import CliRunner

from ios_organizer.cli import app
from ios_organizer.onboarding import Stage, guide, render_inventory


def test_guide_has_stable_stages_and_requires_review():
    assert [stage.value for stage in Stage] == ["connect", "inventory", "preferences", "preview", "apply", "verify"]
    assert "wait for approval" in guide(Stage.preview)["done_when"]
    assert len(guide(Stage.preferences)["questions"]) == 5
    for stage in Stage:
        result = CliRunner().invoke(app, ["guide", "--stage", stage.value])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == guide(stage)
        assert data["developer_mode_required"] is False


def test_inventory_includes_every_location_and_preserves_folder_boundaries():
    layout = {
        "dock": ["Phone (com.example.phone)"],
        "pages": [{"page": 1, "items": []}, {"page": 2, "items": [
            "Loose (com.example.loose)",
            {"folder": "Work <script>", "apps": ["One (com.example.one)", "Two (com.example.two)"], "page_sizes": [1, 0, 1]},
            "@widget:w:Weather", "@webclip:b:Example Portal",
        ]}],
        "unassigned_apps": ["Library (com.example.library)", "Phone (com.example.phone)"],
    }
    report = render_inventory(layout, source="fictional.yaml")
    assert "5 unique app IDs" in report
    for label in ("Dock", "Empty page", "Page 2", "Folder page 2", "Folder page 3", "com.example.one", "com.example.two", "Weather", "Example Portal", "com.example.library"):
        assert label in report
    assert "<script>" not in report
    assert "&lt;script&gt;" in report
    assert "not a new phone scan" in report


def test_inventory_cli_is_offline_and_protects_source(tmp_path, monkeypatch):
    import ios_organizer.cli as cli
    monkeypatch.chdir(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Device access from offline command")
    monkeypatch.setattr(cli, "fetch_device_snapshot", forbidden)
    source = Path("layout.yaml")
    original = "schema_version: 1\ndock: []\npages: []\nunassigned_apps: ['Only Library (com.example.library)']\n"
    source.write_text(original)
    result = CliRunner().invoke(app, ["inventory", "--layout", str(source)])
    assert result.exit_code == 0, result.output
    assert "com.example.library" in Path("workspace/app_inventory.md").read_text()
    result = CliRunner().invoke(app, ["inventory", "--layout", str(source), "--output", str(source)])
    assert result.exit_code == 1
    assert source.read_text() == original
    assert not Path("backups").exists()
