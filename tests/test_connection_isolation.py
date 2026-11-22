"""Public-command regressions using a relocated Skill and fake CLI under /tmp."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
sql=sys.stdin.read()
with open(os.environ['EXPLICIT_TEST_LOG'],'a') as output:
    output.write(json.dumps({'args':args,'sql':sql})+'\n')
path=Path(os.environ['EXPLICIT_TEST_DB'])
db=json.loads(path.read_text())
key=args[args.index('--app-id')+1]+':'+args[args.index('--environment')+1]
if sql.startswith('SELECT '):
    result=[db[key]] if key in db else []
elif sql.startswith('INSERT '):
    db[key]=json.loads(Path(os.environ['EXPLICIT_TEST_ROW']).read_text())
    path.write_text(json.dumps(db));result={'rows_affected':1}
elif sql.startswith('UPDATE '):
    db[key]['status']='confirmed'
    db[key]['_updated_at']='2026-11-17T01:00:00Z'
    path.write_text(json.dumps(db));result={'rows_affected':1}
else:
    raise RuntimeError('Unexpected SQL in offline test')
print(json.dumps({'ok':True,'data':result}))
'''


class ConnectionIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='explicit-connection-test-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.skill = self.root / 'copied skill'
        (self.skill / 'scripts').mkdir(parents=True)
        (self.skill / 'references/rules').mkdir(parents=True)
        shutil.copy2(SKILL / 'scripts/runtime.py', self.skill / 'scripts/runtime.py')
        self.runtime = self.skill / 'scripts/runtime.py'
        self.a = {'app_id': 'app_auditA', 'app_url': 'https://audit.example/app/app_auditA', 'environment': 'online'}
        self.b = {'app_id': 'app_auditB', 'app_url': 'https://audit.example/app/app_auditB', 'environment': 'online'}
        self.preview = {**self.a, 'environment': 'preview'}
        self.config_a = self.file('a.json', self.a)
        self.config_b = self.file('b.json', self.b)
        self.config_preview = self.file('preview.json', self.preview)
        self.file('copied skill/references/rules/application.json', self.a)
        self.cli = self.root / 'fake lark cli'
        self.cli.write_text(FAKE_CLI)
        self.cli.chmod(0o755)
        self.log = self.root / 'calls.jsonl'
        self.db = self.file('db.json', {})
        self.env = dict(os.environ, EXPLICIT_TEST_LOG=str(self.log), EXPLICIT_TEST_DB=str(self.db),
                        EXPLICIT_TEST_ROW=str(self.root / 'row.json'), PYTHONDONTWRITEBYTECODE='1')
        feature = {'id': 'f1', 'name': '保存草稿', 'priority': 'P0', 'priorityEvidence': ['P0'],
                   'sourceRef': {'document_id': 'source-A', 'block_id': 'p1'},
                   'evaluationClass': 'business', 'evaluationReason': '来源支持真实用户功能', 'defaultSelected': True}
        self.snapshot = {'source_document': {'canonical_id': 'source-A', 'url': 'https://source.example/docx/source-A'},
                         'featureCatalog': [feature]}
        self.snapshot_file = self.file('snapshot.json', self.snapshot)
        ability = {'id': 'a1', 'name': '草稿管理', 'featureIds': ['f1'],
                   'needs': [{'id': 'n1', 'name': '保存草稿', 'featureIds': ['f1']}]}
        model = self.file('model.json', {'abilitiesByScope': {'p0': [ability], 'all': [ability]}})
        self.project_file = self.root / 'project.json'
        self.command('prepare', '--snapshot', self.snapshot_file, '--abilities', model,
                     '--object', '模拟项目', '--tool-type', 'MCP', '--output', self.project_file)
        self.project = json.loads(self.project_file.read_text())
        self.key = self.project['projectKey']
        self.state_path = self.root / 'states' / self.key / 'state.json'
        columns = {'projectKey': 'project_key', 'evaluationObject': 'evaluation_object', 'toolType': 'tool_type',
                   'version': 'version', 'prdUrl': 'prd_url', 'featureCatalog': 'feature_catalog',
                   'scopeMode': 'scope_mode', 'coverageMode': 'coverage_mode', 'excludedFeatureIds': 'excluded_feature_ids',
                   'exclusionNote': 'exclusion_note', 'abilities': 'abilities', 'scopeVariants': 'scope_variants', 'status': 'status'}
        self.row = {column: self.project[field] for field, column in columns.items()}
        self.row.update(_created_at='2026-11-17T00:00:00Z', _updated_at='2026-11-17T00:00:00Z')
        self.file('row.json', self.row)

    def file(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        return path

    def command(self, command, *args, config=None, ok=True, state_root=None):
        argv = [sys.executable, str(self.runtime), '--cli', str(self.cli), '--state-root', str(state_root or self.root / 'states')]
        if config is not None:
            argv += ['--connection', str(config)]
        result = subprocess.run(argv + [command, *map(str, args)], cwd=self.root, env=self.env, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return json.loads(result.stderr)['error']

    def create(self, **kwargs):
        return self.command('create', '--snapshot', self.snapshot_file, '--project', self.project_file,
                            '--authorization', '本次离线测试创建项目', **kwargs)

    def state(self):
        return json.loads(self.state_path.read_text())

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def legacy(self):
        self.create()
        state = self.state()
        del state['connection_binding']
        state.update(stage='complete', range_ready=True, user_scenarios_confirmed=True,
                     bmk_plan_confirmed=True, outputs_stale=False,
                     outputs={'document_id': 'existing-document', 'board_id': 'existing-board'})
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=3) + '\n')
        return state

    def test_new_project_records_actual_connection(self):
        self.create(config=self.config_preview)
        self.assertEqual(self.state()['connection_binding'], {'app_id': 'app_auditA', 'environment': 'preview'})
        self.assertTrue(any(call['sql'].startswith('INSERT ') for call in self.calls()))

    def test_different_app_and_environment_refused_before_any_remote_call(self):
        self.create()
        before = self.state_path.read_bytes()
        commands = [('create', '--snapshot', self.snapshot_file, '--project', self.project_file, '--authorization', 'yes'),
                    ('read-project', self.key), ('confirm-default', self.key, '--authorization', 'yes')]
        for config in (self.config_b, self.config_preview):
            for command in commands:
                with self.subTest(config=config.name, command=command[0]):
                    count = len(self.calls())
                    error = self.command(*command, config=config, ok=False)
                    self.assertIn('--state-root', error)
                    self.assertEqual(self.state_path.read_bytes(), before)
                    self.assertEqual(len(self.calls()), count)

    def test_same_connection_create_read_and_default_confirmation(self):
        self.create(config=self.config_a)
        self.assertTrue(self.create(config=self.config_a)['reused'])
        self.command('read-project', self.key, config=self.config_a)
        self.command('confirm-default', self.key, '--authorization', '仅本项目采用默认范围', config=self.config_a)
        self.assertTrue(self.state()['range_ready'])
        self.assertEqual(self.state()['range_confirmation']['origin'], 'user-preauthorized-default')

    def test_all_local_mutations_check_binding(self):
        self.create()
        before = self.state_path.read_bytes()
        data = self.file('unused.json', {})
        commands = [('propose', self.key, '--kind', 'scenarios', '--input', data),
                    ('confirm', self.key, '--proposal-id', 'unused', '--authorization', 'yes'),
                    ('invalidate', self.key, '--from-stage', 'range', '--reason', 'test'),
                    ('set-scope', self.key, '--input', data), ('outputs', self.key, '--input', data)]
        for config in (self.config_b, self.config_preview):
            for command in commands:
                with self.subTest(config=config.name, command=command[0]):
                    self.assertIn('--state-root', self.command(*command, config=config, ok=False))
                    self.assertEqual(self.state_path.read_bytes(), before)

    def test_state_reads_show_actual_binding_and_reject_explicit_conflict(self):
        self.create(config=self.config_preview)
        # No explicit connection: a local read must report preview, not assume the default online app.
        for view in (['--summary'], ['--section', 'outputs']):
            summary = self.command('state', self.key, *view)
            self.assertEqual(summary['connection_binding']['environment'], 'preview')
            self.assertEqual(summary['connection_binding_status'], 'bound')
            self.command('state', self.key, *view, config=self.config_a, ok=False)

    def test_unbound_legacy_state_is_preserved_and_remains_readable(self):
        self.legacy()
        before = self.state_path.read_bytes()
        count = len(self.calls())
        self.assertIn('--legacy-connection', self.create(ok=False))
        self.command('read-project', self.key, ok=False)
        self.command('confirm-default', self.key, '--authorization', 'yes', ok=False)
        self.command('invalidate', self.key, '--from-stage', 'range', '--reason', 'test', ok=False)
        for view in (['--summary'], ['--section', 'outputs']):
            summary = self.command('state', self.key, *view, config=self.config_b)
            self.assertIsNone(summary['connection_binding'])
            self.assertEqual(summary['connection_binding_status'], 'missing')
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(len(self.calls()), count)

    def test_legacy_binding_verification_only_adds_binding_and_event(self):
        before = self.legacy()
        count = len(self.calls())
        result = self.command('read-project', self.key, '--legacy-connection', self.config_a, config=self.config_a)
        self.assertTrue(result['legacy_binding_verified'])
        self.assertEqual(result['local_state_update'], 'connection_binding_only')
        after = self.state()
        self.assertEqual(after['connection_binding'], {'app_id': 'app_auditA', 'environment': 'online'})
        for key in before.keys() - {'events', 'updated_at'}:
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(len(after['events']), len(before['events']) + 1)
        self.assertTrue(all(call['sql'].startswith('SELECT ') for call in self.calls()[count:]))

    def test_legacy_connection_mismatch_or_unspecified_environment_refused(self):
        self.legacy()
        before = self.state_path.read_bytes()
        count = len(self.calls())
        incomplete = self.file('old-without-env.json', {key: value for key, value in self.a.items() if key != 'environment'})
        for original, current in ((self.config_a, self.config_b), (self.config_a, self.config_preview),
                                  (self.config_b, self.config_b), (incomplete, self.config_a)):
            with self.subTest(original=original.name, current=current.name):
                self.command('read-project', self.key, '--legacy-connection', original, config=current, ok=False)
                self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(len(self.calls()), count)

    def test_legacy_remote_evidence_mismatch_preserves_exact_state(self):
        self.legacy()
        before = self.state_path.read_bytes()
        changed = copy.deepcopy(self.row)
        changed['_created_at'] = '2026-11-17T00:01:00Z'
        self.file('db.json', {'app_auditA:online': changed})
        count = len(self.calls())
        self.command('read-project', self.key, '--legacy-connection', self.config_a, ok=False)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertTrue(all(call['sql'].startswith('SELECT ') for call in self.calls()[count:]))

    def test_legacy_missing_identity_evidence_does_not_bind(self):
        state = self.legacy()
        del state['latest_remote']['createdAt']
        self.state_path.write_text(json.dumps(state))
        before = self.state_path.read_bytes()
        self.command('read-project', self.key, '--legacy-connection', self.config_a, ok=False)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_legacy_key_alone_is_not_original_application_url_evidence(self):
        state = self.legacy()
        state['project_url'] = self.key
        self.state_path.write_text(json.dumps(state))
        before = self.state_path.read_bytes()
        count = len(self.calls())
        self.command('read-project', self.key, '--legacy-connection', self.config_a, ok=False)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.assertEqual(len(self.calls()), count)

    def test_recovered_state_records_selected_connection(self):
        self.file('db.json', {'app_auditA:preview': self.row})
        self.command('read-project', self.key, '--snapshot', self.snapshot_file, config=self.config_preview)
        self.assertEqual(self.state()['connection_binding'], {'app_id': 'app_auditA', 'environment': 'preview'})

    def test_independent_state_root_does_not_change_original_state(self):
        self.create()
        before = self.state_path.read_bytes()
        other = self.root / 'other-states'
        self.create(config=self.config_b, state_root=other)
        self.assertEqual(self.state_path.read_bytes(), before)
        state = json.loads((other / self.key / 'state.json').read_text())
        self.assertEqual(state['connection_binding']['app_id'], 'app_auditB')

    def test_description_flow_does_not_read_connection_or_call_cli(self):
        (self.skill / 'references/rules/application.json').unlink()
        missing = self.root / 'unavailable-connection.json'
        data = self.file('description.json', {'input_document_type': 'evaluation_description',
                    'source_document': {'canonical_id': 'description-A', 'url': 'https://source.example/docx/description-A'},
                    'evaluation_scope': [{'name': '草稿管理', 'user_needs': ['保存草稿'],
                                          'scope_points': ['保存当前草稿'], 'source_refs': ['scope-row']}],
                    'scope_basis': {'section': '评测范围'}})
        key = self.command('init-description', '--input', data, config=missing)['project_key']
        scope = self.command('state', key, '--section', 'scope', config=missing)
        self.assertFalse(scope['confirmation_flags']['range_ready'])
        self.command('confirm', key, '--proposal-id', scope['data']['pending']['proposal_id'],
                     '--authorization', '确认一级能力及用户需求', config=missing)
        scenes = self.file('scenes.json', [{'id': 's1', 'name': '日常记录', 'purpose': '保存工作草稿'}])
        proposal = self.command('propose', key, '--kind', 'scenarios', '--input', scenes, config=missing)
        self.command('confirm', key, '--proposal-id', proposal['proposal_id'], '--authorization', '确认场景', config=missing)
        summary = self.command('state', key, '--summary', config=missing)
        self.assertEqual(summary['connection_binding_status'], 'not_applicable')
        self.assertTrue(summary['confirmation_flags']['user_scenarios_confirmed'])
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
