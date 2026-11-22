#!/usr/bin/env python3
"""Local dataset checkpoints and structural checks; never generates questions or calls Lark.

The model owns semantic coverage, realistic task design, feedback interpretation,
and actual remote review. A file hash is an evidence locator, not proof of quality.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

_spec = importlib.util.spec_from_file_location('explicit_dataset_runtime', Path(__file__).with_name('runtime.py'))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)
FIELDS = ('Query', '维度', '场景分布', '用户场景', '能力', '预置信息', 'Rubrics')
ROW_FIELDS = ('id', 'query', 'dimension', 'ability_ids', 'task_scenarios', 'user_scenario_id', 'preconditions', 'rubrics')
require = rt.require
nonempty = rt.nonempty


def evidence(value, directory):
    require(nonempty(value), 'An actual saved evidence file is required')
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = directory / path
    path = path.resolve()
    require(path.is_file(), 'Evidence file missing: ' + str(path))
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def evidence_current(item):
    try:
        return evidence(item['path'], Path.cwd()) == item
    except (ValueError, KeyError, OSError):
        return False


def project_fingerprint(project):
    # Runtime keeps the confirmed business digest through --presentation-only.
    # A real range change must re-confirm scope (description) or invalidate range
    # (PRD); include that event because PRD rendering has additional boundaries.
    scope_digest = (project.get('confirmed_range_digest') if project.get('input_document_type') == 'prd'
                    else (project.get('range_confirmation') or {}).get('range_digest'))
    if nonempty(scope_digest):
        range_events = [item for item in project.get('events', []) if item.get('kind') == 'upstream_changed'
                        and (item.get('detail') or {}).get('from') == 'range']
        scope = {'confirmed_digest': scope_digest,
                 'structure': rt.scope_structure(project.get('evaluation_scope') or []),
                 'last_range_invalidation': range_events[-1] if range_events else None}
    else:
        # Older state without a reliable confirmation token remains conservative.
        scope = {key: project.get(key) for key in rt.SCOPE_FIELDS}
    return rt.digest({key: project.get(key) for key in (
        'project_key', 'source_document', 'user_scenarios', 'bmk_plan')}
        | {'scope': scope, 'document_id': project.get('outputs', {}).get('document_id')})


def require_document_ready(project):
    require(all(project.get(key) is True for key in (
        'range_ready', 'user_scenarios_confirmed', 'bmk_plan_confirmed')),
        'Confirm scope, scenes and BMK before dataset production')
    require(project.get('stage') == 'complete' and project.get('outputs_stale') is not True,
            'Deliver and verify the document and whiteboard first')
    outputs = project.get('outputs') or {}
    require(all(outputs.get(key) for key in (
        'document_id', 'document_url', 'board_id', 'readback_evidence',
        'board_preview_evidence', 'showcase_quality_review')),
        'Document/board/readback/showcase evidence is incomplete')
    rt.validate_bmk(project['bmk_plan'], project.get('evaluation_scope'), project['user_scenarios'])


def cell_key(item):
    return tuple(item[key] for key in ('ability_id', 'task_scenario_id', 'user_scenario_id', 'dimension'))


def validate_rows(rows, plan, accepted=(), complete=False):
    require(isinstance(rows, list), 'rows must be an array')
    abilities = {item['id']: item for item in plan['abilities']}
    users = {item['id'] for item in plan['user_scenarios']}
    quotas = Counter()
    for cell in plan['cells']:
        quotas[cell_key(cell)] += cell['count']
    counts = Counter()
    seen_ids, queries = set(), set()
    canonical = []
    for row in rows:
        require(isinstance(row, dict) and set(row) == set(ROW_FIELDS), 'Each row must have exactly the canonical row fields')
        require(nonempty(row['id']) and row['id'] not in seen_ids, 'Missing or duplicate row id')
        seen_ids.add(row['id'])
        require(nonempty(row['query']), 'Missing Query: ' + row['id'])
        query = row['query'].strip()
        require(query not in queries, 'Duplicate Query: ' + row['id'])
        queries.add(query)
        require(isinstance(row['dimension'], str) and row['dimension'] in rt.DIMENSIONS,
                'Dimension must be one breadth/thickness/depth value')
        require(isinstance(row['user_scenario_id'], str) and row['user_scenario_id'] in users,
                'User scenario must be one confirmed id')
        aids = row['ability_ids']
        require(isinstance(aids, list) and aids and all(isinstance(a, str) and a in abilities for a in aids),
                'Ability tags must be ordered confirmed ids')
        require(len(set(aids)) == len(aids), 'Duplicate ability tags')
        tasks = row['task_scenarios']
        require(isinstance(tasks, list) and tasks, 'Task scene tags required')
        pairs = []
        for task in tasks:
            require(isinstance(task, dict) and set(task) == {'ability_id', 'task_scenario_id'},
                    'Task tag must contain its parent ability and task id')
            aid, tid = task['ability_id'], task['task_scenario_id']
            require(isinstance(aid, str) and aid in aids and isinstance(tid, str)
                    and tid in {t['id'] for t in abilities[aid]['task_scenarios']},
                    'Task tag is outside its tagged parent ability')
            pairs.append((aid, tid))
        require(len(set(pairs)) == len(pairs), 'Duplicate task scene tags')
        require(pairs[0][0] == aids[0], 'First task scene must belong to first ability')
        require(isinstance(row['preconditions'], str), 'Preconditions must be text; use an empty string when unnecessary')
        require(row['preconditions'].strip() != '—', 'Convert the showcase preconditions placeholder — to an empty string')
        rubrics = row['rubrics']
        require(isinstance(rubrics, list) and rubrics and all(nonempty(point) for point in rubrics),
                'Rubrics must be a nonempty list of concise criteria')
        key = (aids[0], pairs[0][1], row['user_scenario_id'], row['dimension'])
        counts[key] += 1
        require(counts[key] <= quotas[key], 'Primary-label allocation exceeds confirmed BMK: ' + str(key))
        canonical.append(copy.deepcopy(row))
    by_id = {row['id']: row for row in canonical}
    for example in accepted:
        require(by_id.get(example['id']) == example,
                'Keep the approved example unchanged or revise and confirm showcase first: ' + example['id'])
    if complete:
        require(counts == +quotas, 'Dataset does not fill all confirmed BMK allocation cells')
    remaining = [{**dict(zip(('ability_id', 'task_scenario_id', 'user_scenario_id', 'dimension'), key)),
                  'count': quotas[key] - counts[key]} for key in quotas if quotas[key] > counts[key]]
    return {'rows': canonical, 'count': len(canonical), 'total': plan['total'],
            'complete': counts == +quotas, 'remaining': remaining,
            'semantic_review_required': True}


def compact_row(row):
    """Return the fields needed to locate a row without carrying Rubrics."""
    return {key: copy.deepcopy(value) for key, value in row.items() if key != 'rubrics'}


def row_cell(row):
    """Return the primary allocation cell used by BMK accounting."""
    task = row['task_scenarios'][0]
    return (row['ability_ids'][0], task['task_scenario_id'], row['user_scenario_id'], row['dimension'])


def parse_cell_filter(value):
    """Parse a stable, shell-friendly cell filter: ability|task|user|dimension."""
    require(isinstance(value, str) and value.count('|') == 3,
            'Cell filter must be ability_id|task_scenario_id|user_scenario_id|dimension')
    return tuple(value.split('|'))


def row_summary(rows, plan):
    """Build a bounded summary so recovery does not require reading every row."""
    primary_abilities = Counter(row['ability_ids'][0] for row in rows)
    dimensions = Counter(row['dimension'] for row in rows)
    users = Counter(row['user_scenario_id'] for row in rows)
    cells = Counter(row_cell(row) for row in rows)
    quotas = Counter(cell_key(cell) for cell in plan.get('cells', []))
    gaps = {"|".join(key): quotas[key] - cells[key] for key in quotas if quotas[key] > cells[key]}
    gap_items = sorted(gaps.items())
    return {
        'row_count': len(rows),
        'by_dimension': dict(sorted(dimensions.items())),
        'by_primary_ability': dict(sorted(primary_abilities.items())),
        'by_user_scenario': dict(sorted(users.items())),
        'remaining_count': sum(gaps.values()),
        'remaining_cells_preview': dict(gap_items[:50]),
        'remaining_cells_truncated': len(gap_items) > 50,
    }


def bounded_result(result, limit=50):
    """Keep batch responses useful without returning every remaining BMK cell."""
    out = {key: value for key, value in result.items() if key != 'remaining'}
    remaining = result.get('remaining', [])
    out.update(remaining=remaining[:limit], remaining_count=sum(item['count'] for item in remaining),
               remaining_truncated=len(remaining) > limit)
    return out


def filter_rows(rows, ids=None, abilities=None, cell=None):
    ids = set(ids or [])
    abilities = set(abilities or [])
    selected = []
    for row in rows:
        if ids and row['id'] not in ids:
            continue
        if abilities and not abilities.intersection(row['ability_ids']):
            continue
        if cell is not None and row_cell(row) != cell:
            continue
        selected.append(row)
    return selected


BASE_FIELDS = ('Query', '维度', '场景分布', '用户场景', '能力', '预置信息', 'Rubrics')
DIMENSION_LABELS = {'breadth': '广度', 'thickness': '厚度', 'depth': '深度'}


def label_maps(plan):
    abilities = {item['id']: item for item in plan['abilities']}
    ability_names = {aid: item['name'] for aid, item in abilities.items()}
    users = {item['id']: item['name'] for item in plan['user_scenarios']}
    tasks = {}
    task_name_owner = {}
    for ability in plan['abilities']:
        for task in ability.get('task_scenarios', []):
            key = (ability['id'], task['id'])
            tasks[key] = task['name']
            task_name_owner.setdefault(task['name'], []).append(key)
    ambiguous = sorted(name for name, owners in task_name_owner.items() if len(owners) > 1)
    require(not ambiguous,
            'Task scenario labels are ambiguous across abilities; resolve labels before Base export: '
            + ', '.join(ambiguous))
    return abilities, ability_names, users, tasks


def row_to_base_fields(row, plan):
    abilities, ability_names, users, tasks = label_maps(plan)
    task_labels = [tasks[(item['ability_id'], item['task_scenario_id'])] for item in row['task_scenarios']]
    return {
        'Query': row['query'],
        '维度': [DIMENSION_LABELS[row['dimension']]],
        '场景分布': task_labels,
        '用户场景': [users[row['user_scenario_id']]],
        '能力': [ability_names[aid] for aid in row['ability_ids']],
        '预置信息': row['preconditions'],
        'Rubrics': '\n'.join(f'R{index}: {point}' for index, point in enumerate(row['rubrics'], 1)),
    }


def base_schema(plan):
    abilities, ability_names, users, tasks = label_maps(plan)
    return [
        {'name': 'Query', 'type': 'text'},
        {'name': '维度', 'type': 'select', 'multiple': False,
         'options': [{'name': DIMENSION_LABELS[key]} for key in ('breadth', 'thickness', 'depth')]},
        {'name': '场景分布', 'type': 'select', 'multiple': True,
         'options': [{'name': tasks[key]} for key in sorted(tasks)]},
        {'name': '用户场景', 'type': 'select', 'multiple': False,
         'options': [{'name': users[item['id']]} for item in plan['user_scenarios']]},
        {'name': '能力', 'type': 'select', 'multiple': True,
         'options': [{'name': ability_names[item['id']]} for item in plan['abilities']]},
        {'name': '预置信息', 'type': 'text'},
        {'name': 'Rubrics', 'type': 'text'},
    ]


def visible_signature(fields):
    """Stable signature for reconciliation; it preserves ordered select values."""
    return rt.digest({key: fields.get(key) for key in BASE_FIELDS})


def row_signature(row, plan):
    return visible_signature(row_to_base_fields(row, plan))


def load_json_or_ndjson(path):
    path = Path(path).resolve()
    require(path.is_file(), 'Remote/readback file missing: ' + str(path))
    raw = path.read_text(encoding='utf-8')
    try:
        value = json.loads(raw)
        return value
    except json.JSONDecodeError:
        rows = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f'Invalid NDJSON at line {line_number}: {path}') from exc
        return rows


def extract_remote_rows(value):
    if isinstance(value, dict):
        for key in ('rows', 'records', 'items'):
            if isinstance(value.get(key), list):
                return value[key]
        data = value.get('data')
        if isinstance(data, dict):
            for key in ('rows', 'records', 'items'):
                if isinstance(data.get(key), list):
                    return data[key]
        if value.get('record_id') or value.get('recordId') or value.get('fields'):
            return [value]
        return []
    require(isinstance(value, list), 'Remote/readback data must be a rows/records array or NDJSON')
    return value


def normalize_remote_value(value):
    if isinstance(value, list):
        return [normalize_remote_value(item) for item in value]
    if isinstance(value, dict):
        # Lark cell values may be wrapped objects. Prefer an explicit display
        # value; never infer a label from object size or option order.
        for key in ('name', 'text', 'value'):
            if key in value:
                return normalize_remote_value(value[key])
        return value
    return value


def load_mapping(path, state):
    mapping = dict((state.get('delivery') or {}).get('record_ids', {}))
    if path:
        value = load_json_or_ndjson(path)
        if isinstance(value, dict) and isinstance(value.get('record_ids'), dict):
            value = value['record_ids']
        require(isinstance(value, dict), 'Mapping must be an object of row_id to record_id')
        mapping.update(value)
    require(all(nonempty(key) and nonempty(value) for key, value in mapping.items()),
            'Mapping contains an empty row or record id')
    return mapping


def load_previous_rows(path):
    if not path:
        return {}
    value = load_json_or_ndjson(path)
    rows = value.get('rows') if isinstance(value, dict) else value
    require(isinstance(rows, list), 'Previous snapshot must contain a rows array')
    return {row['id']: row for row in rows if isinstance(row, dict) and nonempty(row.get('id'))}


def write_json(path, value):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def export_base(project, state, output_dir, batch_size=200):
    """Create a local, replayable Base write plan; this function never calls Lark."""
    require(1 <= batch_size <= 200, 'batch-size must be between 1 and 200')
    require_document_ready(project)
    current_review(state, project)
    require(not state.get('rows_stale'), 'Rows are stale; save the current approved collection before export')
    rows = state.get('rows', [])
    checked = validate_rows(rows, project['bmk_plan'], state['review']['examples'], complete=True)
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    visible_rows = [{'id': row['id'], 'fields': row_to_base_fields(row, project['bmk_plan'])} for row in checked['rows']]
    batches = []
    for offset in range(0, len(visible_rows), batch_size):
        items = visible_rows[offset:offset + batch_size]
        number = offset // batch_size + 1
        name = f'batch-{number:03d}'
        payload = {'create_records': [item['fields'] for item in items]}
        write_json(out / f'{name}.json', payload)
        write_json(out / f'{name}.meta.json', {
            'batch_id': name, 'project_key': project['project_key'],
            'project_fingerprint': project_fingerprint(project),
            'review_id': state['review_id'], 'row_ids': [item['id'] for item in items],
            'row_hashes': {item['id']: visible_signature(item['fields']) for item in items},
            'payload_hash': rt.digest(payload), 'count': len(items), 'source': 'dataset_workflow export-base',
        })
        batches.append({'batch_id': name, 'path': str((out / f'{name}.json').resolve()),
                        'meta_path': str((out / f'{name}.meta.json').resolve()),
                        'count': len(items), 'row_ids': [item['id'] for item in items],
                        'payload_hash': rt.digest(payload)})
    plan = {'project_key': project['project_key'], 'project_fingerprint': project_fingerprint(project),
            'review_id': state['review_id'], 'schema': base_schema(project['bmk_plan']),
            'schema_path': str((out / 'schema.json').resolve()), 'total': len(visible_rows),
            'batch_size': batch_size, 'batches': batches, 'source': 'dataset_workflow export-base'}
    write_json(out / 'schema.json', {'fields': plan['schema']})
    plan_path = write_json(out / 'export-plan.json', plan)
    return {'ok': True, 'output_dir': str(out), 'schema_path': plan['schema_path'],
            'plan_path': str(plan_path), 'batch_count': len(batches), 'record_count': len(visible_rows),
            'review_id': state['review_id']}


def remote_aliases(state):
    fields = (state.get('delivery') or {}).get('field_ids', {})
    return {value: key for key, value in fields.items()}


def canonical_remote_field(name, value):
    value = normalize_remote_value(value)
    if name in ('维度', '场景分布', '用户场景', '能力'):
        if not isinstance(value, list):
            value = [] if value in (None, '') else [value]
        return [str(item) for item in value]
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return '' if value is None else str(value)


def normalize_remote_fields(record, aliases=None):
    record_id = record.get('record_id') or record.get('recordId') or record.get('id')
    fields = record.get('fields') or record.get('values') or record.get('record')
    if fields is None:
        fields = record
    require(isinstance(fields, dict), 'Remote record fields must be an object')
    aliases = aliases or {}
    normalized = {}
    for key, value in fields.items():
        name = aliases.get(key, key)
        if name in BASE_FIELDS:
            normalized[name] = canonical_remote_field(name, value)
    missing = [key for key in BASE_FIELDS if key not in normalized]
    for key in ('维度', '场景分布', '用户场景', '能力'):
        normalized.setdefault(key, [])
    normalized.setdefault('预置信息', '')
    normalized.setdefault('Rubrics', '')
    return record_id, normalized, missing


def load_remote_snapshot(path, aliases=None):
    value = load_json_or_ndjson(path)
    records = extract_remote_rows(value)
    if isinstance(value, dict) and not records and not any(key in value for key in ('rows', 'records', 'items')):
        data = value.get('data')
        require(isinstance(data, dict) and any(key in data for key in ('rows', 'records', 'items')),
                'Unrecognized lark-base readback shape; preserve raw evidence and diagnose manually')
    out = []
    seen = set()
    for record in records:
        require(isinstance(record, dict), 'Remote record must be an object')
        record_id, fields, missing = normalize_remote_fields(record, aliases)
        duplicate = record_id in seen if record_id else False
        if record_id:
            seen.add(record_id)
        out.append({'record_id': record_id, 'fields': fields, 'signature': visible_signature(fields),
                    'duplicate': duplicate, 'missing_fields': missing})
    return out


def load_readback_manifest(path, expected_count=None):
    if not path:
        return {'present': False, 'problems': ['manifest missing']}
    value = load_json_or_ndjson(path)
    require(isinstance(value, dict), 'Readback manifest must be an object')
    problems = []
    if value.get('has_more') is True:
        problems.append('pagination is incomplete: has_more=true')
    if value.get('next_offset') not in (None, '', 0) and value.get('has_more') is not False:
        problems.append('pagination has a next_offset without an exhausted readback')
    if expected_count is not None and value.get('records_count') is not None:
        try:
            if int(value['records_count']) != expected_count:
                problems.append('manifest records_count does not equal parsed record count')
        except (TypeError, ValueError):
            problems.append('manifest records_count is not numeric')
    pages = value.get('pages')
    if isinstance(pages, list):
        revisions = {page.get('rev') for page in pages if isinstance(page, dict) and page.get('rev') is not None}
        contexts = {json.dumps(page.get('query_context'), sort_keys=True, ensure_ascii=False)
                    for page in pages if isinstance(page, dict) and page.get('query_context') is not None}
        if len(revisions) > 1:
            problems.append('readback pages use different revisions')
        if len(contexts) > 1:
            problems.append('readback pages use different query_context values')
    return {'present': True, 'problems': problems, 'raw': value}


def schema_problems(path, plan):
    if not path:
        return []
    value = load_json_or_ndjson(path)
    fields = value.get('fields') if isinstance(value, dict) else value
    if isinstance(value, dict) and isinstance(value.get('data'), dict):
        fields = value['data'].get('fields', fields)
    if not isinstance(fields, list):
        return ['field schema shape is unknown']
    expected = base_schema(plan)
    actual_by_name = {}
    for item in fields:
        if not isinstance(item, dict):
            return ['field schema contains a non-object entry']
        name = item.get('name') or item.get('field_name') or item.get('fieldName')
        if name:
            actual_by_name[name] = item
    problems = []
    if list(actual_by_name)[:len(expected)] != [item['name'] for item in expected]:
        problems.append('field order/name differs from the seven-column contract')
    if set(actual_by_name) != {item['name'] for item in expected}:
        problems.append('field schema does not contain exactly the seven visible fields')
    for item in expected:
        actual = actual_by_name.get(item['name'])
        if not actual:
            continue
        if item['type'] == 'text' and actual.get('type') not in (None, 'text', 'Text', 'string'):
            problems.append(item['name'] + ' is not text')
        if item['type'] == 'select':
            if actual.get('multiple') is not None and bool(actual.get('multiple')) != bool(item.get('multiple')):
                problems.append(item['name'] + ' multiple setting differs')
            options = actual.get('options')
            if isinstance(options, list):
                names = {opt.get('name') if isinstance(opt, dict) else opt for opt in options}
                expected_names = {opt['name'] for opt in item['options']}
                if not expected_names <= names:
                    problems.append(item['name'] + ' options are incomplete')
    return problems


def previous_signatures(path, aliases=None, mapping=None, plan=None):
    if not path:
        return {}
    value = load_json_or_ndjson(path)
    records = extract_remote_rows(value)
    result = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        record_id = record.get('record_id') or record.get('recordId') or record.get('id')
        fields = record.get('fields') or record.get('values') or record.get('record')
        if fields is not None and record_id:
            _, normalized, _ = normalize_remote_fields(record, aliases)
            result[record_id] = visible_signature(normalized)
            continue
        row_id = record.get('id')
        if row_id and isinstance(record.get('row'), dict):
            row_id = record['row'].get('id', row_id)
        if row_id and isinstance(record, dict) and set(record) >= set(ROW_FIELDS):
            require(plan is not None, 'Canonical previous rows require the confirmed BMK plan')
            result[(mapping or {}).get(row_id, row_id)] = visible_signature(row_to_base_fields(record, plan))
    return result


def reconcile_remote(project, state, remote_path, mapping_path=None, previous_path=None, output_dir=None,
                     manifest_path=None, schema_path=None):
    require_document_ready(project)
    current_review(state, project)
    require(not state.get('rows_stale'), 'Rows are stale; save the current approved collection before diagnosis')
    rows = validate_rows(state.get('rows', []), project['bmk_plan'], state['review']['examples'], complete=True)['rows']
    aliases = remote_aliases(state)
    remote = load_remote_snapshot(remote_path, aliases)
    manifest = load_readback_manifest(manifest_path, len(remote))
    schema_issues = schema_problems(schema_path, project['bmk_plan'])
    mapping = load_mapping(mapping_path, state)
    previous = previous_signatures(previous_path, aliases, mapping, project['bmk_plan'])
    target = {row['id']: row for row in rows}
    remote_by_id = {item['record_id']: item for item in remote if item.get('record_id')}
    remote_by_sig = {}
    for item in remote:
        remote_by_sig.setdefault(item['signature'], []).append(item['record_id'])
    statuses, used = [], set()
    create_records, update_records = [], {}
    for row in rows:
        rid, fields = row['id'], row_to_base_fields(row, project['bmk_plan'])
        sig = visible_signature(fields)
        remote_id = mapping.get(rid)
        detail = {'row_id': rid, 'target_signature': sig, 'mapped_record_id': remote_id}
        if remote_id:
            item = remote_by_id.get(remote_id)
            if item is None:
                detail['status'] = 'unknown'; detail['reason'] = 'mapped remote record is absent'
            elif item.get('duplicate'):
                used.add(remote_id); detail['status'] = 'conflict'; detail['record_id'] = remote_id
                detail['reason'] = 'remote record_id is duplicated in readback'
            elif item['signature'] == sig:
                used.add(remote_id); detail['status'] = 'unchanged'; detail['record_id'] = remote_id
            elif previous.get(remote_id) == item['signature']:
                used.add(remote_id); detail['status'] = 'update'; detail['record_id'] = remote_id
                update_records[remote_id] = fields
            else:
                used.add(remote_id); detail['status'] = 'conflict'; detail['record_id'] = remote_id
                detail['reason'] = 'remote differs from target and previous snapshot does not prove a safe update'
        else:
            candidates = [item for item in remote_by_sig.get(sig, []) if item and item not in used]
            if len(candidates) == 1:
                used.add(candidates[0]); detail['status'] = 'matched_unmapped'; detail['record_id'] = candidates[0]
                detail['reason'] = 'unique visible match; persist explicit row-to-record mapping before completion'
            elif len(candidates) > 1:
                detail['status'] = 'conflict'; detail['reason'] = 'multiple remote rows match the visible fields'
            else:
                detail['status'] = 'create'; create_records.append({'row_id': rid, 'fields': fields})
        statuses.append(detail)
    extra = [{'record_id': item['record_id'], 'signature': item['signature']}
             for item in remote if item.get('record_id') not in used]
    duplicate_ids = sorted({item['record_id'] for item in remote if item.get('duplicate') and item.get('record_id')})
    missing_id_count = sum(1 for item in remote if not item.get('record_id'))
    missing_field_count = sum(1 for item in remote if item.get('missing_fields'))
    counts = Counter(item['status'] for item in statuses)
    diagnostics = {'project_key': project['project_key'], 'project_fingerprint': project_fingerprint(project),
                   'review_id': state['review_id'], 'remote_path': str(Path(remote_path).resolve()),
                   'statuses': statuses, 'extra_remote_records': extra,
                   'readback_manifest': manifest, 'field_schema_path': schema_path,
                   'field_schema_problems': schema_issues,
                   'summary': {'total_target': len(rows), 'remote_count': len(remote),
                               'unchanged_count': counts['unchanged'], 'create_count': counts['create'],
                               'update_count': counts['update'], 'conflict_count': counts['conflict'],
                               'unknown_count': counts['unknown'], 'matched_unmapped_count': counts['matched_unmapped'],
                               'extra_count': len(extra), 'duplicate_record_ids': duplicate_ids,
                               'missing_record_id_count': missing_id_count,
                               'missing_field_count': missing_field_count,
                               'manifest_problem_count': len(manifest['problems']),
                               'field_schema_problem_count': len(schema_issues),
                               'safe_to_complete': not any(counts[name] for name in
                                                           ('create', 'conflict', 'unknown', 'matched_unmapped'))
                               and not duplicate_ids and not missing_id_count and not manifest['problems']
                               and not schema_issues and missing_field_count == 0 and len(extra) == 0}}
    out_dir = Path(output_dir).resolve() if output_dir else Path(remote_path).resolve().parent / 'diagnostic'
    out_dir.mkdir(parents=True, exist_ok=True)
    diag_path = write_json(out_dir / 'diagnostic.json', diagnostics)
    plan = {'project_key': project['project_key'], 'review_id': state['review_id'],
            'diagnostic_path': str(diag_path),
            'create_records': [item['fields'] for item in create_records],
            'create_row_ids': [item['row_id'] for item in create_records],
            'update_records': update_records,
            'safe_to_complete': diagnostics['summary']['safe_to_complete']}
    plan_path = write_json(out_dir / 'reconcile-plan.json', plan)
    return {'ok': True, 'diagnostic_path': str(diag_path), 'plan_path': str(plan_path), **diagnostics['summary']}


def review_input(data, source, project):
    require(data.get('document_id') == project['outputs']['document_id'], 'Review belongs to another output document')
    require(nonempty(str(data.get('document_revision', ''))) and data.get('document_revision') is not None,
            'Actual document revision required')
    examples = data.get('examples')
    require(isinstance(examples, list) and examples, 'Keep the actual approved showcase examples')
    checked = validate_rows(examples, project['bmk_plan'])
    feedback = data.get('feedback')
    require(isinstance(feedback, list), 'Chat feedback must be an array, separate from examples')
    ids = {row['id'] for row in examples}
    source_ids = data.get('source_case_ids', [row['id'] for row in examples])
    require(isinstance(source_ids, list) and all(nonempty(item) for item in source_ids)
            and len(set(source_ids)) == len(source_ids) and ids <= set(source_ids),
            'source_case_ids must contain unique actual readback ids, including every retained example')
    for item in feedback:
        require(isinstance(item, dict) and nonempty(item.get('text')) and item.get('applies_to') in ('all', 'cases'),
                'Feedback requires text and applies_to=all|cases')
        case_ids = item.get('case_ids', [])
        require(isinstance(case_ids, list) and all(isinstance(i, str) and i in source_ids for i in case_ids),
                'Case feedback must refer to actual source showcase ids')
        require(bool(case_ids) if item['applies_to'] == 'cases' else not case_ids,
                'Global feedback has no case_ids; case feedback needs case_ids')
    return {'document_id': data['document_id'], 'document_revision': data['document_revision'],
            'source_readback': evidence(data.get('source_readback'), source.parent),
            'source_case_ids': source_ids, 'examples': checked['rows'], 'feedback': copy.deepcopy(feedback)}


def current_review(state, project):
    require(state.get('project_key') == project.get('project_key'), 'Dataset state belongs to another project')
    require(state.get('project_fingerprint') == project_fingerprint(project),
            'Scope/BMK/source changed; present a new showcase review before continuing')
    review = state.get('review') or {}
    require(evidence_current(review.get('source_readback', {})), 'Showcase readback changed or missing; refresh review')
    require(state.get('review_id') == review_digest(review, state['project_fingerprint']),
            'Saved review changed; present and confirm it again')
    require((state.get('confirmation') or {}).get('review_id') == state.get('review_id'),
            'Confirm the current showcase and chat directions before generating')


def review_digest(review, fingerprint):
    # Provenance can refresh after a layout edit without changing approved content.
    return rt.digest({key: review.get(key) for key in ('document_id', 'examples', 'feedback', 'source_case_ids')}
                     | {'project_fingerprint': fingerprint})


def merge_delivery(state, incoming, source):
    require(isinstance(incoming, dict), 'Delivery metadata must be an object')
    delivery = copy.deepcopy(state.get('delivery') or {})
    for key in ('base_token', 'base_url', 'table_id'):
        if key in incoming:
            require(nonempty(incoming[key]), 'Missing delivery identity: ' + key)
            require(key not in delivery or delivery[key] == incoming[key], 'Reuse the original delivery: ' + key)
            delivery[key] = incoming[key]
    if delivery.get('base_url'):
        require(urlparse(delivery['base_url']).scheme == 'https', 'Base URL must be HTTPS')
    valid_rows = {row['id'] for row in state.get('rows', [])}
    for key, allowed in (('field_ids', set(FIELDS)), ('record_ids', valid_rows)):
        if key not in incoming:
            continue
        mapping = incoming[key]
        require(isinstance(mapping, dict) and set(mapping) <= allowed and all(nonempty(v) for v in mapping.values()),
                'Invalid ' + key)
        previous = delivery.setdefault(key, {})
        require(all(k not in previous or previous[k] == v for k, v in mapping.items()), 'Do not replace saved ' + key)
        previous.update(mapping)
        require(len(set(previous.values())) == len(previous), 'Duplicate remote ids: ' + key)
    if 'retired_record_ids' in incoming:
        retired = incoming['retired_record_ids']
        previous = delivery.setdefault('record_ids', {})
        history_ids = {rid: remote_id for entry in delivery.get('retirement_history', [])
                       for rid, remote_id in entry['record_ids'].items()}
        require(isinstance(retired, dict) and retired and all(
            rid not in valid_rows and nonempty(remote_id) and (
                previous.get(rid) == remote_id or (rid not in previous and history_ids.get(rid) == remote_id))
            for rid, remote_id in retired.items()),
            'Retirement must explicitly match saved record ids outside the current rows')
        require(nonempty(incoming.get('retirement_reason')), 'Explicit retirement reason required')
        proof = evidence(incoming.get('retirement_readback'), source.parent)
        # The model checks the actual remote rows were removed/moved. No remote
        # deletion is performed here; final normalized full readback is mandatory.
        newly_retired = {rid: remote_id for rid, remote_id in retired.items() if rid in previous}
        if newly_retired:
            delivery.setdefault('retirement_history', []).append({
                'record_ids': newly_retired, 'reason': incoming['retirement_reason'],
                'readback_evidence': proof, 'at': rt.now()})
        for rid in newly_retired:
            del previous[rid]
    return delivery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-state', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    cmd = sub.add_parser('state')
    view = cmd.add_mutually_exclusive_group()
    view.add_argument('--summary', action='store_true')
    view.add_argument('--section', choices=('review', 'rows', 'delivery'))
    cmd = sub.add_parser('rows', help='Read a bounded, optionally compact row view')
    cmd.add_argument('--id', action='append', dest='row_ids', help='Filter by internal row id; repeatable')
    cmd.add_argument('--ability', action='append', dest='abilities', help='Filter by any ability tag; repeatable')
    cmd.add_argument('--cell', help='Filter by ability|task|user|dimension primary cell')
    cmd.add_argument('--limit', type=int, default=50)
    cmd.add_argument('--offset', type=int, default=0)
    cmd.add_argument('--compact', action='store_true', help='Omit Rubrics from the row payload')
    cmd = sub.add_parser('export-base', help='Create local seven-field Lark Base write batches')
    cmd.add_argument('--output-dir', required=True)
    cmd.add_argument('--batch-size', type=int, default=200)
    cmd = sub.add_parser('diagnostic', help='Reconcile actual Lark Base readback with the local target')
    cmd.add_argument('--remote', required=True, help='Saved record-list JSON/NDJSON from lark-base')
    cmd.add_argument('--mapping', help='Optional JSON row_id to record_id mapping')
    cmd.add_argument('--previous', help='Optional previous successful record-list snapshot')
    cmd.add_argument('--manifest', help='Optional lark-base pagination/schema manifest')
    cmd.add_argument('--schema', help='Optional actual field-list/schema evidence')
    cmd.add_argument('--output-dir')
    for name in ('review', 'save', 'append', 'delivery'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--input', required=True)
        if name == 'delivery':
            cmd.add_argument('--complete', action='store_true')
    cmd = sub.add_parser('confirm')
    cmd.add_argument('--review-id', required=True)
    cmd.add_argument('--authorization', required=True)
    args = parser.parse_args()
    project_path = Path(args.project_state).resolve()
    project = rt.read(project_path)
    path = project_path.with_name('dataset-state.json')
    state = rt.read(path) if path.exists() else {'project_key': project['project_key'], 'stage': 'not_started'}
    require(state['project_key'] == project['project_key'], 'Dataset state belongs to another project')
    if args.command == 'state':
        stale = state.get('project_fingerprint') not in (None, project_fingerprint(project))
        blockers = []
        try:
            require_document_ready(project)
        except (ValueError, KeyError, TypeError) as exc:
            blockers.append(str(exc))
        if stale:
            blockers.append('Scope/BMK/source changed; refresh and confirm showcase review')
        if not state.get('review'):
            blockers.append('Showcase review has not been collected')
        elif not evidence_current(state['review'].get('source_readback', {})):
            blockers.append('Showcase readback changed or missing; refresh review')
        elif state.get('review_id') != review_digest(state['review'], state.get('project_fingerprint')):
            blockers.append('Saved review changed; present and confirm it again')
        if (state.get('confirmation') or {}).get('review_id') != state.get('review_id') or not state.get('review_id'):
            blockers.append('Current showcase review is not confirmed')
        if args.section:
            out = {key: state.get(key) for key in ('project_key', 'stage', 'review_id')}
            out.update(project_changed=stale, data=state.get(args.section))
        elif args.summary:
            out = {key: state.get(key) for key in ('project_key', 'stage', 'review_id', 'confirmation', 'rows_stale', 'delivery_stale')}
            rows = state.get('rows', [])
            out.update(row_summary(rows, project.get('bmk_plan') or {'cells': []}),
                       project_changed=stale, state_file=str(path),
                       base_url=state.get('delivery', {}).get('base_url'),
                       review_example_count=len((state.get('review') or {}).get('examples', [])),
                       feedback_count=len((state.get('review') or {}).get('feedback', [])),
                       mapped_record_count=len((state.get('delivery') or {}).get('record_ids', {})),
                       mapped_field_count=len((state.get('delivery') or {}).get('field_ids', {})),
                       append_batch_count=len(state.get('append_history', [])),
                       pending_batch_count=len((state.get('delivery') or {}).get('pending_batches', [])),
                       conflict_count=len((state.get('delivery') or {}).get('conflicts', [])),
                       next_action=('blocked:' + blockers[0] if blockers else
                                    ('confirm_review' if state.get('stage') == 'showcase_feedback_pending'
                                     or (state.get('review_id') and
                                         (state.get('confirmation') or {}).get('review_id') != state.get('review_id'))
                                     else 'deliver' if state.get('stage') == 'dataset_ready' else
                                     'append_or_save' if state.get('stage') == 'dataset_generating' else
                                     'review')))
        else:
            out = copy.deepcopy(state)
        out.update(project_changed=stale, blocked=bool(blockers), blocked_reasons=blockers)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if args.command == 'rows':
        require(isinstance(args.limit, int) and 1 <= args.limit <= 200, 'limit must be between 1 and 200')
        require(isinstance(args.offset, int) and args.offset >= 0, 'offset must be non-negative')
        cell = parse_cell_filter(args.cell) if args.cell else None
        all_rows = state.get('rows', [])
        selected = filter_rows(all_rows, args.row_ids, args.abilities, cell)
        page = selected[args.offset:args.offset + args.limit]
        next_offset = args.offset + args.limit if args.offset + args.limit < len(selected) else None
        stale = state.get('project_fingerprint') not in (None, project_fingerprint(project))
        out = {'rows': [compact_row(row) if args.compact else row for row in page],
               'count': len(page), 'matched': len(selected), 'offset': args.offset,
               'limit': args.limit, 'next_offset': next_offset,
               'project_changed': stale, 'rows_stale': bool(state.get('rows_stale')),
               'review_id': state.get('review_id')}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if args.command == 'export-base':
        out = export_base(project, state, args.output_dir, args.batch_size)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if args.command == 'diagnostic':
        out = reconcile_remote(project, state, args.remote, args.mapping, args.previous, args.output_dir,
                               args.manifest, args.schema)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    require_document_ready(project)
    if args.command == 'review':
        source = Path(args.input).resolve()
        review = review_input(rt.read(source), source, project)
        fingerprint = project_fingerprint(project)
        review_id = review_digest(review, fingerprint)
        if state.get('review_id') != review_id or state.get('project_fingerprint') != fingerprint:
            if state.get('review'):
                state.setdefault('review_history', []).append({key: state.get(key) for key in ('review_id', 'review', 'confirmation')})
            state.update(review=review, review_id=review_id, project_fingerprint=fingerprint,
                         stage='showcase_feedback_pending', rows_stale=True, delivery_stale=True)
            state.pop('confirmation', None)
        elif state.get('review') != review:
            state.setdefault('review_evidence_history', []).append({key: state['review'].get(key)
                for key in ('document_revision', 'source_readback')})
            state['review'] = review
        out = {'ok': True, 'stage': state['stage'], 'review_id': review_id,
               'example_count': len(review['examples']), 'feedback_count': len(review['feedback'])}
    elif args.command == 'confirm':
        require(nonempty(args.authorization), 'Actual explicit user confirmation is required')
        require(args.review_id == state.get('review_id'), 'Confirmation does not match current showcase review')
        require(state.get('project_fingerprint') == project_fingerprint(project), 'Project changed; refresh review')
        require(evidence_current(state.get('review', {}).get('source_readback', {})), 'Review source changed; refresh review')
        require(state['review_id'] == review_digest(state['review'], state['project_fingerprint']), 'Review was altered; refresh it')
        # Retrying the same acknowledgement must not regress a finished delivery.
        if (state.get('confirmation') or {}).get('review_id') != args.review_id:
            state['confirmation'] = {'review_id': args.review_id, 'authorization': args.authorization, 'at': rt.now()}
            state['stage'] = 'dataset_generating'
        out = {'ok': True, 'stage': state['stage']}
    else:
        current_review(state, project)
        source = Path(args.input).resolve()
        data = rt.read(source)
        if args.command == 'append':
            require(isinstance(data.get('rows'), list) and data['rows'], 'append input requires a nonempty rows array')
            existing = copy.deepcopy(state.get('rows', []))
            require(not (existing and state.get('rows_stale')),
                    'Rows are stale; use save with the current approved collection before append')
            existing_by_id = {row['id']: row for row in existing}
            skipped_ids = []
            additions = []
            for row in data['rows']:
                row_id = row.get('id') if isinstance(row, dict) else None
                if row_id in existing_by_id:
                    require(existing_by_id[row_id] == row,
                            'Existing row content changed; use explicit save and review before append: ' + str(row_id))
                    skipped_ids.append(row_id)
                else:
                    additions.append(row)
            seeded = []
            base = existing
            if not base:
                approved = copy.deepcopy(state['review']['examples'])
                approved_by_id = {row['id']: row for row in approved}
                seeded = [row['id'] for row in approved]
                base = approved
                for row in additions:
                    if row.get('id') in approved_by_id:
                        require(approved_by_id[row['id']] == row,
                                'Approved example changed; revise and confirm showcase first: ' + str(row.get('id')))
                        skipped_ids.append(row['id'])
                    else:
                        base.append(row)
            else:
                base.extend(additions)
            result = validate_rows(base, project['bmk_plan'], state['review']['examples'])
            state.update(rows=result['rows'], rows_stale=False,
                         stage='dataset_ready' if result['complete'] else 'dataset_generating',
                         delivery_stale=True)
            state.setdefault('append_history', []).append({
                'batch_digest': rt.digest({'rows': data['rows']}),
                'added_ids': [row['id'] for row in additions],
                'skipped_ids': skipped_ids,
                'seeded_ids': seeded,
                'count': len(result['rows']),
                'at': rt.now(),
            })
            out = bounded_result({key: value for key, value in result.items() if key != 'rows'}) | {
                'ok': True, 'stage': state['stage'], 'added_ids': [row['id'] for row in additions],
                'skipped_ids': skipped_ids, 'seeded_ids': seeded,
            }
        elif args.command == 'save':
            result = validate_rows(data.get('rows'), project['bmk_plan'], state['review']['examples'])
            changed = state.get('rows') != result['rows'] or state.get('rows_stale')
            if state.get('rows') and state['rows'] != result['rows']:
                state.setdefault('rows_history', []).append({'rows': state['rows'], 'at': rt.now()})
            state.update(rows=result['rows'], rows_stale=False)
            if changed:
                state.update(stage='dataset_ready' if result['complete'] else 'dataset_generating', delivery_stale=True)
            out = bounded_result({key: value for key, value in result.items() if key != 'rows'}) | {'ok': True, 'stage': state['stage']}
        else:
            require(not state.get('rows_stale', True), 'Save current approved rows before recording delivery')
            delivery = merge_delivery(state, data, source)
            if args.complete:
                validate_rows(state['rows'], project['bmk_plan'], state['review']['examples'], complete=True)
                require(all(delivery.get(key) for key in ('base_token', 'base_url', 'table_id')), 'Actual Base and table ids required')
                require(set(delivery.get('field_ids', {})) == set(FIELDS), 'All seven field ids required')
                require(set(delivery.get('record_ids', {})) == {row['id'] for row in state['rows']}, 'All record ids required')
                refs = {key: evidence(data.get(key), source.parent) for key in (
                    'readback_rows', 'readback_evidence', 'quality_review', 'document_link_readback')}
                remote_rows = rt.read(refs['readback_rows']['path']).get('rows')
                validate_rows(remote_rows, project['bmk_plan'], state['review']['examples'], complete=True)
                require({row['id']: row for row in remote_rows} == {row['id']: row for row in state['rows']},
                        'Remote rows differ, including label order; preserve user edits and resolve before completion')
                if data.get('diagnostic_evidence'):
                    diagnostic_proof = evidence(data['diagnostic_evidence'], source.parent)
                    diagnostic = rt.read(diagnostic_proof['path'])
                    require(isinstance(diagnostic.get('summary'), dict)
                            and diagnostic['summary'].get('safe_to_complete') is True,
                            'Remote reconciliation diagnostic has conflicts or incomplete evidence')
                    delivery['diagnostic_evidence'] = diagnostic_proof
                delivery.update(refs)
                state.update(stage='dataset_complete', delivery_stale=False)
            else:
                state.update(stage='dataset_writing', delivery_stale=True)
            state['delivery'] = delivery
            out = {'ok': True, 'stage': state['stage'], 'base_url': delivery.get('base_url')}
    state['updated_at'] = rt.now()
    rt.save(path, state)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
