"""Regenerate public images from fictional examples only; never reads a phone."""
from pathlib import Path

from ios_organizer.compiler import parse_layout_yaml
from ios_organizer.preview_image import render_layout_png

ROOT = Path(__file__).resolve().parents[1]


def main():
    assets = ROOT / "docs/assets"
    assets.mkdir(parents=True, exist_ok=True)
    for source, target, spatial, title in (
        ("layout.yaml", "layout-preview.png", False, "A place for every app"),
        ("spatial-layout.yaml", "widget-preview.png", True, "Plan your main pages"),
    ):
        layout, _ = parse_layout_yaml((ROOT / "examples" / source).read_text())
        (assets / target).write_bytes(render_layout_png(layout, {}, spatial=spatial, title=title))
    print("Generated fictional demo images in docs/assets")


if __name__ == "__main__":
    main()
