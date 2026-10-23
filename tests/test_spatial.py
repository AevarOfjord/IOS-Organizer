"""Exact plans retain empty regions and never become compact USB payloads."""

from copy import deepcopy
import json

import pytest
from typer.testing import CliRunner

from ios_organizer.cli import app
from ios_organizer.compiler import compile_layout
from ios_organizer.spatial import render_spatial_preview, spatial_tasks, validate_spatial_plan
from ios_organizer.validation import validate_layout


@pytest.fixture
def plan():
    return {'spatial_version': 1, 'grid': {'columns': 4, 'rows': 6}, 'pages': [
        {'page': 1, 'items': [
            {'empty': True, 'row': 1, 'column': 1, 'width': 4, 'height': 4},
            {'app': 'Mail (com.example.mail)', 'row': 5, 'column': 1},
        ]}]}


def test_gap_plan_preserves_app_row_and_empty_rectangle(plan):
    tasks = spatial_tasks(plan)
    assert tasks[0]['operation'] == 'keep_empty'
    assert tasks[1]['row'] == 5
    html = render_spatial_preview(plan)
    assert 'grid-column:1 / span 1;grid-row:5 / span 1' in html
    assert 'grid-column:1 / span 4;grid-row:1 / span 4' in html
    assert 'USB push cannot apply' in html


def test_overlap_includes_explicitly_empty_space(plan):
    plan['pages'][0]['items'][1]['row'] = 4
    assert any('overlapping' in error for error in validate_spatial_plan(plan))


def test_widget_span_and_configuration_are_retained(plan):
    plan['pages'][0]['items'][0] = {'widget': {'id': 'tasks', 'app': 'Reminders', 'size': 'medium',
                                             'configuration': {'list': 'Today'}}, 'row': 1, 'column': 1}
    tasks = spatial_tasks(plan)
    assert tasks[0]['operation'] == 'ensure_widget'
    assert tasks[0]['widget']['configuration'] == {'list': 'Today'}
    assert 'grid-column:1 / span 4;grid-row:1 / span 2' in render_spatial_preview(plan)


@pytest.mark.parametrize('change', [
    {'row': 0}, {'row': True}, {'column': 5}, {'width': 0}, {'width': 2},
    {'app': ''}, {'position': [5, 1]}, {'widget': {'id': 'x', 'app': 'Weather', 'size': 'small'}},
])
def test_bad_app_geometry_or_ambiguous_item_is_rejected(plan, change):
    plan['pages'][0]['items'][1].update(change)
    assert validate_spatial_plan(plan)


def test_duplicate_apps_and_widget_ids_are_blocked(plan):
    item = deepcopy(plan['pages'][0]['items'][1]); item['column'] = 2
    plan['pages'][0]['items'].append(item)
    assert any('Duplicate app' in e for e in validate_spatial_plan(plan))
    plan['pages'][0]['items'] = [
        {'widget': {'id': 'same', 'app': 'Weather', 'size': 'small'}, 'row': 1, 'column': 1},
        {'widget': {'id': 'same', 'app': 'Calendar', 'size': 'small'}, 'row': 1, 'column': 3}]
    assert any('Duplicate widget' in e for e in validate_spatial_plan(plan))


def test_spatial_plan_is_blocked_by_legacy_validator_and_compiler(plan):
    assert not validate_layout(plan).is_valid
    with pytest.raises(ValueError, match='Spatial designs cannot'):
        compile_layout(plan)
    disguised = {'schema_version': 1, 'pages': [{'items': [{'folder': 'A', 'apps': [], 'row': 5}]}]}
    assert not validate_layout(disguised).is_valid
    with pytest.raises(ValueError, match='Exact cells'):
        compile_layout(disguised)


def test_spatial_cli_and_preview_are_offline(tmp_path, monkeypatch, plan):
    import yaml
    import ios_organizer.ui_cli as ui
    monkeypatch.setattr(ui, 'observe_ui', lambda *a: pytest.fail('Offline plan must not connect'))
    path = tmp_path / 'design.yaml'; path.write_text(yaml.safe_dump(plan))
    preview = tmp_path / 'preview.html'
    result = CliRunner().invoke(app, ['ui', 'plan', '--layout', str(path), '--preview', str(preview)])
    assert result.exit_code == 0, result.output
    assert 'plan_only' in result.output and preview.exists()
    output = json.loads(result.output)
    assert output['backend'] == 'mirroring' and output['developer_mode_required'] is False


def test_preview_escapes_untrusted_labels(plan):
    plan['pages'][0]['items'][1]['app'] = '<script>alert(1)</script> (com.example.mail)'
    html = render_spatial_preview(plan)
    assert '<script>' not in html and '&lt;script&gt;' in html
