"""Offline preview behavior and safe rendering of user-controlled labels."""

import json

import pytest
from typer.testing import CliRunner

from ios_organizer.cli import app
from ios_organizer.preview import render_layout_preview


def test_preview_preserves_labels_and_folder_pages_without_html_injection():
    name = '<script>alert("hello")</script>'
    layout = {"dock": ["Phone (com.example.phone)"], "pages": [
        {"items": []},
        {"items": [{"folder": name, "page_sizes": [1, 1], "apps": ["Alpha (com.example.a)", "Beta (com.example.b)"]},
                   "@widget:w:Weather", "@widget:unknown:Unknown"]},
    ], "unassigned_apps": ["Hidden (com.example.hidden)"]}
    html = render_layout_preview(layout, {"w": {"iconType": "custom", "gridSize": "medium"}}, title=name)
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert "Empty page · preserved" in html
    assert "Folder page 1" in html and "Folder page 2" in html
    assert 'grid-column:span 4;grid-row:span 2' in html
    assert "Unknown size · estimated" in html
    assert "App Library warning" in html
    assert "Content-Security-Policy" in html


def test_dock_only_layout_is_visible():
    html = render_layout_preview({"dock": ["Phone (com.example.phone)"]}, {})
    assert 'aria-label="Dock"' in html
    assert "Phone" in html


@pytest.mark.parametrize("command", ["preview", "validate"])
def test_local_commands_never_connect_to_device(tmp_path, monkeypatch, command):
    import ios_organizer.cli as cli
    monkeypatch.chdir(tmp_path)
    def unexpected(*args, **kwargs):
        raise AssertionError("Local command attempted device access")
    for name in ("fetch_device_snapshot", "get_raw_icon_state", "get_installed_apps", "set_raw_icon_state"):
        monkeypatch.setattr(cli, name, unexpected)
    path = tmp_path / "layout.yaml"
    path.write_text("schema_version: 1\ndock: []\npages:\n  - items: ['@widget:w:Weather']\n")
    store = tmp_path / "objects"
    store.mkdir()
    (store / "w.json").write_text(json.dumps({"iconType": "custom", "displayIdentifier": "weather", "gridSize": "medium"}))
    result = CliRunner().invoke(app, [command, "--layout", str(path), "--opaque-dir", str(store)])
    assert result.exit_code == 0, result.output
    if command == "preview":
        html = (tmp_path / "workspace/layout_preview.html").read_text()
        assert "Weather" in html
        assert 'grid-column:span 4;grid-row:span 2' in html
    assert not (tmp_path / "backups").exists()


def test_preview_refuses_to_overwrite_source_layout(tmp_path):
    path = tmp_path / "layout.yaml"
    original = "schema_version: 1\npages: []\n"
    path.write_text(original)
    result = CliRunner().invoke(app, ["preview", "--layout", str(path), "--output", str(path)])
    assert result.exit_code == 1
    assert path.read_text() == original


@pytest.mark.parametrize("command", ["preview", "validate"])
def test_local_commands_block_missing_widget_payload(tmp_path, command):
    path = tmp_path / "layout.yaml"
    path.write_text("schema_version: 1\npages:\n  - items: ['@widget:missing:Missing']\n")
    result = CliRunner().invoke(app, [command, "--layout", str(path), "--opaque-dir", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "Unknown opaque token" in result.output
