import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import organize_copy
from project_catalog import ProjectCatalog
from source_config import STAGES


class OrganizeCopyTests(unittest.TestCase):
    def test_plan_and_copy_preserve_project_files_and_unassigned(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / 'work') as temp:
            base = Path(temp)
            source, dest = base / 'source', base / 'dest'
            dest.mkdir()
            for _, stage in STAGES:
                (source / stage).mkdir(parents=True)
            project = source / STAGES[0][1] / '项目甲'
            (project / '示意图').mkdir(parents=True)
            (project / '示意图/a.pdf').write_bytes(b'project')
            (source / STAGES[0][1] / '台账.xlsx').write_bytes(b'unassigned')
            storage = base / 'work/folder_mount'
            catalog = ProjectCatalog(storage_dir=storage, root=source)
            catalog.register_or_find('S1', '项目甲/示意图/a.pdf', False)
            catalog.close()
            with patch.multiple(organize_copy, BASE=base, SOURCE=source, DEST=dest,
                                PLAN=base / 'work/plan.jsonl', CATALOG=base / 'work/catalog'):
                organize_copy.plan()
                organize_copy.copy()
                with self.assertRaises(ValueError):
                    organize_copy.copy()
            index = json.loads((dest / '整理项目索引.json').read_text(encoding='utf-8'))
            folder = next(iter(index['projects'].values()))['name']
            self.assertEqual((dest / STAGES[0][1] / folder / '示意图/a.pdf').read_bytes(), b'project')
            self.assertEqual((dest / STAGES[0][1] / '_待归属资料/台账.xlsx').read_bytes(), b'unassigned')
            self.assertEqual((project / '示意图/a.pdf').read_bytes(), b'project')


if __name__ == '__main__':
    unittest.main()
