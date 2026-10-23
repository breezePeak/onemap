from pathlib import Path
import unittest
import tempfile
import sqlite3
from unittest.mock import patch,mock_open
from inspect_sources import is_office_lock,extracted_files
from review_service import BASE,write_json
from cross_stage_materials import discover_files
from link_stages import required_parser


class SourceFilesTests(unittest.TestCase):
    def test_nested_extraction_index_retains_original_source_and_stage(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            job=Path(folder);root=job/'sources';stage=root/'6.用地规划';stage.mkdir(parents=True)
            nested=job/'nested.zip';nested.write_bytes(b'zip');page=job/'page.pdf';page.write_bytes(b'%PDF')
            database=job/'index.sqlite'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE files (archive TEXT,raw TEXT,dest TEXT,dsize INTEGER,dmtime INTEGER,status TEXT)')
                for archive,raw,path in ((stage/'a.zip','nested.zip',nested),(nested,'page.pdf',page)):
                    stat=path.stat();db.execute('INSERT INTO files VALUES (?,?,?,?,?,?)',(str(archive),raw,str(path),stat.st_size,stat.st_mtime_ns,'done'))
            db.close()
            entries=extracted_files(database,root);entry=next(e for e in entries if e['path']==str(page))
            self.assertTrue(entry['source'].endswith('a.zip!nested.zip!page.pdf'));self.assertEqual(entry['storage_stage'],'6.用地规划')
            work=job/'work';write_json(work/'files.json',entries)
            files,_=discover_files(root,work,job/'out',[],[])
            self.assertEqual(files[0]['source'],entry['source'])
            page.write_bytes(b'changed')
            self.assertNotIn(str(page),[e['path'] for e in extracted_files(database,root)])

    def test_office_lock_is_skipped_without_opening(self):
        for name in ('~$project.xlsx','~$材料.doc','folder/~$old.xls'):
            with self.subTest(name=name),patch.object(Path,'open',side_effect=PermissionError('locked')):
                self.assertTrue(is_office_lock(name))
                self.assertIsNone(required_parser(Path(name)))

    def test_real_workbooks_are_still_checked(self):
        self.assertFalse(is_office_lock('project.xlsx'))
        with patch.object(Path,'open',mock_open(read_data=b'PK123456')):
            self.assertIsNone(required_parser(Path('project.xlsx')))
        with patch.object(Path,'open',mock_open(read_data=b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1')):
            self.assertIn('OLE/XLS',required_parser(Path('project.xlsx')))


if __name__=='__main__':unittest.main()
