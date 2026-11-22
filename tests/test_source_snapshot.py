"""Context-bounded source inventory tests; no remote reads."""
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('explicit_source_snapshot', ROOT / 'scripts/source_snapshot.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.doc = {'document_id': 'doc-1', 'revision_id': 'r1'}
        self.root = ET.fromstring(
            '<root><h1 id="h">需求</h1><p id="p">完整业务功能</p>'
            '<p id="d" style="line-through">已删除内容</p></root>'
        )

    def test_compact_inventory_omits_full_block_text(self):
        result = MODULE.inventory(self.doc, self.root, compact=True)
        block = next(item for item in result['content_blocks'] if item['id'] == 'p')
        self.assertNotIn('text', block)
        self.assertNotIn('raw_text', block)
        self.assertEqual(block['preview'], '完整业务功能')
        self.assertTrue(next(item for item in result['content_blocks'] if item['id'] == 'd')['has_deleted'])

    def test_selected_blocks_returns_text_and_only_needed_raw_text(self):
        result = MODULE.selected_blocks(self.doc, self.root, ['p', 'd'])
        plain = next(item for item in result['content_blocks'] if item['id'] == 'p')
        deleted = next(item for item in result['content_blocks'] if item['id'] == 'd')
        self.assertEqual(plain['text'], '完整业务功能')
        self.assertNotIn('raw_text', plain)
        self.assertEqual(deleted['raw_text'], '已删除内容')

    def test_heading_selection_is_explicit_and_bounded(self):
        result = MODULE.selected_blocks(self.doc, self.root, heading='需求')
        self.assertEqual([item['id'] for item in result['content_blocks']], ['h', 'p', 'd'])


if __name__ == '__main__':
    unittest.main()
