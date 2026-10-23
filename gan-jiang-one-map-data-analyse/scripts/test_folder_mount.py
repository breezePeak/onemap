import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib
from extract_archives import seven_zip
from folder_mount import scan_folders, PreparationError, validate_methods, project_rows, folder_entry
from source_config import STAGES
BASE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE/'app'))
import server


def zip_bytes(members):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        for name,value in members.items(): archive.writestr(name,value)
    return stream.getvalue()


def stored_rar(path):
    # 单成员无压缩 RAR4，由实际 7-Zip 读取，避免伪扩展名测试。
    def header(value): return struct.pack('<H',zlib.crc32(value)&0xffff)+value
    data=b'rar-test-content'; name=b'item.pdf'
    main=header(struct.pack('<BHHHI',0x73,0,13,0,0))
    item=header(struct.pack('<BHHIIBIIBBHI',0x74,0x8000,32+len(name),len(data),len(data),2,zlib.crc32(data),0,20,0x30,len(name),0x20)+name)
    path.write_bytes(b'Rar!\x1a\x07\x00'+main+item+data+header(struct.pack('<BHH',0x7b,0x4000,7)))


class FolderMountTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir=BASE/'work'); self.addCleanup(self.temporary.cleanup)
        self.base=Path(self.temporary.name); self.root=self.base/'source'; self.database=self.base/'index/解压记录.sqlite'
        for _,name in STAGES: (self.root/name).mkdir(parents=True)
        self.events=[]
        source_patch=patch.object(server,'SOURCE',self.root); source_patch.start(); self.addCleanup(source_patch.stop)

    def put(self,stage,path,value):
        target=self.root/STAGES[stage-1][1]/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(value); return target

    def scan(self,cache=None): return scan_folders(self.root,self.database,cache or {},self.events.append)

    def api(self,path,payload=None):
        handler=server.Handler.__new__(server.Handler); handler.path=path; handler.headers={}
        handler.body=lambda *_:json.dumps(payload).encode(); responses=[]; handler.json=lambda value,status=200:responses.append((value,status))
        if payload is None: handler.do_GET()
        else: handler.do_POST()
        return responses[-1]

    def test_content_business_aggregation_ambiguity_adjacency_and_cache(self):
        for stage,folder in ((1,'A'),(2,'B'),(2,'C'),(3,'D'),(4,'E')):
            for n in range(3): self.put(stage,f'{folder}/附件/扫描件/{stage}-{n}.pdf',f'content-{n}'.encode())
        self.put(1,'A/same.pdf',b'left'); self.put(2,'B/same.pdf',b'right')
        self.put(1,'A/~$lock.xlsx',b'content-0'); self.put(1,'A/empty.pdf',b'')
        self.put(1,'A/test.prj',b'coordinate'); self.put(2,'B/test.cpg',b'coordinate')
        self.put(1,'loose.pdf',b'content-0'); self.put(2,'loose.pdf',b'content-0')
        self.put(1,'orphan/a.pdf',b'orphan'); self.put(2,'unmatched/a.pdf',b'unmatched')
        ignored=self.root/'5.其他/X.pdf'; ignored.parent.mkdir(); ignored.write_bytes(b'content-0')
        result,cache=self.scan()
        self.assertEqual(len(result['links']),5)
        self.assertTrue(all(e['evidence_count']==3 for e in result['links']))
        self.assertTrue(all((e['left']['stage'],e['right']['stage']) in [('S1','S2'),('S2','S3'),('S3','S4')] for e in result['links']))
        self.assertTrue(all(e['left']['name'] not in ('附件','扫描件','未分组材料') for e in result['links']))
        self.assertEqual([e['ambiguous'] for e in result['links'] if e['left']['stage']=='S1'],[True,True])
        with patch('folder_mount.digest',side_effect=AssertionError('缓存未复用')): self.assertEqual(self.scan(cache)[0],result)
        self.assertTrue(all(e['validation']=='verified' and e['physical_path'] for e in result['materials']))
        self.assertEqual(len({e['id'] for e in result['materials']}),len(result['materials']))

    def test_nested_in_place_legacy_reuse_and_incremental_changes(self):
        inner=zip_bytes({'nested.pdf':b'nested'})
        archive=self.put(1,'A/material.zip',zip_bytes({'keep.pdf':b'keep','nested.zip':inner,'change.pdf':b'one'}))
        original=self.put(1,'A/material/keep.pdf',b'keep')
        self.put(2,'B/keep.pdf',b'keep'); self.put(2,'B/change.pdf',b'one'); self.put(2,'B/nested.pdf',b'nested')
        before=hashlib.sha256(archive.read_bytes()).hexdigest(); original_hash=hashlib.sha256(original.read_bytes()).hexdigest()
        result,cache=self.scan(); self.assertEqual(result['links'][0]['evidence_count'],3)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),before)
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),original_hash)
        self.assertFalse(archive.with_name(archive.name+'.解压').joinpath('keep.pdf').exists())
        self.assertTrue(archive.with_name(archive.name+'.解压').joinpath('nested.zip.解压/nested.pdf').is_file())
        physical=[e['physical_path'] for e in result['materials']]; self.assertEqual(len(physical),len(set(physical)))
        self.assertTrue(any('material.zip!nested.zip!nested.pdf' in e['source'] for e in result['materials']))
        again,_=self.scan(cache); self.assertEqual(again['stages'][0]['extracted'],0); self.assertEqual(again['links'],result['links'])
        archive.write_bytes(zip_bytes({'keep.pdf':b'keep','nested.zip':inner,'change.pdf':b'two'}))
        changed,_=self.scan(cache); self.assertEqual(changed['stages'][0]['extracted'],1)
        self.assertNotEqual(changed['links'][0]['signature'],result['links'][0]['signature'])
        phases=[e['phase'] for e in self.events]; self.assertLess(phases.index('校验'),phases.index('目录匹配'))

    def test_wrong_original_never_reused_or_overwritten(self):
        original=self.put(1,'A/doc/a.pdf',b'wrong'); archive=self.put(1,'A/doc.zip',zip_bytes({'a.pdf':b'right'}))
        result,_=self.scan(); self.assertEqual(original.read_bytes(),b'wrong')
        self.assertEqual(archive.with_name('doc.zip.解压').joinpath('a.pdf').read_bytes(),b'right')
        self.assertEqual(result['stages'][0]['extracted'],1)

    def test_unowned_destination_is_preserved_across_retries_and_disk_failure(self):
        archive=self.put(1,'A/doc.zip',zip_bytes({'a.pdf':b'right'}))
        original=self.put(1,'A/doc.zip.解压/a.pdf',b'wrong')
        for _ in range(2):
            with self.assertRaises(PreparationError): self.scan()
            self.assertEqual(original.read_bytes(),b'wrong')
        original.unlink()
        with patch('extract_archives.shutil.disk_usage',return_value=type('Disk',(),{'free':0})()):
            with self.assertRaises(PreparationError) as caught: self.scan()
        self.assertTrue(any('磁盘空间不足' in e['error'] for e in caught.exception.result['errors']))
        result,_=self.scan(); self.assertEqual(result['stages'][0]['extracted'],1)

    def test_source_renaming_invalidates_evidence_and_removed_members_disappear(self):
        archive=self.put(1,'A/material.zip',zip_bytes({'old.pdf':b'keep','removed.pdf':b'removed'}))
        self.put(2,'B/keep.pdf',b'keep'); self.put(2,'B/removed.pdf',b'removed')
        result,cache=self.scan()
        archive.write_bytes(zip_bytes({'renamed.pdf':b'keep'}))
        changed,_=self.scan(cache)
        self.assertEqual(changed['links'][0]['evidence_count'],1)
        self.assertNotEqual(changed['links'][0]['signature'],result['links'][0]['signature'])
        self.assertFalse(any('removed.pdf' in e['source'] for e in changed['materials'] if e['stage']=='S1'))

    def test_multiple_archives_keep_distinct_logical_material_identities(self):
        self.put(1,'shared.pdf',b'shared')
        for name in ('first.zip','second.zip'): self.put(1,name,zip_bytes({'shared.pdf':b'shared'}))
        self.put(2,'B/item.pdf',b'shared')
        result,_=self.scan()
        self.assertEqual(len(result['links']),2)
        self.assertEqual(len([e for e in result['materials'] if e['stage']=='S1']),2)
        self.assertEqual(len({e['id'] for e in result['materials']}),3)

    def test_crc_failure_and_nested_rar_7z(self):
        value=bytearray(zip_bytes({'a.pdf':b'unique-crc-payload'}))
        value[value.index(b'unique-crc-payload')]=ord('X')
        bad=self.put(1,'A/crc.zip',value)
        with self.assertRaises(PreparationError) as caught: self.scan()
        self.assertTrue(any('CRC' in e['error'] for e in caught.exception.result['errors'])); bad.unlink()
        rar=self.base/'stored.rar'; stored_rar(rar)
        source=self.base/'test.pdf'; source.write_bytes(b'7z nested content'); seven=self.base/'stored.7z'
        subprocess.run([seven_zip(),'a',str(seven),str(source)],check=True,capture_output=True)
        self.put(1,'A/outer.zip',zip_bytes({'nested.rar':rar.read_bytes(),'nested.7z':seven.read_bytes()}))
        self.put(2,'B/rar.pdf',b'rar-test-content'); self.put(2,'B/7z.pdf',b'7z nested content')
        result,_=self.scan(); self.assertEqual(result['links'][0]['evidence_count'],2)
        self.assertTrue(any('!nested.rar!' in e['source'] for e in result['materials']))
        self.assertTrue(any('!nested.7z!' in e['source'] for e in result['materials']))

    def test_corrupt_traversal_encrypted_missing_tool_and_stage_block(self):
        bad=self.put(1,'A/corrupt.zip',b'not zip')
        with self.assertRaises(PreparationError) as caught: self.scan()
        self.assertTrue(caught.exception.result['errors']); self.assertNotIn('目录匹配',[e['phase'] for e in self.events]); bad.unlink()
        bad=self.put(1,'A/unsafe.zip',zip_bytes({'../outside.pdf':b'unsafe'}))
        with self.assertRaises(PreparationError): self.scan()
        self.assertFalse((bad.parent/'outside.pdf').exists()); bad.unlink()
        tool=seven_zip(); plain=self.put(1,'A/plain.pdf',b'secret'); encrypted=plain.parent/'encrypted.zip'
        subprocess.run([tool,'a','-tzip','-psecret',str(encrypted),str(plain)],capture_output=True,check=True)
        result,_=self.scan()
        self.assertTrue(any('加密' in e['error'] for e in result['errors'])); encrypted.unlink()
        rar=self.put(1,'A/unavailable.rar',b'x')
        with patch('extract_archives.seven_zip',side_effect=RuntimeError('缺少工具')):
            with self.assertRaises(PreparationError) as caught: self.scan()
        self.assertIn('缺少工具',caught.exception.result['errors'][0]['error']); rar.unlink()
        shutil.rmtree(self.root/STAGES[3][1])
        with self.assertRaises(PreparationError): self.scan()

    def test_real_rar_7z_and_virtual_business_directory(self):
        rar=self.root/STAGES[0][1]/'root.rar'; stored_rar(rar)
        source=self.base/'item.pdf'; source.write_bytes(b'rar-test-content')
        archive=self.root/STAGES[1][1]/'root.7z'
        subprocess.run([seven_zip(),'a',str(archive),str(source)],check=True,capture_output=True)
        result,cache=self.scan(); self.assertEqual(len(result['links']),1)
        self.assertEqual(result['links'][0]['left']['kind'],'archive'); self.assertEqual(result['links'][0]['right']['kind'],'archive')
        self.assertEqual((rar.with_name('root.rar.解压')/'item.pdf').read_bytes(),b'rar-test-content')
        again,_=self.scan(cache); self.assertEqual(again['stages'][0]['reused'],1); self.assertEqual(again['stages'][1]['reused'],1)

    def test_api_decisions_details_filters_chains_restart_and_failed_snapshot(self):
        for n in range(23):
            self.put(1,f'A/deep/{n}.pdf',str(n).encode()); self.put(2,f'B/{n}.pdf',str(n).encode()); self.put(3,f'C/{n}.pdf',str(n).encode())
        directory=self.base/'state'; state=dict(status='ready',phase='完成')
        with patch.object(server,'MOUNT_DIR',directory),patch.object(server,'MOUNT_STATE',state):
            result,cache=self.scan(); server.write_json(directory/'result.json',dict(result,scanned_at='test'))
            link=result['links'][0]; payload=dict(id=link['id'],signature=link['signature']); self.assertFalse(server.mount_view()['chains'])
            for status in ('confirmed','rejected','pending','review','confirmed'):
                response,code=self.api('/api/folder-mount/decision',dict(payload,status=status)); self.assertEqual(code,200,response)
                self.assertEqual(server.mount_view()['links'][0]['status'],status)
            self.assertGreaterEqual(len(server.read_json(directory/'decisions.json')[link['id']]['history']),4)
            response,code=self.api('/api/folder-mount'); self.assertEqual(code,200); self.assertNotIn('evidence',response['links'][0])
            evidence,code=self.api('/api/folder-mount/links/'+link['id']+'?page=2'); self.assertEqual(code,200); self.assertEqual(len(evidence['evidence']),3)
            evidence,_=self.api('/api/folder-mount/links/'+link['id']+'?q=deep%2F10.pdf'); self.assertEqual(evidence['count'],1)
            response,_=self.api('/api/folder-mount?status=confirmed'); self.assertEqual(response['count'],1)
            other=result['links'][1]; self.api('/api/folder-mount/decision',dict(id=other['id'],signature=other['signature'],status='confirmed'))
            self.assertEqual(len(server.mount_view()['chains']),1)
            again,_=self.scan(cache); server.write_json(directory/'result.json',dict(again,scanned_at='again')); self.assertEqual(server.mount_view()['summary']['confirmed'],2)
            with patch.object(server,'MOUNT_STATE',dict(status='idle',phase='restart')): self.assertEqual(server.mount_view()['summary']['confirmed'],2)
            self.put(1,'A/deep/0.pdf',b'changed'); changed,_=self.scan(cache); server.write_json(directory/'result.json',dict(changed,scanned_at='changed'))
            self.assertEqual(server.mount_view()['links'][0]['status'],'review'); self.assertFalse(server.mount_view()['chains'])
            _,code=self.api('/api/folder-mount/decision',dict(payload,status='confirmed')); self.assertEqual(code,400)
            self.put(1,'A/broken.zip',b'broken'); old=(directory/'result.json').read_bytes()
            with patch.object(server,'SOURCE',self.root),patch.object(server,'BASE',self.base): server.mount_scan(['M2'])
            self.assertEqual(state['status'],'failed'); self.assertEqual((directory/'result.json').read_bytes(),old)
            self.assertTrue(server.mount_view()['historical']); self.assertTrue(server.mount_view()['errors'])
            _,code=self.api('/api/folder-mount/decision',dict(id=other['id'],signature=other['signature'],status='confirmed')); self.assertEqual(code,400)

    def test_methods_and_duplicate_submissions_rejected(self):
        for methods in ([],['M1'],['M2','M3'],None,'M2'):
            with self.assertRaises(ValueError): validate_methods(methods)
            _,code=self.api('/api/folder-mount/scan',dict(methods=methods)); self.assertEqual(code,400)
        with patch.object(server,'MOUNT_DIR',self.base/'state'),patch.object(server,'MOUNT_STATE',dict(status='running',phase='test')):
            _,code=self.api('/api/folder-mount/scan',dict(methods=['M2'])); self.assertEqual(code,400)

    def test_missing_root(self):
        with self.assertRaises(ValueError): scan_folders(self.base/'missing',self.database,{},lambda _:None)

    def test_four_stage_project_rows_skip_empty_stage_without_confirmation(self):
        self.put(1,'项目甲/附件/a.pdf',b'shared')
        self.put(3,'项目乙/a.pdf',b'shared'); self.put(4,'项目丙/a.pdf',b'shared')
        result,_=self.scan(); self.assertEqual(len(result['links']),2)
        gap=next(e for e in result['links'] if e['left']['stage']=='S1')
        self.assertEqual(gap['right']['stage'],'S3'); self.assertEqual(gap['skipped_stages'],['S2'])
        rows=project_rows(result); self.assertEqual(len(rows),1)
        self.assertEqual([len(rows[0]['stages'][sid]) for sid in ('S1','S2','S3','S4')],[1,0,1,1])
        self.assertEqual(rows[0]['status'],'pending'); self.assertTrue(rows[0]['stages']['S3'][0]['candidate'])
        for link in result['links']: link['status']='confirmed'
        row=project_rows(result)[0]; self.assertEqual(row['status'],'confirmed')
        self.assertFalse(any(e['candidate'] for values in row['stages'].values() for e in values))

    def test_project_nodes_skip_classification_year_and_attachment_directories(self):
        path=self.put(4,'出让/04出让档案整理/2023年/2023-9 中宝/06 测量/附件/a.pdf',b'x')
        entry=folder_entry(path,str(path),self.root)
        self.assertEqual(entry['name'],'2023-9 中宝'); self.assertTrue(entry['folder'].endswith('2023-9 中宝'))
        source=str(self.root/STAGES[2][1]/'2019——2023年报批附件材料.zip')+'!2021/赣江新区2021年报批材料/幸福河项目.zip!幸福河项目/附件/a.pdf'
        physical=self.put(3,'2019——2023年报批附件材料.zip.解压/2021/幸福河项目.zip.解压/幸福河项目/附件/a.pdf',b'x')
        entry=folder_entry(physical,source,self.root); self.assertEqual(entry['name'],'幸福河项目')
        self.assertTrue(Path(entry['folder']).is_relative_to(self.root)); self.assertNotIn('work',Path(entry['folder']).relative_to(self.root).parts)

    def test_shared_project_candidates_are_kept_separate_in_stage_cells(self):
        self.put(1,'项目甲/a.pdf',b'shared')
        self.put(3,'项目乙/a.pdf',b'shared'); self.put(3,'项目丙/a.pdf',b'shared')
        result,_=self.scan(); row=project_rows(result)[0]
        self.assertTrue(row['ambiguous']); self.assertEqual(len(row['stages']['S3']),2)
        self.assertTrue(all(e['status']=='pending' for e in result['links']))

    def test_project_api_never_displays_work_or_extraction_copies_outside_source(self):
        self.put(1,'真实项目/a.pdf',b'one')
        result,_=self.scan()
        outside=dict(result['folders'][0],id='outside',name='样例项目',folder=str(self.base/'work/样例项目'))
        result['folders'].append(outside)
        directory=self.base/'state'
        with patch.object(server,'MOUNT_DIR',directory),patch.object(server,'MOUNT_STATE',dict(status='ready',phase='完成')):
            server.write_json(directory/'result.json',dict(result,scanned_at='test'))
            response,code=self.api('/api/folder-mount?view=projects')
            self.assertEqual(code,200); self.assertEqual(len(response['rows']),1)
            self.assertEqual(response['rows'][0]['name'],'真实项目')
            self.assertTrue(all(Path(f['folder']).is_relative_to(self.root) for row in response['rows'] for values in row['stages'].values() for f in values))

    def test_archive_root_directory_marker_is_ignored_but_absolute_files_block(self):
        archive=self.put(1,'A/marker.zip',zip_bytes({'/':b'','ok.pdf':b'content'}))
        self.put(2,'B/ok.pdf',b'content')
        result,_=self.scan(); self.assertEqual(len(result['links']),1)
        archive.write_bytes(zip_bytes({'/outside.pdf':b'blocked'}))
        with self.assertRaises(PreparationError): self.scan()

    def test_project_inventory_pagination_and_unscanned_status(self):
        for n in range(51): self.put(4,f'项目{n:02}/a.pdf',str(n).encode())
        result,_=self.scan(); directory=self.base/'state'
        with patch.object(server,'MOUNT_DIR',directory),patch.object(server,'MOUNT_STATE',dict(status='failed',phase='test',errors=[])):
            server.write_json(directory/'inventory.json',{key:result[key] for key in ('folders','stages','errors')})
            response,code=self.api('/api/folder-mount?view=projects&page=2&pair=S4')
            self.assertEqual(code,200); self.assertEqual(response['page'],2); self.assertEqual(len(response['rows']),1)
            self.assertEqual(response['rows'][0]['status'],'unscanned')

    def test_open_path_and_file_preview_endpoints(self):
        doc=self.put(1,'项目甲/说明.txt','中文坐标内容'.encode('gb18030'))
        archive=self.put(1,'项目甲/包.zip',zip_bytes({'sub/a.pdf':b'%PDF-1.4'}))
        self.scan()
        launched=[]
        with patch.object(server,'launch_explorer',side_effect=lambda target,action:launched.append((str(target),action))):
            res,code=self.api('/api/folder-mount/open-path',dict(path=str(doc.parent),action='folder'))
            self.assertEqual(code,200); self.assertTrue(res['is_dir'])
            res,code=self.api('/api/folder-mount/open-path',dict(path=str(archive)+'!sub/a.pdf',action='select'))
            self.assertEqual(code,200); self.assertFalse(res['is_dir']); self.assertTrue(res['opened'].endswith('a.pdf'))
            _,code=self.api('/api/folder-mount/open-path',dict(path='C:/Windows/System32',action='folder'))
            self.assertEqual(code,400)
        self.assertEqual(len(launched),2)


if __name__=='__main__': unittest.main()
