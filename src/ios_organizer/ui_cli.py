"""Agent-operated native screen controls and offline spatial design commands."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from enum import Enum
import hashlib
import json
from pathlib import Path
import time
from typing import Annotated, Optional
from uuid import uuid4

import typer

from ios_organizer.backup import write_backup
from ios_organizer.compiler import parse_layout_yaml
from ios_organizer.device import IOSDeviceError, fetch_device_snapshot, get_ui_capabilities, observe_ui, perform_ui_action
from ios_organizer.screen import ScreenAction, png_size
from ios_organizer.spatial import render_spatial_preview, spatial_tasks
from ios_organizer.preview_image import render_layout_png


app = typer.Typer(help='Spatial plans and agent-guided iPhone Mirroring. Developer tools are explicit opt-in.', no_args_is_help=True)
UI_ROOT = Path('workspace/ui')
MAX_FRAME_AGE = 120


class Backend(str, Enum):
    mirroring = 'mirroring'
    coredevice = 'coredevice'


MIRRORING_GUIDANCE = (
    'Use the agent\'s approved computer-use tools with the iPhone Mirroring app. '
    'This route does not require Developer Mode. The CLI does not control that app. '
    'If app access is unavailable, guide the remaining steps on the iPhone. '
    'CoreDevice commands require explicit --backend coredevice and separate owner opt-in.'
)


def _require_coredevice(backend: Backend) -> None:
    if backend != Backend.coredevice:
        raise ValueError(MIRRORING_GUIDANCE)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _save_frame(capture: dict) -> Path:
    image = capture['image']
    width, height = png_size(image)
    if (capture['width'], capture['height']) != (width, height):
        raise ValueError('Capture dimensions do not match its PNG.')
    folder = UI_ROOT / uuid4().hex
    folder.mkdir(parents=True)
    image_path = folder / 'screen.png'
    image_path.write_bytes(image)
    frame_path = folder / 'frame.json'
    _write_json(frame_path, {'frame_version': 1, 'udid': capture['udid'], 'created_at': time.time(),
                            'width': width, 'height': height, 'screenshot': str(image_path.resolve()),
                            'sha256': hashlib.sha256(image).hexdigest()})
    return frame_path


def _load_frame(path: Path, udid: str | None = None) -> dict:
    frame = json.loads(path.read_text())
    if not isinstance(frame, dict) or frame.get('frame_version') != 1:
        raise ValueError('Expected a frame produced by ui observe.')
    if not isinstance(frame.get('udid'), str) or not frame['udid']:
        raise ValueError('Frame has no target device.')
    if udid is not None and udid.replace('-', '') != frame['udid'].replace('-', ''):
        raise ValueError('Frame belongs to another device. Observe the target phone again.')
    age = time.time() - frame['created_at']
    if not 0 <= age <= MAX_FRAME_AGE:
        raise ValueError('Screenshot is stale. Run ui observe again; no input sent.')
    if path.with_suffix('.used').exists():
        raise ValueError('This frame was already consumed. Observe again; never replay uncertain input.')
    image = Path(frame['screenshot']).read_bytes()
    if hashlib.sha256(image).hexdigest() != frame['sha256'] or png_size(image) != (frame['width'], frame['height']):
        raise ValueError('Screenshot or frame dimensions changed. Observe again.')
    return frame


@contextmanager
def _device_lock(udid: str):
    locks = UI_ROOT / 'locks'
    locks.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(udid.replace('-', '').encode()).hexdigest()
    path = locks / f'{key}.lock'
    try:
        handle = path.open('x')
    except FileExistsError:
        raise ValueError('A screen operation is already running for this phone. If interrupted, inspect it before removing its workspace/ui/locks file.') from None
    try:
        handle.write(udid); handle.close()
        yield
    finally:
        handle.close()
        path.unlink(missing_ok=True)


def _emit_frame(path: Path, **extra) -> None:
    frame = json.loads(path.read_text())
    typer.echo(json.dumps({'frame': str(path.resolve()), 'screenshot': frame['screenshot'],
                          'width': frame['width'], 'height': frame['height'], **extra}))


@app.command()
def plan(
    layout: Annotated[Path, typer.Option('--layout', help='Spatial YAML design to validate.')],
    preview: Annotated[Optional[Path], typer.Option('--preview', help='Optional exact-grid HTML or PNG preview.')] = None,
    backend: Annotated[Backend, typer.Option('--backend', help='Default uses the agent’s iPhone Mirroring tools.')] = Backend.mirroring,
) -> None:
    """Validate a spatial design and emit widget/placement tasks without touching a phone."""
    try:
        data, _ = parse_layout_yaml(layout.read_text())
        tasks = spatial_tasks(data)
        if preview is not None:
            if preview.resolve() == layout.resolve():
                raise ValueError('Preview output must differ from the design file.')
            preview.parent.mkdir(parents=True, exist_ok=True)
            if preview.suffix.lower() == '.png':
                preview.write_bytes(render_layout_png(data, {}, spatial=True, title='Exact placement plan'))
            elif preview.suffix.lower() in {'.html', '.htm'}:
                preview.write_text(render_spatial_preview(data))
            else:
                raise ValueError('Preview output must be HTML or PNG.')
        typer.echo(json.dumps({'status': 'plan_only', 'backend': backend.value,
                              'developer_mode_required': backend == Backend.coredevice, 'tasks': tasks,
                              'requires': ['fresh screenshots', 'approved agent computer-use access to iPhone Mirroring' if backend == Backend.mirroring else 'CoreDevice developer services', 'agent visual verification'],
                              'preview': str(preview.resolve()) if preview else None}, ensure_ascii=False))
    except (OSError, ValueError, TypeError) as exc:
        typer.echo(f'Spatial plan error: {exc}', err=True)
        raise typer.Exit(1)


@app.command()
def doctor(
    udid: Annotated[Optional[str], typer.Option('--udid', help='Target iPhone; required only for CoreDevice checks.')] = None,
    backend: Annotated[Backend, typer.Option('--backend')] = Backend.mirroring,
) -> None:
    """Describe the default no-Developer-Mode route, or explicitly check CoreDevice."""
    try:
        if backend == Backend.mirroring:
            typer.echo(json.dumps({'backend': 'mirroring', 'developer_mode_required': False,
                                  'app_access': 'not_probed_use_agent_tools', 'next_step': MIRRORING_GUIDANCE}))
            return
        if not udid:
            raise ValueError('--udid is required for an explicit CoreDevice check.')
        typer.echo(json.dumps(get_ui_capabilities(udid)))
    except (IOSDeviceError, ValueError) as exc:
        typer.echo(f'UI prerequisite error: {exc}', err=True)
        raise typer.Exit(1)


@app.command()
def observe(
    udid: Annotated[Optional[str], typer.Option('--udid', help='Explicit target iPhone UDID.')] = None,
    backend: Annotated[Backend, typer.Option('--backend')] = Backend.mirroring,
) -> None:
    """Use agent tools by default; --backend coredevice enables the optional PNG adapter."""
    try:
        _require_coredevice(backend)
        if not udid:
            raise ValueError('--udid is required for a CoreDevice observation.')
        with _device_lock(udid):
            capture = observe_ui(udid)
            if capture['udid'].replace('-', '') != udid.replace('-', ''):
                raise ValueError('Screenshot belongs to another phone.')
            path = _save_frame(capture)
        _emit_frame(path, status='observed', outcome_verified=False)
    except (IOSDeviceError, OSError, ValueError) as exc:
        typer.echo(f'Screen observation error: {exc}', err=True)
        raise typer.Exit(1)


@app.command()
def action(
    kind: Annotated[str, typer.Argument(help='tap, hold, drag, type, or home')],
    frame: Annotated[Path, typer.Option('--frame', help='Fresh frame.json from ui observe or the previous action.')],
    udid: Annotated[Optional[str], typer.Option('--udid', help='Optional device cross-check; frame already pins it.')] = None,
    x: Annotated[Optional[int], typer.Option('--x')] = None,
    y: Annotated[Optional[int], typer.Option('--y')] = None,
    to_x: Annotated[Optional[int], typer.Option('--to-x')] = None,
    to_y: Annotated[Optional[int], typer.Option('--to-y')] = None,
    hold: Annotated[float, typer.Option('--hold', help='Contact hold before releasing or dragging; use 0 for scrolling.')] = 0.8,
    duration: Annotated[float, typer.Option('--duration')] = 0.6,
    text: Annotated[Optional[str], typer.Option('--text', help='ASCII text for a focused widget search field.')] = None,
    yes: Annotated[bool, typer.Option('--yes', help='Execute the action in an already-authorized UI task.')] = False,
    backend: Annotated[Backend, typer.Option('--backend')] = Backend.mirroring,
) -> None:
    """Send one observed input and save its result screenshot; the agent must verify it."""
    journal = None
    attempted = False
    try:
        _require_coredevice(backend)
        source = _load_frame(frame, udid)
        request = ScreenAction(kind, x, y, to_x, to_y, hold, duration, text)
        request.validate(source['width'], source['height'])
        if not yes and not typer.confirm('Send this input to the observed iPhone?'):
            typer.echo('UI action cancelled. No input sent.'); return
        with _device_lock(source['udid']):
            _load_frame(frame, udid)  # Recheck after confirmation/lock acquisition.
            info, raw, _ = fetch_device_snapshot(udid=source['udid'])
            if info.udid.replace('-', '') != source['udid'].replace('-', ''):
                raise ValueError('Backup snapshot belongs to a different phone; no input sent.')
            backup = write_backup(raw, info.name, info.udid, info.ios_version,
                                  info.model, info.product_type, info.build_version)
            record = {'status': 'prepared', 'device': info.udid, 'frame': str(frame.resolve()),
                      'action': asdict(request), 'backup': str(backup.path.resolve()),
                      'outcome_verified': False,
                      'recovery_limit': 'Raw USB backup may not capture or restore free placement or widget configuration.'}
            journal = frame.parent / 'action.json'
            _write_json(journal, record)
            _load_frame(frame, udid)  # A slow backup must not turn an old screenshot into a valid input target.
            # Atomic consumption prevents replay even after a partial action or failed screenshot.
            with frame.with_suffix('.used').open('x') as consumed:
                consumed.write('Input attempt reserved. Observe again before retrying.\n')
            record['status'] = 'input_attempt_started'
            _write_json(journal, record)
            attempted = True
            result = perform_ui_action(source['udid'], request, source['width'], source['height'])
            if result['udid'].replace('-', '') != source['udid'].replace('-', ''):
                raise ValueError('Result screenshot belongs to another phone.')
            next_frame = _save_frame(result)
            record.update(status='input_sent_visual_verification_required', result_frame=str(next_frame.resolve()))
            _write_json(journal, record)
        _emit_frame(next_frame, status=record['status'], journal=str(journal.resolve()), outcome_verified=False)
    except (IOSDeviceError, OSError, ValueError, TypeError, KeyError) as exc:
        if attempted:
            if journal is not None:
                try:
                    record.update(status='uncertain_observe_before_retry', error=str(exc))
                    _write_json(journal, record)
                except OSError:
                    pass
            typer.echo(f'UI result uncertain: {exc}. Input may have been delivered. Observe again; do not replay. Journal: {journal}', err=True)
            raise typer.Exit(2)
        typer.echo(f'UI action blocked before input: {exc}', err=True)
        raise typer.Exit(1)
