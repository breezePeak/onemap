import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from contextlib import closing
from directory_mount_store import save_directory_mounts, load_directory_mounts, review_directory_mount


class DirectoryMountStoreTests(unittest.TestCase):
    def test_database_review_rejects_stale_or_inactive_links(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / 'work') as folder:
            root = Path(folder) / 'source'
            self.assertEqual(load_directory_mounts(folder, root)['links'], [])
            link = dict(id='a-b', left=dict(id='a', stage='S1', folder='A'),
                        right=dict(id='b', stage='S2', folder='B'), signature='v1')
            scan = dict(folders=[link['left'], link['right']], rule_version='r1', methods=['M2'])
            save_directory_mounts(folder, root, [link], scan=scan)
            review_directory_mount(folder, root, 'a-b', 'v1', 'confirmed', 'r1')
            before = load_directory_mounts(folder, root)
            for ident, signature, status, rule in [('a-b','old','confirmed','r1'),
                                                  ('a-b','v1','invalid','r1'),
                                                  ('a-b','v1','confirmed','r2')]:
                with self.assertRaises(ValueError):
                    review_directory_mount(folder, root, ident, signature, status, rule)
            self.assertEqual(load_directory_mounts(folder, root), before)
            with self.assertRaises(ValueError):
                load_directory_mounts(folder, root / 'other')
            save_directory_mounts(folder, root, [], scan=scan)
            with self.assertRaises(ValueError):
                review_directory_mount(folder, root, 'a-b', 'v1', 'confirmed', 'r1')
            with closing(sqlite3.connect(Path(folder) / 'project_catalog.sqlite')) as db:
                self.assertEqual(db.execute('SELECT status,active FROM directory_mounts').fetchone(), ('confirmed',0))

    def test_save_review_rescan_and_rollback(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / 'work') as folder:
            root = Path(folder) / 'source'
            link = dict(id='a-b', left=dict(id='a', stage='S1', folder='A'),
                        right=dict(id='b', stage='S2', folder='B'), signature='v1',
                        methods=['M1', 'M2'], evidence=[dict(code='123')])
            save_directory_mounts(folder, root, [link])
            db = sqlite3.connect(Path(folder) / 'project_catalog.sqlite')
            self.addCleanup(db.close)
            self.assertEqual(db.execute('SELECT status,evidence_count,active FROM directory_mounts').fetchone(),
                             ('pending', 1, 1))
            link.update(status='confirmed', confirmation=dict(status='confirmed', history=[dict(status='review')]))
            save_directory_mounts(folder, root, [link])
            saved = db.execute('SELECT status,confirmation,payload FROM directory_mounts').fetchone()
            self.assertEqual(saved[0], 'confirmed')
            self.assertEqual(json.loads(saved[1]), link['confirmation'])
            self.assertEqual(json.loads(saved[2]), link)
            with self.assertRaises(KeyError):
                save_directory_mounts(folder, root, [dict(id='broken')])
            with self.assertRaises(ValueError):
                save_directory_mounts(folder, root / 'other', [])
            self.assertEqual(db.execute('SELECT status,active FROM directory_mounts').fetchone(), ('confirmed', 1))
            save_directory_mounts(folder, root, [])
            self.assertEqual(db.execute('SELECT active FROM directory_mounts').fetchone()[0], 0)
            save_directory_mounts(folder, root, [link])
            self.assertEqual(db.execute('SELECT count(*),active FROM directory_mounts').fetchone(), (1, 1))
            db.close()


if __name__ == '__main__':
    unittest.main()
