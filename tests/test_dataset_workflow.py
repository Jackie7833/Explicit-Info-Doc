"""Public CLI tests with temporary files only; no Lark calls or real project writes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/dataset_workflow.py'
FIELDS = ('Query', '维度', '场景分布', '用户场景', '能力', '预置信息', 'Rubrics')


class DatasetWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='explicit-dataset-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = {
            'total': 3, 'dimensions': {'breadth': 1, 'thickness': 1, 'depth': 1},
            'user_scenarios': [{'id': 'u', 'name': '办公', 'count': 3}],
            'abilities': [
                {'id': 'a', 'name': '文档处理', 'count': 2, 'dimensions': {'breadth': 1, 'thickness': 1, 'depth': 0},
                 'task_scenarios': [{'id': 't', 'name': '文档整理', 'count': 2}]},
                {'id': 'b', 'name': '消息发送', 'count': 1, 'dimensions': {'breadth': 0, 'thickness': 0, 'depth': 1},
                 'task_scenarios': [{'id': 't', 'name': '发送消息', 'count': 1}]},
                {'id': 'c', 'name': '查询联系人', 'count': 0, 'dimensions': {'breadth': 0, 'thickness': 0, 'depth': 0},
                 'task_scenarios': [{'id': 'c', 'name': '查找同事', 'count': 0}]},
            ],
            'cells': [dict(ability_id=a, task_scenario_id='t', user_scenario_id='u', dimension=d, count=1)
                      for a, d in [('a', 'breadth'), ('a', 'thickness'), ('b', 'depth')]],
        }
        self.rows = [self.row('q1', 'a', 'breadth'), self.row('q2', 'a', 'thickness'), self.row('q3', 'b', 'depth')]
        self.rows[1]['ability_ids'] = ['a', 'b', 'c']
        self.rows[1]['task_scenarios'] += [{'ability_id': 'b', 'task_scenario_id': 't'}, {'ability_id': 'c', 'task_scenario_id': 'c'}]
        self.project = {
            'project_key': 'dataset-test', 'input_document_type': 'evaluation_description',
            'source_document': {'canonical_id': 'source-doc'}, 'stage': 'complete', 'outputs_stale': False,
            'range_ready': True, 'user_scenarios_confirmed': True, 'bmk_plan_confirmed': True,
            'evaluation_scope': [{'name': item['name']} for item in self.plan['abilities']],
            'scope_basis': {'source': 'scope'}, 'user_scenarios': [{'id': 'u', 'name': '办公'}], 'bmk_plan': self.plan,
            'outputs': {'document_id': 'doc', 'document_url': 'https://example.org/docx/doc', 'board_id': 'board',
                        'readback_evidence': 'prior-document.json', 'board_preview_evidence': 'prior-board.png',
                        'showcase_quality_review': 'prior-quality.json'},
        }
        self.project_path = self.write('state.json', self.project)
        self.original = self.project_path.read_bytes()
        self.raw = self.write('source-readback.json', {'document_id': 'doc', 'revision': 8, 'rows': [self.rows[0]]})
        self.review = {'document_id': 'doc', 'document_revision': 8, 'source_readback': self.raw.name,
                       'examples': [self.rows[0]], 'feedback': [{'text': '题目表达自然', 'applies_to': 'all'}]}
        self.delivery = {'base_token': 'base', 'base_url': 'https://example.org/base/base', 'table_id': 'table',
                         'field_ids': {name: 'field' + str(i) for i, name in enumerate(FIELDS)},
                         'record_ids': {row['id']: 'record-' + row['id'] for row in self.rows}}

    @staticmethod
    def row(rid, aid, dimension):
        return {'id': rid, 'query': '测试任务 ' + rid, 'dimension': dimension, 'ability_ids': [aid],
                'task_scenarios': [{'ability_id': aid, 'task_scenario_id': 't'}], 'user_scenario_id': 'u',
                'preconditions': '', 'rubrics': ['返回正确结果', '保留指定内容']}

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False))
        return path

    def run_cli(self, *args, fails=False):
        proc = subprocess.run([sys.executable, '-B', str(SCRIPT), '--project-state', str(self.project_path), *map(str, args)],
                              capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(proc.returncode == 0, not fails, proc.stdout + proc.stderr)
        return json.loads(proc.stderr if fails else proc.stdout)

    def start(self, confirm=True):
        result = self.run_cli('review', '--input', self.write('review.json', self.review))
        if confirm:
            self.run_cli('confirm', '--review-id', result['review_id'], '--authorization', '确认当前示例和修改方向，开始生成')
        return result

    def save_rows(self, rows=None, fails=False):
        return self.run_cli('save', '--input', self.write('rows.json', {'rows': rows if rows is not None else self.rows}), fails=fails)

    def completion_input(self, rows=None):
        data = copy.deepcopy(self.delivery)
        data['readback_rows'] = str(self.write('normalized-readback.json', {'rows': rows if rows is not None else self.rows}))
        for field in ('readback_evidence', 'quality_review', 'document_link_readback'):
            data[field] = str(self.write(field + '.json', {'actual_readback': field}))
        return self.write('delivery.json', data)

    def run_runtime(self, *args):
        proc = subprocess.run([sys.executable, '-B', str(SCRIPT.with_name('runtime.py')),
                               '--state-root', str(self.root / 'runtime'), *map(str, args)],
                              capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def confirm_runtime(self, proposal):
        self.run_runtime('confirm', self.runtime_key, '--proposal-id', proposal['proposal_id'], '--authorization', '确认')

    def finish_runtime(self, plan=None):
        self.confirm_runtime(self.run_runtime('propose', self.runtime_key, '--kind', 'scenarios', '--input',
                            self.write('scenarios.json', [{'id': 'u', 'name': '办公', 'purpose': '完成办公任务'}])))
        self.confirm_runtime(self.run_runtime('propose', self.runtime_key, '--kind', 'bmk', '--input',
                            self.write('plan.json', plan or self.plan)))
        self.run_runtime('outputs', self.runtime_key, '--input', self.write('outputs.json', self.project['outputs']), '--complete')

    def init_runtime_project(self):
        self.scope_input = {'input_document_type': 'evaluation_description',
                           'source_document': self.project['source_document'],
                           'scope_basis': {'source': 'confirmed document'}, 'shared_boundaries': ['返回查询结果'],
                           'explicit_exclusions': ['外部资源'], 'exclusions_status': 'confirmed',
                           'evaluation_scope': [{'name': item['name'], 'user_needs': ['处理需求'],
                                                'scope_points': ['完成相应任务'], 'source_refs': ['source-doc']}
                                               for item in self.plan['abilities']]}
        result = self.run_runtime('init-description', '--input', self.write('scope.json', self.scope_input))
        self.runtime_key = result['project_key']
        self.project_path = Path(result['state_file'])
        self.confirm_runtime(result['pending'])
        self.finish_runtime()

    def test_help_and_readonly_views_do_not_modify_project(self):
        proc = subprocess.run([sys.executable, '-B', str(SCRIPT), '--help'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn('review', proc.stdout)
        result = self.run_cli('state', '--summary')
        self.assertEqual(result['stage'], 'not_started')
        self.assertFalse((self.root / 'dataset-state.json').exists())
        self.start()
        before = (self.root / 'dataset-state.json').read_bytes()
        for view in ('review', 'rows', 'delivery'):
            self.run_cli('state', '--section', view)
        self.assertEqual((self.root / 'dataset-state.json').read_bytes(), before)
        self.assertEqual(self.project_path.read_bytes(), self.original)

    def test_document_and_whiteboard_delivery_are_prerequisites(self):
        self.project['outputs'].pop('board_preview_evidence')
        self.write('state.json', self.project)
        self.run_cli('review', '--input', self.write('review.json', self.review), fails=True)
        self.assertFalse((self.root / 'dataset-state.json').exists())

    def test_unconfirmed_showcase_cannot_generate_or_deliver(self):
        self.start(confirm=False)
        self.save_rows(fails=True)
        self.run_cli('delivery', '--input', self.write('delivery.json', self.delivery), fails=True)
        self.run_cli('confirm', '--review-id', 'wrong', '--authorization', '确认', fails=True)
        self.assertEqual(self.project_path.read_bytes(), self.original)

    def test_end_to_end_retains_showcase_counts_primary_tags_and_verifies_readback(self):
        self.start()
        partial = self.save_rows(self.rows[:2])
        self.assertEqual(partial['count'], 2)
        self.assertEqual(sum(c['count'] for c in partial['remaining']), 1)
        self.assertEqual(partial['remaining'][0]['ability_id'], 'b')
        full = self.save_rows()
        self.assertTrue(full['complete'])
        self.run_cli('delivery', '--input', self.write('identity.json', self.delivery))
        result = self.run_cli('delivery', '--input', self.completion_input(), '--complete')
        self.assertEqual(result['stage'], 'dataset_complete')
        self.assertEqual(self.project_path.read_bytes(), self.original)

    def test_append_seeds_approved_examples_and_is_idempotent(self):
        self.start()
        first = self.run_cli('append', '--input', self.write('batch-one.json', {'rows': [self.rows[1]]}))
        self.assertEqual(first['seeded_ids'], ['q1'])
        self.assertEqual(first['added_ids'], ['q2'])
        self.assertEqual(first['count'], 2)
        repeated = self.run_cli('append', '--input', self.write('batch-repeat.json', {'rows': [self.rows[1]]}))
        self.assertEqual(repeated['added_ids'], [])
        self.assertEqual(repeated['skipped_ids'], ['q2'])
        self.assertEqual(repeated['count'], 2)
        self.assertEqual(self.run_cli('state', '--summary')['append_batch_count'], 2)
        stored = self.run_cli('state', '--section', 'rows')['data']
        self.assertEqual([row['id'] for row in stored], ['q1', 'q2'])

    def test_rows_query_is_bounded_filterable_and_compact(self):
        self.start()
        self.save_rows(self.rows)
        page = self.run_cli('rows', '--ability', 'a', '--limit', '1', '--compact')
        self.assertEqual(page['matched'], 2)
        self.assertEqual(page['count'], 1)
        self.assertIsNotNone(page['next_offset'])
        self.assertNotIn('rubrics', page['rows'][0])
        cell = self.run_cli('rows', '--cell', 'a|t|u|thickness')
        self.assertEqual([row['id'] for row in cell['rows']], ['q2'])

    def test_summary_reports_gaps_and_delivery_counts_without_rows(self):
        self.start()
        self.save_rows(self.rows[:2])
        summary = self.run_cli('state', '--summary')
        self.assertEqual(summary['row_count'], 2)
        self.assertEqual(summary['remaining_count'], 1)
        self.assertEqual(summary['by_dimension'], {'breadth': 1, 'thickness': 1})
        self.assertEqual(summary['mapped_record_count'], 0)
        self.assertNotIn('rows', summary)

    def test_new_feedback_invalidates_confirmation_but_keeps_remote_ids(self):
        old = self.start()
        self.save_rows()
        self.run_cli('delivery', '--input', self.write('delivery.json', self.delivery))
        self.review['feedback'][0]['text'] = '更简洁，突出结果'
        new = self.start(confirm=False)
        self.assertNotEqual(new['review_id'], old['review_id'])
        self.save_rows(fails=True)
        self.run_cli('confirm', '--review-id', old['review_id'], '--authorization', '确认旧版', fails=True)
        stored = self.run_cli('state', '--section', 'delivery')['data']
        self.assertEqual(stored['record_ids'], self.delivery['record_ids'])

    def test_replaying_same_review_and_confirmation_preserves_completed_delivery(self):
        original = self.start()
        self.save_rows()
        self.run_cli('delivery', '--input', self.completion_input(), '--complete')
        review = self.start(confirm=False)
        self.assertEqual(review['review_id'], original['review_id'])
        self.assertEqual(review['stage'], 'dataset_complete')
        repeated = self.run_cli('confirm', '--review-id', review['review_id'], '--authorization', '确认')
        self.assertEqual(repeated['stage'], 'dataset_complete')

    def test_upstream_scope_change_blocks_save_and_delivery_without_mutating_source(self):
        self.start()
        self.save_rows()
        self.project['scope_basis']['source'] = 'changed-scope'
        self.write('state.json', self.project)
        self.assertTrue(self.run_cli('state', '--summary')['project_changed'])
        self.save_rows(fails=True)
        self.run_cli('delivery', '--input', self.write('delivery.json', self.delivery), fails=True)

    def test_readback_file_change_invalidates_review_confirmation(self):
        review = self.start(confirm=False)
        self.raw.write_text('new content')
        self.run_cli('confirm', '--review-id', review['review_id'], '--authorization', '确认', fails=True)

    def test_new_provenance_with_same_approved_content_preserves_completion(self):
        old = self.start()
        self.save_rows()
        self.run_cli('delivery', '--input', self.completion_input(), '--complete')
        self.review['document_revision'] = 9
        self.review['source_readback'] = str(self.write('revision-9.json', {'actual_document': 'new layout, same cases'}))
        refreshed = self.start(confirm=False)
        self.assertEqual(refreshed['review_id'], old['review_id'])
        self.assertEqual(refreshed['stage'], 'dataset_complete')
        self.assertFalse(self.run_cli('state', '--summary')['blocked'])
        stored = self.run_cli('state', '--section', 'review')['data']
        self.assertEqual(stored['document_revision'], 9)
        self.assertTrue(stored['source_readback']['path'].endswith('revision-9.json'))

    def test_feedback_on_removed_source_case_is_preserved(self):
        self.review['source_case_ids'] = ['q1', 'q2']
        self.review['feedback'] = [{'text': '第二题重复，不保留', 'applies_to': 'cases', 'case_ids': ['q2']}]
        self.start()
        stored = self.run_cli('state', '--section', 'review')['data']
        self.assertEqual([row['id'] for row in stored['examples']], ['q1'])
        self.assertEqual(stored['feedback'][0]['case_ids'], ['q2'])
        self.review['source_case_ids'] = ['q2']
        self.run_cli('review', '--input', self.write('invalid-review.json', self.review), fails=True)

    def test_summary_blocks_upstream_not_ready_even_when_fingerprint_unchanged(self):
        self.start()
        for key, value in [('range_ready', False), ('user_scenarios_confirmed', False),
                           ('bmk_plan_confirmed', False), ('outputs_stale', True), ('stage', 'document_pending')]:
            project = copy.deepcopy(self.project)
            project[key] = value
            self.write('state.json', project)
            before = self.project_path.read_bytes()
            summary = self.run_cli('state', '--summary')
            self.assertFalse(summary['project_changed'])
            self.assertTrue(summary['blocked'])
            self.assertTrue(summary['blocked_reasons'])
            self.save_rows(fails=True)
            self.assertEqual(self.project_path.read_bytes(), before)

    def test_runtime_presentation_edit_does_not_invalidate_dataset_confirmation(self):
        self.init_runtime_project()
        self.start()
        self.save_rows()
        self.scope_input['evaluation_scope'][0]['scope_points'] = ['执行对应任务']
        self.run_runtime('set-scope', self.runtime_key, '--input', self.write('wording.json', self.scope_input),
                         '--presentation-only', '--reason', '仅同义措辞，业务范围不变')
        self.assertTrue(self.run_cli('state', '--summary')['blocked'])  # document update is still required
        self.run_runtime('outputs', self.runtime_key, '--input', self.write('outputs.json', self.project['outputs']), '--complete')
        summary = self.run_cli('state', '--summary')
        self.assertFalse(summary['project_changed'])
        self.assertFalse(summary['blocked'])
        self.save_rows()

    def test_runtime_business_boundaries_reconfirmation_invalidates_dataset(self):
        self.init_runtime_project()
        for field, new_value in [('shared_boundaries', ['仅处理工作区内结果']),
                                 ('explicit_exclusions', ['已归档资源']), ('exclusions_status', 'revised')]:
            self.start()
            self.save_rows()
            self.run_runtime('invalidate', self.runtime_key, '--from-stage', 'range', '--reason', '业务范围调整')
            self.scope_input[field] = new_value
            scope = self.run_runtime('set-scope', self.runtime_key, '--input', self.write('changed.json', self.scope_input))
            self.confirm_runtime(scope['pending'])
            self.finish_runtime()
            self.assertTrue(self.run_cli('state', '--summary')['project_changed'])
            self.save_rows(fails=True)

    def test_confirmed_reduced_plan_can_explicitly_retire_old_records(self):
        self.init_runtime_project()
        self.start()
        self.save_rows()
        self.run_cli('delivery', '--input', self.completion_input(), '--complete')
        plan = copy.deepcopy(self.plan)
        plan['total'] = plan['user_scenarios'][0]['count'] = 2
        plan['dimensions']['depth'] = 0
        plan['abilities'][1]['count'] = plan['abilities'][1]['dimensions']['depth'] = 0
        plan['abilities'][1]['task_scenarios'][0]['count'] = 0
        plan['cells'] = plan['cells'][:2]
        self.run_runtime('invalidate', self.runtime_key, '--from-stage', 'bmk', '--reason', '用户确认减少总题量')
        self.confirm_runtime(self.run_runtime('propose', self.runtime_key, '--kind', 'bmk', '--input', self.write('reduced.json', plan)))
        self.run_runtime('outputs', self.runtime_key, '--input', self.write('outputs.json', self.project['outputs']), '--complete')
        self.start()
        self.save_rows(self.rows[:2])
        self.delivery['record_ids'].pop('q3')
        path = self.completion_input(self.rows[:2])
        self.run_cli('delivery', '--input', path, '--complete', fails=True)
        data = json.loads(path.read_text())
        data.update(retired_record_ids={'q3': 'record-q3'}, retirement_reason='用户减少题量，远端该行已移出')
        self.run_cli('delivery', '--input', self.write('retirement.json', data), '--complete', fails=True)
        data['retirement_readback'] = str(self.write('retirement-readback.json', {'records': ['record-q1', 'record-q2']}))
        self.run_cli('delivery', '--input', self.write('retirement.json', data))
        self.run_cli('delivery', '--input', self.write('retirement.json', data), '--complete')
        self.run_cli('delivery', '--input', self.write('retirement.json', data), '--complete')
        wrong_retirement = copy.deepcopy(data)
        wrong_retirement['retired_record_ids']['q3'] = 'record-impostor'
        self.run_cli('delivery', '--input', self.write('wrong-retirement.json', wrong_retirement), '--complete', fails=True)
        state = self.run_cli('state')
        self.assertEqual(state['stage'], 'dataset_complete')
        self.assertNotIn('q3', state['delivery']['record_ids'])
        self.assertEqual(state['delivery']['retirement_history'][0]['record_ids'], {'q3': 'record-q3'})
        self.assertEqual(len(state['delivery']['retirement_history']), 1)
        self.assertEqual(len(state['rows_history'][-1]['rows']), 3)

    def test_retirement_refuses_current_rows_and_unknown_identity(self):
        self.start()
        self.save_rows()
        self.run_cli('delivery', '--input', self.completion_input(), '--complete')
        for rid, remote_id in [('q1', 'record-q1'), ('missing-id', 'record-q3')]:
            data = {'retired_record_ids': {rid: remote_id}, 'retirement_reason': '错误退役',
                    'retirement_readback': str(self.raw)}
            self.run_cli('delivery', '--input', self.write('invalid-retirement.json', data), fails=True)

    def test_accepted_showcase_cannot_be_dropped_or_rewritten(self):
        self.start()
        self.save_rows(self.rows[1:], fails=True)
        rows = copy.deepcopy(self.rows)
        rows[0]['query'] = '悄悄替换认可题'
        self.save_rows(rows, fails=True)

    def test_quota_conflict_and_duplicate_query_rejected(self):
        self.start()
        rows = copy.deepcopy(self.rows)
        rows[1]['dimension'] = 'breadth'
        self.save_rows(rows, fails=True)
        rows = copy.deepcopy(self.rows)
        rows[1]['query'] = rows[0]['query']
        self.save_rows(rows, fails=True)

    def test_labels_must_exist_and_primary_task_matches_primary_ability(self):
        self.start()
        for change in ('parent', 'unknown', 'duplicate', 'single-user', 'preconditions-placeholder'):
            rows = copy.deepcopy(self.rows)
            if change == 'parent':
                rows[1]['task_scenarios'].reverse()
            elif change == 'unknown':
                rows[1]['ability_ids'].append('invented')
            elif change == 'duplicate':
                rows[1]['ability_ids'].append('a')
            elif change == 'single-user':
                rows[1]['user_scenario_id'] = ['u']
            else:
                rows[1]['preconditions'] = ' — '
            self.save_rows(rows, fails=True)

    def test_remote_label_order_change_cannot_pass_completion(self):
        self.start()
        self.save_rows()
        rows = copy.deepcopy(self.rows)
        rows[1]['ability_ids'] = ['a', 'c', 'b']
        result = self.run_cli('delivery', '--input', self.completion_input(rows), '--complete', fails=True)
        self.assertIn('label order', result['error'])

    def test_completion_requires_full_count_actual_evidence_and_ids(self):
        self.start()
        self.save_rows(self.rows[:1])
        self.run_cli('delivery', '--input', self.completion_input(), '--complete', fails=True)
        self.save_rows()
        self.run_cli('delivery', '--input', self.write('metadata.json', self.delivery), '--complete', fails=True)
        self.delivery['record_ids'].pop('q3')
        self.run_cli('delivery', '--input', self.completion_input(), '--complete', fails=True)

    def test_resume_cannot_recreate_base_or_replace_record_identity(self):
        self.start()
        self.save_rows()
        self.run_cli('delivery', '--input', self.write('metadata.json', self.delivery))
        changed = copy.deepcopy(self.delivery)
        changed['base_token'] = 'another-base'
        self.run_cli('delivery', '--input', self.write('newbase.json', changed), fails=True)
        changed = copy.deepcopy(self.delivery)
        changed['record_ids']['q2'] = 'another-record'
        self.run_cli('delivery', '--input', self.write('newrecord.json', changed), fails=True)

    def _export_fixture(self):
        self.start()
        self.save_rows()
        result = self.run_cli('export-base', '--output-dir', self.root / 'base-export', '--batch-size', '2')
        plan = json.loads((self.root / 'base-export' / 'export-plan.json').read_text())
        remote = []
        for batch in plan['batches']:
            payload = json.loads(Path(batch['path']).read_text())
            for index, fields in enumerate(payload['create_records']):
                row_id = batch['row_ids'][index]
                remote.append({'record_id': 'record-' + row_id, 'fields': fields})
        remote_path = self.root / 'remote.ndjson'
        remote_path.write_text('\n'.join(json.dumps(item, ensure_ascii=False) for item in remote) + '\n')
        manifest_path = self.write('remote.manifest.json', {'records_count': len(remote), 'has_more': False,
                                                            'next_offset': None, 'rev': 'r1'})
        mapping_path = self.write('mapping.json', {row['id']: 'record-' + row['id'] for row in self.rows})
        return result, remote, remote_path, mapping_path, manifest_path

    def test_export_base_is_bounded_and_contains_only_visible_fields(self):
        result, _, _, _, _ = self._export_fixture()
        self.assertEqual(result['record_count'], 3)
        self.assertEqual(result['batch_count'], 2)
        batch = json.loads((self.root / 'base-export' / 'batch-001.json').read_text())
        self.assertLessEqual(len(batch['create_records']), 2)
        self.assertEqual(set(batch['create_records'][0]), set(FIELDS))
        self.assertNotIn('id', json.dumps(batch, ensure_ascii=False))
        self.assertIn('R1:', batch['create_records'][0]['Rubrics'])

    def test_diagnostic_matches_readback_without_order_assumption(self):
        _, remote, remote_path, mapping_path, manifest_path = self._export_fixture()
        remote.reverse()
        remote_path.write_text('\n'.join(json.dumps(item, ensure_ascii=False) for item in remote) + '\n')
        result = self.run_cli('diagnostic', '--remote', remote_path, '--mapping', mapping_path,
                              '--manifest', manifest_path, '--output-dir', self.root / 'diag')
        self.assertEqual(result['unchanged_count'], 3)
        self.assertEqual(result['conflict_count'], 0)
        self.assertTrue(result['safe_to_complete'])

    def test_diagnostic_emits_safe_update_and_blocks_manual_conflict(self):
        _, remote, remote_path, mapping_path, manifest_path = self._export_fixture()
        previous = self.root / 'previous.ndjson'
        previous.write_text('\n'.join(json.dumps(item, ensure_ascii=False) for item in remote) + '\n')
        current = copy.deepcopy(remote)
        self.rows[1]['preconditions'] = '已有文档：项目说明'
        self.save_rows(self.rows)
        result = self.run_cli('diagnostic', '--remote', remote_path, '--mapping', mapping_path,
                              '--manifest', manifest_path, '--previous', previous, '--output-dir', self.root / 'diag-update')
        self.assertEqual(result['update_count'], 1)
        plan = json.loads((self.root / 'diag-update' / 'reconcile-plan.json').read_text())
        self.assertIn('record-q2', plan['update_records'])
        current[1]['fields']['Query'] = '人工修改过的题目'
        remote_path.write_text('\n'.join(json.dumps(item, ensure_ascii=False) for item in current) + '\n')
        conflict = self.run_cli('diagnostic', '--remote', remote_path, '--mapping', mapping_path,
                                '--manifest', manifest_path, '--previous', previous, '--output-dir', self.root / 'diag-conflict')
        self.assertEqual(conflict['conflict_count'], 1)
        self.assertFalse(conflict['safe_to_complete'])

    def test_diagnostic_requires_pagination_manifest_for_safe_completion(self):
        _, remote, remote_path, mapping_path, _ = self._export_fixture()
        result = self.run_cli('diagnostic', '--remote', remote_path, '--mapping', mapping_path,
                              '--output-dir', self.root / 'diag-no-manifest')
        self.assertEqual(result['manifest_problem_count'], 1)
        self.assertFalse(result['safe_to_complete'])

    def test_diagnostic_rejects_duplicate_remote_ids_as_conflict(self):
        _, remote, remote_path, mapping_path, manifest_path = self._export_fixture()
        remote.append(copy.deepcopy(remote[0]))
        remote_path.write_text('\n'.join(json.dumps(item, ensure_ascii=False) for item in remote) + '\n')
        manifest_path.write_text(json.dumps({'records_count': len(remote), 'has_more': False, 'next_offset': None}))
        result = self.run_cli('diagnostic', '--remote', remote_path, '--mapping', mapping_path,
                              '--manifest', manifest_path, '--output-dir', self.root / 'diag-duplicate')
        self.assertFalse(result['safe_to_complete'])
        self.assertTrue(result['duplicate_record_ids'])


if __name__ == '__main__':
    unittest.main()
