import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from storage_info import storage_info


class StorageInfoTests(unittest.TestCase):
    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'work') as folder:
            base=Path(folder)
            result=storage_info(base,base/'source')
            self.assertFalse(result['database_exists'])
            self.assertEqual(result['entries'],[])
            self.assertFalse((base/'解压材料').exists())

    def test_records_search_pagination_and_disk_status(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'work') as folder:
            base=Path(folder);source=base/'source';source.mkdir()
            original=source/'原件.pdf';original.write_bytes(b'pdf')
            (base/'解压材料').mkdir();database=base/'解压材料/解压记录.sqlite'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE files (archive TEXT,raw TEXT,dest TEXT,size INTEGER,status TEXT,error TEXT)')
                db.executemany('INSERT INTO files VALUES (?,?,?,?,?,?)',[(str(source/'a.zip'),f'{i:03}.pdf',str(original),3,'done',None) for i in range(51)])
                db.execute('INSERT INTO files VALUES (?,?,?,?,?,?)',('bad.zip',"单引号\'_%",str(base/'missing'),0,'failed','CRC错误'))
            db.close()
            before=database.read_bytes()
            result=storage_info(base,source)
            self.assertEqual(result['counts'],{'done':51,'failed':1})
            self.assertEqual(len(result['entries']),50)
            self.assertEqual(len(storage_info(base,source,page=2)['entries']),2)
            entry=storage_info(base,source,query="单引号'_%")['entries'][0]
            self.assertFalse(entry['exists']);self.assertEqual(entry['error'],'CRC错误')
            entry=storage_info(base,source,query='000.pdf')['entries'][0]
            self.assertTrue(entry['exists']);self.assertEqual(entry['location'],'复用原目录文件')
            (base/'work').mkdir();(base/'work/files.json').write_text(json.dumps([dict(path=str(original),size=3)]),encoding='utf-8')
            result=storage_info(base,source,'inventory','原件')
            self.assertEqual(result['total'],1);self.assertEqual(result['entries'][0]['status'],'snapshot')
            original.unlink()
            self.assertFalse(storage_info(base,source,'inventory')['entries'][0]['exists'])
            self.assertEqual(database.read_bytes(),before)
            with self.assertRaises(ValueError):storage_info(base,source,page=0)


if __name__=='__main__':unittest.main()
