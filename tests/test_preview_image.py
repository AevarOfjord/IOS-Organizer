from io import BytesIO

from PIL import Image
import pytest
from typer.testing import CliRunner

from ios_organizer.cli import app
from ios_organizer.preview_image import render_layout_png


def test_png_is_deterministic_and_places_spatial_app_below_empty_rows():
    plan = {"spatial_version": 1, "grid": {"columns": 4, "rows": 6}, "pages": [{"page": 1, "items": [
        {"app": "Example Mail (com.example.mail)", "row": 5, "column": 1},
    ]}]}
    png = render_layout_png(plan, {}, spatial=True)
    assert png == render_layout_png(plan, {}, spatial=True)
    image = Image.open(BytesIO(png))
    assert image.format == "PNG"
    # The light Mail icon is in row five, while its row-one cell stays wallpaper.
    # Export is rendered at 2x; sample inside the icon, away from its glyph.
    assert all(c < 100 for c in image.getpixel((160, 480)))
    assert all(c > 200 for c in image.getpixel((160, 1200)))


def test_png_rejects_overlap_and_bulk_overflow():
    with pytest.raises(ValueError, match="overlapping"):
        render_layout_png({"spatial_version": 1, "grid": {"columns": 4, "rows": 6}, "pages": [{"page": 1, "items": [
            {"app": "com.example.a", "row": 1, "column": 1},
            {"empty": True, "row": 1, "column": 1},
        ]}]}, {}, spatial=True)
    with pytest.raises(ValueError, match="overflows"):
        render_layout_png({"pages": [{"items": [f"com.example.app{i}" for i in range(25)]}]}, {})


@pytest.mark.parametrize("kind", ["bulk", "spatial"])
def test_png_cli_exports_without_device_access(tmp_path, monkeypatch, kind):
    import ios_organizer.cli as cli
    monkeypatch.chdir(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview attempted a phone connection")
    monkeypatch.setattr(cli, "fetch_device_snapshot", forbidden)
    source = tmp_path / "design.yaml"
    if kind == "bulk":
        source.write_text("schema_version: 1\npages:\n  - items: ['Example (com.example.app)']\n")
        command = ["preview", "--layout", str(source), "--output"]
    else:
        source.write_text("spatial_version: 1\ngrid: {columns: 4, rows: 6}\npages:\n  - page: 1\n    items:\n      - app: Example (com.example.app)\n        row: 5\n        column: 1\n")
        command = ["ui", "plan", "--layout", str(source), "--preview"]
    target = tmp_path / "preview.png"
    result = CliRunner().invoke(app, [*command, str(target)])
    assert result.exit_code == 0, result.output
    with Image.open(target) as image:
        image.verify()
    result = CliRunner().invoke(app, [*command, str(tmp_path / "bad.txt")])
    assert result.exit_code == 1
    assert not (tmp_path / "bad.txt").exists()
    assert not (tmp_path / "backups").exists()
