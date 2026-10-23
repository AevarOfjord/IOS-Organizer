"""Typer CLI entrypoints for ios-organizer."""

from __future__ import annotations

import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ios_organizer import __version__
from ios_organizer.backup import (
    BackupRecord,
    list_backups,
    load_backup,
    load_opaque_store,
    save_opaque_store,
    write_backup,
)
from ios_organizer.compiler import (
    compile_layout,
    decompile,
    diff_icon_states,
    dump_layout_yaml,
    parse_layout_yaml,
    to_json_compatible,
)
from ios_organizer.device import (
    IOSDeviceError,
    fetch_device_snapshot,
    get_installed_apps,
    get_raw_icon_state,
    set_raw_icon_state,
)
from ios_organizer.organizer import (
    auto_sort_layout,
    dump_categories_yaml,
    parse_categories_yaml,
)
from ios_organizer.validation import ValidationResult, validate_layout
from ios_organizer.preview import render_layout_preview
from ios_organizer.preview_image import render_layout_png
from ios_organizer.private_io import private_directory, secure_runtime_roots, write_private
from ios_organizer.onboarding import Stage, guide as workflow_guide, render_inventory
from ios_organizer.ui_cli import app as ui_app

app = typer.Typer(
    name="ios-organizer",
    help="Two-way iPhone Home Screen layout manager for macOS.",
    add_completion=False,
    no_args_is_help=True,
)
app.add_typer(ui_app, name='ui')

console = Console()
err_console = Console(stderr=True)

DEFAULT_WORKSPACE_DIR = Path("workspace")
DEFAULT_LAYOUT_PATH = DEFAULT_WORKSPACE_DIR / "layout.yaml"
DEFAULT_NEW_LAYOUT_PATH = DEFAULT_WORKSPACE_DIR / "layout.new.yaml"
DEFAULT_SORTED_LAYOUT_PATH = DEFAULT_WORKSPACE_DIR / "layout_sorted.yaml"
DEFAULT_CATEGORIES_PATH = DEFAULT_WORKSPACE_DIR / "categories.yaml"
DEFAULT_OPAQUE_DIR = DEFAULT_WORKSPACE_DIR / ".opaque"
DEFAULT_LAST_PULL_HASH_PATH = DEFAULT_OPAQUE_DIR / ".last_pull_sha256"
DEFAULT_PREVIEW_PATH = DEFAULT_WORKSPACE_DIR / "compiled_preview.json"
DEFAULT_BACKUP_DIR = Path("backups")
DEFAULT_HTML_PREVIEW_PATH = DEFAULT_WORKSPACE_DIR / "layout_preview.html"


@app.callback()
def protect_local_phone_data(ctx: typer.Context) -> None:
    """Restrict existing private runtime artifacts before commands access them."""
    if ctx.invoked_subcommand in (None, "guide"):
        return
    try:
        secure_runtime_roots()
    except OSError as exc:
        err_console.print(f"Private storage error: {exc}", markup=False)
        raise typer.Exit(1)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _format_age(iso_timestamp: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(iso_timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = now - dt
        seconds = max(0, int(delta.total_seconds()))
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s ago"
        if seconds < 86400:
            return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h ago"
    except Exception:
        return iso_timestamp


def _load_categories_if_present(path: Path = DEFAULT_CATEGORIES_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return parse_categories_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _print_validation(validation: ValidationResult) -> None:
    for warning in validation.warnings:
        console.print(f"[yellow][WARNING][/yellow] {warning}")
    for error in validation.errors:
        err_console.print(f"[bold red][ERROR][/bold red] {error}")


def _print_diff(diff_result: Any, title: str = "Layout Diff (Device -> Target)") -> None:
    if not diff_result.has_changes:
        console.print(
            Panel(
                "[green]No changes detected — target layout is identical to device state.[/green]",
                title=title,
            )
        )
        return

    lines: list[str] = []
    if diff_result.dock_changes:
        lines.append("[bold cyan]Dock:[/bold cyan]")
        for line in diff_result.dock_changes:
            lines.append(f"  {line}")
    if diff_result.page_changes:
        lines.append("[bold cyan]Pages:[/bold cyan]")
        for line in diff_result.page_changes:
            lines.append(f"  {line}")
    if diff_result.folder_changes:
        lines.append("[bold cyan]Folders:[/bold cyan]")
        for line in diff_result.folder_changes:
            lines.append(f"  {line}")
    if diff_result.moved_apps:
        lines.append("[bold cyan]Item Movements:[/bold cyan]")
        for line in diff_result.moved_apps[:60]:
            lines.append(f"  {line}")
        if len(diff_result.moved_apps) > 60:
            lines.append(f"  ... and {len(diff_result.moved_apps) - 60} more item movement(s)")

    console.print(Panel("\n".join(lines), title=title))


def _warn_app_library(raw_state: list[Any], installed_apps: dict[str, dict[str, Any]]) -> None:
    layout, _ = decompile(raw_state, installed_apps=installed_apps)
    omitted = layout["unassigned_apps"]
    if omitted:
        console.print(Panel(
            f"[bold yellow]{len(omitted)} installed app(s) are omitted from this layout.[/bold yellow]\n"
            "On the tested iOS version, SpringBoard places omitted apps into available Home Screen slots. "
            "App Library-only placement cannot be preserved by this push.\n"
            + "\n".join(f"• {entry}" for entry in omitted[:8])
            + (f"\n… and {len(omitted) - 8} more" if len(omitted) > 8 else ""),
            title="App Library warning", border_style="yellow",
        ))


def _verify_write(
    expected: list[Any], installed_apps: dict[str, dict[str, Any]],
    udid: str, pre_backup: BackupRecord, operation: str,
) -> None:
    recovery = f"Recover with: ios-organizer restore {pre_backup.path} --udid {udid}"
    try:
        actual = get_raw_icon_state(udid=udid)
    except IOSDeviceError as exc:
        err_console.print(
            f"[bold red]Verification Re-pull Error:[/bold red] {exc}\n"
            f"The {operation.lower()} was sent, but the resulting layout could not be verified.\n{recovery}"
        )
        raise typer.Exit(code=1)
    result = diff_icon_states(expected, actual, installed_apps=installed_apps)
    if result.has_changes:
        err_console.print(
            f"[bold yellow]{operation} verification mismatch:[/bold yellow] "
            f"The device layout differs from the requested layout.\n{recovery}"
        )
        _print_diff(result, title=f"Post-{operation} Differences")
        raise typer.Exit(code=2)
    console.print(
        f"[bold green]{'Verification passed' if operation == 'Push' else 'Restore verified'}:[/bold green] "
        "Layout order, folder pagination, and opaque objects match. Transient app metadata is ignored."
    )


def _read_local_layout(layout_file: Path, opaque_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        layout, _ = parse_layout_yaml(layout_file.read_text(encoding="utf-8"))
        return layout, load_opaque_store(opaque_dir)
    except (OSError, ValueError) as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1)


@app.command()
def validate(
    layout_file: Annotated[Path, typer.Option("--layout", help="Layout YAML to validate offline.")] = DEFAULT_LAYOUT_PATH,
    opaque_dir: Annotated[Path, typer.Option("--opaque-dir", help="Opaque object directory.")] = DEFAULT_OPAQUE_DIR,
) -> None:
    """Check local schema, references, identities, and compilation without a phone."""
    layout, store = _read_local_layout(layout_file, opaque_dir)
    result = validate_layout(layout, opaque_store=store)
    _print_validation(result)
    if not result.is_valid:
        raise typer.Exit(code=1)
    try:
        compile_layout(layout, opaque_store=store)
    except (KeyError, ValueError) as exc:
        err_console.print(f"[bold red]Compile Error:[/bold red] {exc}")
        raise typer.Exit(code=1)
    console.print("[green]Local validation passed.[/green] Installed-app availability requires a connected-device diff.")


@app.command()
def preview(
    layout_file: Annotated[Path, typer.Option("--layout", help="Layout YAML to preview offline.")] = DEFAULT_LAYOUT_PATH,
    output_file: Annotated[Path, typer.Option("--output", help="Destination HTML or PNG file.")] = DEFAULT_HTML_PREVIEW_PATH,
    opaque_dir: Annotated[Path, typer.Option("--opaque-dir", help="Opaque object directory.")] = DEFAULT_OPAQUE_DIR,
    open_browser: Annotated[bool, typer.Option("--open", help="Open the preview in your browser.")] = False,
    columns: Annotated[int, typer.Option("--columns", min=1, max=12)] = 4,
    rows: Annotated[int, typer.Option("--rows", min=1, max=12)] = 6,
) -> None:
    """Create a self-contained visual preview of pages, folders, widgets, and dock."""
    layout, store = _read_local_layout(layout_file, opaque_dir)
    result = validate_layout(layout, opaque_store=store, page_capacity=columns * rows)
    _print_validation(result)
    if not result.is_valid:
        raise typer.Exit(code=1)
    try:
        compile_layout(layout, opaque_store=store)
        if output_file.suffix.lower() == '.png':
            content = render_layout_png(layout, store, title=layout_file.name, columns=columns, rows=rows)
        elif output_file.suffix.lower() in {'.html', '.htm'}:
            content = render_layout_preview(layout, store, title=layout_file.name, columns=columns, rows=rows,
                                           warnings=result.warnings)
        else:
            raise ValueError('Preview output must be HTML or PNG.')
        if output_file.resolve() == layout_file.resolve():
            raise ValueError("Preview output must differ from the input layout.")
        write_private(output_file, content)
    except (OSError, KeyError, ValueError) as exc:
        err_console.print(f"[bold red]Preview Error:[/bold red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]Visual preview saved to:[/green] {output_file.resolve()}")
    if open_browser:
        typer.launch(output_file.resolve().as_uri())


@app.command()
def guide(
    stage: Annotated[Stage, typer.Option("--stage", help="Current onboarding stage.")] = Stage.connect,
) -> None:
    """Print the shared JSON conversation guide without connecting to a phone."""
    print(json.dumps(workflow_guide(stage), ensure_ascii=False, indent=2))


@app.command()
def inventory(
    layout_file: Annotated[Path, typer.Option("--layout", help="Freshly pulled layout to list.")] = DEFAULT_LAYOUT_PATH,
    output_file: Annotated[Path, typer.Option("--output", help="Destination Markdown inventory.")] = DEFAULT_WORKSPACE_DIR / "app_inventory.md",
    opaque_dir: Annotated[Path, typer.Option("--opaque-dir")] = DEFAULT_OPAQUE_DIR,
) -> None:
    """List all apps, folders, and additional icons in a local layout. No device access."""
    layout, store = _read_local_layout(layout_file, opaque_dir)
    result = validate_layout(layout, opaque_store=store)
    _print_validation(result)
    if not result.is_valid:
        raise typer.Exit(code=1)
    try:
        compile_layout(layout, opaque_store=store)
        if output_file.resolve() == layout_file.resolve():
            raise ValueError("Inventory output must differ from the input layout.")
        write_private(output_file, render_inventory(layout, source=str(layout_file)))
    except (OSError, KeyError, ValueError) as exc:
        err_console.print(f"Inventory Error: {exc}", markup=False)
        raise typer.Exit(code=1)
    console.print(f"Inventory saved to: {output_file.resolve()}", markup=False)


@app.command()
def status(
    udid: Annotated[
        Optional[str],
        typer.Option("--udid", help="Target device UDID if multiple devices are attached."),
    ] = None,
) -> None:
    """Check USB connection and display device info, app counts, and last pull age."""
    try:
        device_info, raw_state, installed_apps = fetch_device_snapshot(udid=udid)
    except IOSDeviceError as exc:
        err_console.print(f"[bold red]Connection Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    layout_dict, _ = decompile(raw_state, installed_apps=installed_apps)
    page_count = len(layout_dict.get("pages") or [])
    unassigned_count = len(layout_dict.get("unassigned_apps") or [])
    visible_apps_count = max(0, len(installed_apps) - unassigned_count)

    backups = list_backups(DEFAULT_BACKUP_DIR)
    if backups:
        latest = backups[0]
        last_pull_str = f"{latest.filename} ({_format_age(latest.created_at)})"
    else:
        last_pull_str = "Never (no backups in backups/)"

    table = Table(title="iOS Device & Workspace Status", show_header=False, box=None)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value")
    table.add_row("Device Name", device_info.name)
    table.add_row("Model", f"{device_info.model} ({device_info.product_type})")
    table.add_row("UDID", device_info.udid)
    table.add_row("iOS Version", f"{device_info.ios_version} (Build {device_info.build_version})")
    table.add_row("Home Screen Pages", str(page_count))
    table.add_row("Visible Apps (Home Screen)", str(visible_apps_count))
    table.add_row("App Library-Only Apps", str(unassigned_count))
    table.add_row("Total User-Facing Apps", str(len(installed_apps)))
    table.add_row("Last Pull / Backup", last_pull_str)

    console.print(Panel(table, title=f"ios-organizer v{__version__}", expand=False))


@app.command()
def pull(
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Overwrite workspace/layout.yaml even if it has local modifications.",
        ),
    ] = False,
    udid: Annotated[
        Optional[str],
        typer.Option("--udid", help="Target device UDID if multiple devices are attached."),
    ] = None,
) -> None:
    """Backup raw state, extract opaque tokens, and write workspace/layout.yaml."""
    try:
        device_info, raw_state, installed_apps = fetch_device_snapshot(udid=udid)
    except IOSDeviceError as exc:
        err_console.print(f"[bold red]Connection Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    backup_rec = write_backup(
        raw_state=raw_state,
        device_name=device_info.name,
        udid=device_info.udid,
        ios_version=device_info.ios_version,
        model=device_info.model,
        product_type=device_info.product_type,
        build_version=device_info.build_version,
        backup_dir=DEFAULT_BACKUP_DIR,
    )
    console.print(f"[green]Backed up raw icon state to:[/green] {backup_rec.path}")

    layout_dict, opaque_store = decompile(raw_state, installed_apps=installed_apps)
    save_opaque_store(opaque_store, DEFAULT_OPAQUE_DIR)
    console.print(
        f"[green]Extracted {len(opaque_store)} opaque element(s) to:[/green] {DEFAULT_OPAQUE_DIR}"
    )

    categories = _load_categories_if_present(DEFAULT_CATEGORIES_PATH)
    yaml_text = dump_layout_yaml(
        layout_dict,
        backup_filename=backup_rec.filename,
        device_name=device_info.name,
        ios_version=device_info.ios_version,
        categories=categories,
        installed_apps=installed_apps,
    )

    private_directory(DEFAULT_WORKSPACE_DIR)
    is_modified = False
    if DEFAULT_LAYOUT_PATH.exists() and not overwrite:
        current_sha = _file_sha256(DEFAULT_LAYOUT_PATH)
        last_pull_sha = (
            DEFAULT_LAST_PULL_HASH_PATH.read_text(encoding="utf-8").strip()
            if DEFAULT_LAST_PULL_HASH_PATH.exists()
            else None
        )
        if last_pull_sha != current_sha:
            is_modified = True

    if is_modified:
        write_private(DEFAULT_NEW_LAYOUT_PATH, yaml_text, encoding="utf-8")
        console.print(
            f"[yellow]Notice:[/yellow] {DEFAULT_LAYOUT_PATH} has local modifications and was not overwritten.\n"
            f"Wrote newly pulled layout to [bold]{DEFAULT_NEW_LAYOUT_PATH}[/bold] "
            "(pass --overwrite to replace workspace/layout.yaml)."
        )
    else:
        write_private(DEFAULT_LAYOUT_PATH, yaml_text, encoding="utf-8")
        private_directory(DEFAULT_OPAQUE_DIR)
        write_private(DEFAULT_LAST_PULL_HASH_PATH,
            _file_sha256(DEFAULT_LAYOUT_PATH) + "\n",
            encoding="utf-8",
        )
        console.print(f"[bold green]Wrote clean layout DSL to:[/bold green] {DEFAULT_LAYOUT_PATH}")


@app.command()
def diff(
    layout_file: Annotated[
        Path,
        typer.Option("--layout", help="Path to layout YAML file to compare."),
    ] = DEFAULT_LAYOUT_PATH,
    udid: Annotated[
        Optional[str],
        typer.Option("--udid", help="Target device UDID if multiple devices are attached."),
    ] = None,
) -> None:
    """Compile workspace/layout.yaml and show a human-readable diff against the device."""
    if not layout_file.exists():
        err_console.print(
            f"[bold red]Error:[/bold red] Layout file not found at {layout_file}. "
            "Run `python -m ios_organizer pull` first."
        )
        raise typer.Exit(code=1)

    try:
        layout_data, header_meta = parse_layout_yaml(layout_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    try:
        device_info, current_raw, installed_apps = fetch_device_snapshot(udid=udid)
    except IOSDeviceError as exc:
        err_console.print(f"[bold red]Connection Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    opaque_store = load_opaque_store(DEFAULT_OPAQUE_DIR)
    source_backup_raw = None
    if header_meta.get("backup_file"):
        try:
            source_backup_raw, _ = load_backup(str(header_meta["backup_file"]), DEFAULT_BACKUP_DIR)
        except FileNotFoundError:
            source_backup_raw = None

    validation = validate_layout(
        layout_data,
        installed_apps=installed_apps,
        opaque_store=opaque_store,
        dock_capacity=device_info.dock_capacity,
        page_capacity=device_info.page_capacity,
        folder_page_capacity=device_info.folder_page_capacity,
        source_backup_raw=source_backup_raw,
        current_device_raw=current_raw,
    )
    _print_validation(validation)

    try:
        compiled_raw = compile_layout(
            layout_data,
            opaque_store=opaque_store,
            installed_apps=installed_apps,
            folder_page_size=device_info.folder_page_capacity,
        )
    except (KeyError, ValueError) as exc:
        err_console.print(f"[bold red]Compile Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    diff_result = diff_icon_states(current_raw, compiled_raw, installed_apps=installed_apps)
    _print_diff(diff_result, title=f"Diff: {device_info.name} vs {layout_file}")


@app.command()
def push(
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Stop after diff and write workspace/compiled_preview.json without modifying device.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Override validation errors and push anyway."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip interactive confirmation prompt."),
    ] = False,
    layout_file: Annotated[
        Path,
        typer.Option("--layout", help="Path to layout YAML file to push."),
    ] = DEFAULT_LAYOUT_PATH,
    udid: Annotated[
        Optional[str],
        typer.Option("--udid", help="Target device UDID if multiple devices are attached."),
    ] = None,
) -> None:
    """Backup current state, validate, compile, show diff, push to iPhone, and verify."""
    if not layout_file.exists():
        err_console.print(
            f"[bold red]Error:[/bold red] Layout file not found at {layout_file}. "
            "Run `python -m ios_organizer pull` first."
        )
        raise typer.Exit(code=1)

    try:
        layout_data, header_meta = parse_layout_yaml(layout_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    try:
        device_info, current_raw, installed_apps = fetch_device_snapshot(udid=udid)
    except IOSDeviceError as exc:
        err_console.print(f"[bold red]Connection Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    # 1. Always create a fresh backup first
    pre_backup = write_backup(
        raw_state=current_raw,
        device_name=device_info.name,
        udid=device_info.udid,
        ios_version=device_info.ios_version,
        model=device_info.model,
        product_type=device_info.product_type,
        build_version=device_info.build_version,
        backup_dir=DEFAULT_BACKUP_DIR,
    )
    console.print(f"[green]Pre-push backup saved to:[/green] {pre_backup.path}")

    # 2. Validate
    opaque_store = load_opaque_store(DEFAULT_OPAQUE_DIR)
    source_backup_raw = None
    if header_meta.get("backup_file"):
        try:
            source_backup_raw, _ = load_backup(str(header_meta["backup_file"]), DEFAULT_BACKUP_DIR)
        except FileNotFoundError:
            source_backup_raw = None

    validation = validate_layout(
        layout_data,
        installed_apps=installed_apps,
        opaque_store=opaque_store,
        dock_capacity=device_info.dock_capacity,
        page_capacity=device_info.page_capacity,
        folder_page_capacity=device_info.folder_page_capacity,
        source_backup_raw=source_backup_raw,
        current_device_raw=current_raw,
    )
    _print_validation(validation)

    if not validation.is_valid:
        if not force:
            err_console.print(
                "[bold red]Push blocked due to validation errors.[/bold red] "
                "Fix the errors above or pass --force to override."
            )
            raise typer.Exit(code=1)
        console.print(
            "[bold yellow]Warning:[/bold yellow] Overriding validation errors because --force was passed."
        )

    # 3. Compile
    try:
        compiled_raw = compile_layout(
            layout_data,
            opaque_store=opaque_store,
            installed_apps=installed_apps,
            folder_page_size=device_info.folder_page_capacity,
        )
    except (KeyError, ValueError) as exc:
        err_console.print(f"[bold red]Compile Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    # 4. Show diff
    diff_result = diff_icon_states(current_raw, compiled_raw, installed_apps=installed_apps)
    _print_diff(diff_result, title=f"Planned Push Diff ({device_info.name})")
    _warn_app_library(compiled_raw, installed_apps)

    if dry_run:
        private_directory(DEFAULT_WORKSPACE_DIR)
        write_private(DEFAULT_PREVIEW_PATH,
            json.dumps(to_json_compatible(compiled_raw), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        console.print(
            f"[bold green]Dry run complete.[/bold green] Wrote compiled state preview to {DEFAULT_PREVIEW_PATH} "
            "(no changes sent to device)."
        )
        return

    if not yes:
        confirmed = typer.confirm(f"Push this layout to {device_info.name}?")
        if not confirmed:
            console.print("[yellow]Push cancelled by user.[/yellow]")
            raise typer.Exit(code=0)

    # 5. Push via set_icon_state()
    try:
        set_raw_icon_state(compiled_raw, udid=device_info.udid)
    except IOSDeviceError as exc:
        err_console.print(
            f"[bold red]Push Failed:[/bold red] {exc}\n"
            f"Your pre-push state is safely backed up at: {pre_backup.path}\n"
            f"Recover with: ios-organizer restore {pre_backup.path} --udid {device_info.udid}"
        )
        raise typer.Exit(code=1)

    console.print("[green]Pushed compiled layout to SpringBoard. Re-pulling to verify...[/green]")

    _verify_write(compiled_raw, installed_apps, device_info.udid, pre_backup, "Push")


@app.command()
def restore(
    backup_file: Annotated[
        Optional[str],
        typer.Argument(help="Backup filename or path to restore (omit to list available backups)."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip interactive confirmation prompt."),
    ] = False,
    udid: Annotated[
        Optional[str],
        typer.Option("--udid", help="Target device UDID if multiple devices are attached."),
    ] = None,
) -> None:
    """List available backups or restore a specific backup to the connected iPhone."""
    backups = list_backups(DEFAULT_BACKUP_DIR)
    if not backup_file:
        if not backups:
            console.print("[yellow]No backups found in backups/.[/yellow]")
            return

        table = Table(title="Available Backups (Newest First)")
        table.add_column("#", style="bold cyan", justify="right", no_wrap=True)
        table.add_column("Backup File", style="bold", no_wrap=True)
        table.add_column("Created At", no_wrap=True)
        table.add_column("Device Name")
        table.add_column("Model")
        table.add_column("iOS Version", no_wrap=True)
        table.add_column("UDID")

        for idx, rec in enumerate(backups, start=1):
            table.add_row(
                str(idx),
                rec.filename,
                rec.created_at,
                rec.device_name,
                rec.model,
                rec.ios_version,
                rec.udid,
            )
        console.print(table)

        if not sys.stdin.isatty():
            return

        choice = typer.prompt(
            "Enter backup number or filename to restore (or press Enter to cancel)",
            default="",
            show_default=False,
        ).strip()
        if not choice:
            console.print("[yellow]Restore cancelled.[/yellow]")
            return
        if choice.isdigit():
            idx_num = int(choice)
            if 1 <= idx_num <= len(backups):
                backup_file = backups[idx_num - 1].filename
            else:
                err_console.print(f"[bold red]Invalid selection:[/bold red] {choice}")
                raise typer.Exit(code=1)
        else:
            backup_file = choice

    try:
        restore_raw, backup_rec = load_backup(backup_file, DEFAULT_BACKUP_DIR)
    except FileNotFoundError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    try:
        device_info, current_raw, installed_apps = fetch_device_snapshot(udid=udid)
    except IOSDeviceError as exc:
        err_console.print(f"[bold red]Connection Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    if backup_rec.udid not in ("Unknown", device_info.udid):
        console.print(
            f"[bold yellow][WARNING][/bold yellow] Backup came from a different device "
            f"(backup UDID: {backup_rec.udid}, connected UDID: {device_info.udid})."
        )
    if backup_rec.ios_version not in ("Unknown", device_info.ios_version):
        console.print(
            f"[bold yellow][WARNING][/bold yellow] Backup came from a different iOS version "
            f"(backup iOS: {backup_rec.ios_version}, connected iOS: {device_info.ios_version})."
        )

    pre_backup = write_backup(
        raw_state=current_raw,
        device_name=device_info.name,
        udid=device_info.udid,
        ios_version=device_info.ios_version,
        model=device_info.model,
        product_type=device_info.product_type,
        build_version=device_info.build_version,
        backup_dir=DEFAULT_BACKUP_DIR,
    )
    console.print(f"[green]Pre-restore safety backup saved to:[/green] {pre_backup.path}")

    diff_result = diff_icon_states(current_raw, restore_raw, installed_apps=installed_apps)
    _print_diff(diff_result, title=f"Restore Diff ({device_info.name} <- {backup_rec.filename})")
    _warn_app_library(restore_raw, installed_apps)

    if not yes:
        confirmed = typer.confirm(
            f"Restore backup '{backup_rec.filename}' to {device_info.name}?"
        )
        if not confirmed:
            console.print("[yellow]Restore cancelled by user.[/yellow]")
            raise typer.Exit(code=0)

    try:
        set_raw_icon_state(restore_raw, udid=device_info.udid)
    except IOSDeviceError as exc:
        err_console.print(
            f"[bold red]Restore Push Failed:[/bold red] {exc}\n"
            f"Recover with: ios-organizer restore {pre_backup.path} --udid {device_info.udid}"
        )
        raise typer.Exit(code=1)

    console.print("[green]Pushed backup state. Re-pulling to verify...[/green]")

    _verify_write(restore_raw, installed_apps, device_info.udid, pre_backup, "Restore")


@app.command(name="auto-sort")
def auto_sort(
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="Sorting mode: 'category' (group loose apps into folders) or 'alpha' (alphabetical).",
        ),
    ] = "category",
    include_unassigned: Annotated[
        bool,
        typer.Option(
            "--include-unassigned",
            help="Also place App Library-only apps from unassigned_apps onto Home Screen pages.",
        ),
    ] = False,
    reorganize_folders: Annotated[
        bool,
        typer.Option(
            "--reorganize-folders",
            help="Dissolve existing folders and re-sort/re-group all their apps together.",
        ),
    ] = False,
    init_categories: Annotated[
        bool,
        typer.Option(
            "--init-categories",
            help="Write a starter workspace/categories.yaml listing all installed apps.",
        ),
    ] = False,
    layout_file: Annotated[
        Path,
        typer.Option("--layout", help="Input layout YAML file."),
    ] = DEFAULT_LAYOUT_PATH,
    output_file: Annotated[
        Path,
        typer.Option("--output", help="Output sorted layout proposal YAML file."),
    ] = DEFAULT_SORTED_LAYOUT_PATH,
    categories_file: Annotated[
        Path,
        typer.Option("--categories", help="Path to categories.yaml file."),
    ] = DEFAULT_CATEGORIES_PATH,
    udid: Annotated[
        Optional[str],
        typer.Option("--udid", help="Target device UDID if querying device for --init-categories."),
    ] = None,
) -> None:
    """Generate a sorted layout proposal in workspace/layout_sorted.yaml without touching layout.yaml or the device."""
    existing_categories = _load_categories_if_present(categories_file)

    if init_categories:
        installed_apps: dict[str, dict[str, Any]] = {}
        try:
            installed_apps = get_installed_apps(udid=udid)
        except IOSDeviceError:
            # Fall back to extracting apps from layout.yaml if offline
            if layout_file.exists():
                layout_data, _ = parse_layout_yaml(layout_file.read_text(encoding="utf-8"))
                from ios_organizer.compiler import is_opaque_entry, parse_app_entry

                def _collect_from_yaml(items: list[Any]) -> None:
                    for it in items:
                        if isinstance(it, dict) and "folder" in it:
                            _collect_from_yaml(it.get("apps") or [])
                        elif isinstance(it, str) and not is_opaque_entry(it):
                            try:
                                name, bid = parse_app_entry(it)
                                installed_apps[bid] = {"name": name, "type": "User", "genre": None}
                            except ValueError:
                                pass

                _collect_from_yaml(layout_data.get("dock") or [])
                for p in layout_data.get("pages") or []:
                    if isinstance(p, dict):
                        _collect_from_yaml(p.get("items") or [])
                _collect_from_yaml(layout_data.get("unassigned_apps") or [])

        if not installed_apps:
            err_console.print(
                "[bold red]Error:[/bold red] Could not query installed apps from device "
                f"and {layout_file} does not exist."
            )
            raise typer.Exit(code=1)

        categories_text = dump_categories_yaml(installed_apps, existing_categories=existing_categories)
        write_private(categories_file, categories_text, encoding="utf-8")
        console.print(
            f"[bold green]Wrote starter categories mapping ({len(installed_apps)} apps) to:[/bold green] "
            f"{categories_file}"
        )
        return

    if not layout_file.exists():
        err_console.print(
            f"[bold red]Error:[/bold red] Layout file not found at {layout_file}. "
            "Run `python -m ios_organizer pull` first."
        )
        raise typer.Exit(code=1)

    try:
        layout_data, header_meta = parse_layout_yaml(layout_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    opaque_store = load_opaque_store(DEFAULT_OPAQUE_DIR)
    installed_apps_map: dict[str, dict[str, Any]] | None = None
    try:
        installed_apps_map = get_installed_apps(udid=udid)
    except IOSDeviceError:
        installed_apps_map = None

    try:
        sorted_layout = auto_sort_layout(
            layout_data,
            mode=mode,
            categories=existing_categories,
            installed_apps=installed_apps_map,
            opaque_store=opaque_store,
            include_unassigned=include_unassigned,
            reorganize_folders=reorganize_folders,
        )
    except ValueError as exc:
        err_console.print(f"[bold red]Auto-Sort Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    yaml_out = dump_layout_yaml(
        sorted_layout,
        backup_filename=header_meta.get("backup_file"),
        device_name=header_meta.get("device"),
        categories=existing_categories,
        installed_apps=installed_apps_map,
    )
    write_private(output_file, yaml_out, encoding="utf-8")
    console.print(
        f"[bold green]Wrote {mode} auto-sort proposal to:[/bold green] {output_file}\n"
        f"(Review {output_file} and copy to {layout_file} or run `push --layout {output_file}` when ready.)"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
