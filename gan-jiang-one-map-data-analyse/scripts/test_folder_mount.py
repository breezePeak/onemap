import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import sys
from unittest.mock import patch

from folder_mount import scan_folders

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE/'app'))
import server


class FolderMountTests(unittest.TestCase):
    def test_cross_stage_hashes_not_names_and_cache_refresh(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as temporary:
            root = Path(temporary)
            for stage, folder, name, content in [
                ('1.选址', '项目A', '意见书.pdf', b'one'),
                ('2.供应', '项目B', '改名.pdf', b'one'),
                ('1.选址', '项目C', '相同.pdf', b'one'),
                ('3.首登', '项目A', '意见书.pdf', b'two'),
                ('4.规划', '项目D', '空.pdf', b''),
                ('5.工程', '项目E', '~$锁.xlsx', b'one'),
            ]:
                path = root/stage/folder/name
                path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(content)
            result, cache = scan_folders(root, root/'absent.sqlite', {}, lambda *_: None)
            self.assertEqual(len(result['links']), 2)
            self.assertEqual(len(result['folders']), 5)
            self.assertFalse(any(link['left']['stage'] == link['right']['stage'] for link in result['links']))
            with patch('folder_mount.digest', side_effect=AssertionError('should reuse cache')):
                again, _ = scan_folders(root, root/'absent.sqlite', cache, lambda *_: None)
            self.assertEqual(result, again)
            (root/'2.供应/项目B/改名.pdf').write_bytes(b'new bytes')
            changed, _ = scan_folders(root, root/'absent.sqlite', cache, lambda *_: None)
            self.assertEqual(changed['links'], [])

    def test_extracted_archive_keeps_logical_stage_and_folder(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as temporary:
            base=Path(temporary);root=base/'source';folder=root/'6.规划/code'
            folder.mkdir(parents=True);(folder/'x.pdf').write_bytes(b'same')
            extracted=base/'extracted.pdf';extracted.write_bytes(b'same')
            database=base/'index.sqlite';stat=extracted.stat()
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE files (archive TEXT,raw TEXT,dest TEXT,dsize INTEGER,dmtime INTEGER,status TEXT)')
                db.execute('INSERT INTO files VALUES (?,?,?,?,?,?)',(str(root/'4.供应/材料.zip'),'项目甲/件.pdf',str(extracted),stat.st_size,stat.st_mtime_ns,'done'))
            db.close()
            result,_=scan_folders(root,database,{},lambda *_: None)
            self.assertEqual(len(result['links']),1)
            self.assertTrue(any(folder['folder'].endswith('材料.zip!项目甲') and folder['stage']=='4.供应' for folder in result['folders']))
            extracted.write_bytes(b'changed')
            result,_=scan_folders(root,database,{},lambda *_: None)
            self.assertEqual(result['links'],[])

    def test_missing_root_is_error(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as temporary:
            with self.assertRaises(ValueError):
                scan_folders(Path(temporary)/'missing',Path(temporary)/'index',{},lambda *_:None)

    def test_shared_coordinate_metadata_is_not_project_evidence(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as temporary:
            root=Path(temporary)
            for stage in ('1.选址','4.供应'):
                folder=root/stage/'项目';folder.mkdir(parents=True)
                (folder/'范围.prj').write_bytes(b'same coordinate system')
                (folder/'范围.cpg').write_bytes(b'UTF-8')
            result,_=scan_folders(root,root/'absent.sqlite',{},lambda *_:None)
            self.assertEqual(len(result['folders']),2)
            self.assertEqual(result['links'],[])

    def test_decisions_persist_revoke_and_invalidate_changed_evidence(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as temporary:
            directory=Path(temporary)
            result=dict(folders=[],links=[dict(id='pair',signature='original')],errors=[])
            server.write_json(directory/'result.json',result)
            handler=server.Handler.__new__(server.Handler)
            handler.path='/api/folder-mount/decision';handler.headers={}
            responses=[];handler.json=lambda value,status=200:responses.append((value,status))
            with patch.object(server,'MOUNT_DIR',directory),patch.object(server,'MOUNT_STATE',dict(status='ready',phase='test')):
                for status in ('confirmed','rejected','pending'):
                    handler.body=lambda *_:json.dumps(dict(id='pair',signature='original',status=status)).encode()
                    handler.do_POST()
                    self.assertEqual(responses[-1][1],200,responses[-1])
                    self.assertEqual(server.mount_view()['links'][0]['status'],status)
                handler.body=lambda *_:b'{"id":"pair","signature":"original","status":"confirmed"}'
                handler.do_POST()
                self.assertEqual(server.read_json(directory/'decisions.json')['pair']['status'],'confirmed')
                result['links'][0]['signature']='new';server.write_json(directory/'result.json',result)
                self.assertEqual(server.mount_view()['links'][0]['status'],'pending')
                handler.do_POST();self.assertEqual(responses[-1][1],400)
                server.MOUNT_STATE['status']='running'
                handler.do_POST();self.assertEqual(responses[-1][1],400)


if __name__=='__main__':unittest.main()
