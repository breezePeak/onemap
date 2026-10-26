import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from folder_mount import scan_folders, validate_methods, project_rows, PreparationError
from project_catalog import ProjectCatalog
from project_codes import extract_codes
from source_config import STAGES
from extract_archives import seven_zip
from test_folder_mount import zip_bytes

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'app'))
import server
import run_stage_mount

CODE = '2017-360199-27-03-014458'
OTHER = '2017-360199-27-03-014459'


class ProjectCodeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=BASE / 'work')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'source'
        self.storage = self.base / 'mount'
        self.events = []
        for _, name in STAGES: (self.root / name).mkdir(parents=True)

    def put(self, stage, relative, text):
        path = self.root / STAGES[stage - 1][1] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8') if isinstance(text, str) else text)
        return path

    def scan(self, methods=None, cache=None):
        return scan_folders(self.root, self.base / 'index/解压记录.sqlite', cache or {}, self.events.append,
                            methods=methods or ['M1'], storage_dir=self.storage)

    def view(self, result, decisions=None, params=None):
        server.write_json(self.storage / 'result.json', dict(result, scanned_at='test'))
        if decisions is not None: server.write_json(self.storage / 'decisions.json', decisions)
        with patch.object(server, 'MOUNT_DIR', self.storage), patch.object(server, 'SOURCE', self.root), patch.object(server, 'MOUNT_STATE', dict(status='ready', phase='完成')):
            return server.mount_view(params), server.mount_result()

    def test_normalization_preserves_raw_and_rejects_partial_other_identifiers_and_ocr(self):
        raw = '２０１７ – ３６０１９９ — ２７ − ０３ － ０１４４５８'
        hits = extract_codes('项目代码：' + raw, labelled=True)
        self.assertEqual(hits[0]['raw_code'], raw)
        self.assertEqual(hits[0]['normalized_code'], CODE)
        for value in ('2017', '360199-27-03-014458', CODE + '9', 'A' + CODE, CODE.replace('014458', 'O14458')):
            self.assertEqual(extract_codes('项目代码：' + value, labelled=True), [], value)
        self.assertEqual(extract_codes('电子监管号：' + CODE, labelled=True), [])
        self.assertEqual(extract_codes('合同编号：' + CODE), [])
        self.assertFalse(extract_codes('0000-000000-27-03-014458')[0]['valid'])

    def test_all_methods_validated(self):
        for methods in (['M1'], ['M2'], ['M1', 'M2'], ['M2', 'M1']): validate_methods(methods)
        for methods in (None, [], ['M1', 'M1'], ['M3'], 'M1'):
            with self.assertRaises(ValueError): validate_methods(methods)

    def test_s1_s7_names_and_subdirectory_sources_without_hashing(self):
        variant = '２０１７–３６０１９９–２７–０３–０１４４５８'
        self.put(1, f'项目甲/{CODE}/a.txt', '无代码')
        self.put(7, f'{variant}/b.txt', '其他材料')
        with patch('folder_mount.digest', side_effect=AssertionError('M1 不应算哈希')):
            result, _ = self.scan()
        self.assertEqual(result['hashed_count'], 0)
        self.assertEqual(len(result['links']), 1)
        link = result['links'][0]
        self.assertEqual((link['left']['stage'], link['right']['stage']), ('S1', 'S7'))
        evidence = link['evidence'][0]
        self.assertEqual(evidence['normalized_code'], CODE)
        self.assertEqual(evidence['left_sources'][0]['source_type'], 'subdirectory')
        self.assertEqual(evidence['right_sources'][0]['raw_code'], variant)
        self.assertEqual(link['status'], 'pending')
        self.assertEqual(result['m1_statistics']['projects_with_codes'], 2)

    def test_body_structured_filename_and_text_cache_invalidation(self):
        first = self.put(1, '甲/a.txt', '项目代码：' + CODE)
        self.put(2, '乙/data.json', json.dumps({'record': {'project_code': CODE}}))
        self.put(7, f'丙/{CODE}.txt', '无代码')
        result, cache = self.scan()
        self.assertEqual(len(result['links']), 3)
        self.assertEqual({e['source_type'] for e in result['project_code_index']}, {'filename', 'body_field', 'structured_field'})
        self.assertTrue(all(e['location'] and e['source_path'] for e in result['project_code_index']))
        with patch('project_codes.read_code_material', side_effect=AssertionError('应复用缓存')):
            again, _ = self.scan(cache=cache)
        self.assertEqual(again['links'], result['links'])
        first.write_text('项目代码：' + OTHER, encoding='utf-8')
        changed, _ = self.scan(cache=cache)
        self.assertEqual(len(changed['links']), 1)
        self.assertEqual(changed['links'][0]['left']['stage'], 'S2')
        first.unlink()
        self.put(1, '甲/new.txt', '项目代码：' + CODE)
        restored, _ = self.scan(cache=cache)
        self.assertEqual(len(restored['links']), 3)
        self.assertFalse(any(e['source_path'].endswith('a.txt') for e in restored['project_code_index']))
        cat = ProjectCatalog(self.storage, self.root)
        try:
            self.assertEqual(cat.db.execute('SELECT count(*) FROM project_codes').fetchone()[0], 3)
            self.assertFalse(any(row[0].endswith('a.txt') for row in cat.db.execute('SELECT source FROM project_code_cache')))
        finally: cat.close()

    def test_native_pdf_docx_xlsx_and_existing_native_cache(self):
        from pypdf import PdfWriter
        from openpyxl import Workbook
        path = self.put(1, '甲/a.pdf', b'')
        writer = PdfWriter(); writer.add_blank_page(100, 100)
        with path.open('wb') as stream: writer.write(stream)
        self.put(2, '乙/a.docx', zip_bytes({'word/document.xml': f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>项目代码：{CODE}</w:t></w:r></w:p></w:body></w:document>'}))
        workbook = Workbook(); sheet = workbook.active; sheet.append(['项目代码', CODE])
        excel = self.put(7, '丙/a.xlsx', b''); workbook.save(excel); workbook.close()
        with patch('extract_pdfs.read_pdf_text', return_value=['项目代码：' + CODE]) as reader:
            result, _ = self.scan()
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(len(result['links']), 3)
        self.assertTrue(any(e['location']=='page=1' for e in result['project_code_index']))
        from project_codes import read_code_material
        stat = path.stat()
        with patch('extract_pdfs.read_pdf_text', side_effect=AssertionError('已有原生文字应复用')):
            texts, _ = read_code_material(path, {str(path): dict(size=stat.st_size, mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns, pages=['项目代码：' + CODE])})
        self.assertEqual(texts[0][0], 'page=1')
        with patch('extract_pdfs.read_pdf_text', return_value=['项目代码：'+OTHER]) as reader:
            texts, _ = read_code_material(path, {str(path): dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,ctime_ns=0,pages=['旧解析'])})
        self.assertEqual(reader.call_count, 1)
        self.assertIn(OTHER,texts[0][1])

    def test_docx_table_and_xlsx_code_column_preserve_real_locations(self):
        xml=f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目代码</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>{CODE}</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
        self.put(1,'甲/table.docx',zip_bytes({'word/document.xml':xml}))
        from openpyxl import Workbook
        book=Workbook(); sheet=book.active
        sheet.append(['项目代码','项目名称']); sheet.append([CODE,'甲'])
        path=self.put(7,'乙/table.xlsx',b''); book.save(path); book.close()
        result,_=self.scan()
        self.assertEqual(len(result['links']),1)
        self.assertEqual({row['location'] for row in result['project_code_index']},{'表1行1列2','Sheet!A2'})
        self.assertTrue(all(row['text_evidence']==CODE for row in result['project_code_index']))

    def test_conflicting_codes_and_one_to_many_candidates(self):
        self.put(1, '甲/a.txt', f'项目代码：{CODE}\n项目代码：{OTHER}')
        self.put(7, f'宗地一/{CODE}.txt', '一')
        self.put(7, f'宗地二/{CODE}.txt', '二')
        result, _ = self.scan()
        self.assertEqual(len(result['links']), 2)
        self.assertTrue(all(e['ambiguous'] and e['conflict'] and e['status']=='review' for e in result['links']))
        self.assertEqual(result['m1_statistics']['conflicts'], 1)
        self.assertTrue(all(e['conflict'] for e in result['project_code_index'] if e['stage']=='S1'))
        self.assertEqual(len(project_rows(result)[0]['stages']['S7']), 2)

    def test_one_to_many_without_conflict_remains_separate_pending_candidates(self):
        for stage, name in ((1,'甲'),(7,'宗地一'),(7,'宗地二')):
            self.put(stage,f'{name}/{CODE}.txt','a')
        result, _ = self.scan()
        self.assertEqual(len(result['links']),2)
        self.assertTrue(all(e['ambiguous'] and not e['conflict'] and e['status']=='pending' for e in result['links']))
        self.assertEqual(result['m1_statistics']['conflicts'],0)
        self.assertEqual(len({e['right']['id'] for e in result['links']}),2)

    def test_name_similarity_and_partial_codes_do_not_link(self):
        self.put(1, '同名工程/a.txt', '项目代码：' + CODE)
        self.put(7, '同名工程/a.txt', '项目代码：' + OTHER)
        self.put(2, '同名工程/a.txt', '项目代码：2017-360199')
        result, _ = self.scan()
        self.assertEqual(result['links'], [])
        self.assertEqual(result['m1_statistics']['unrecognized_fields'], 1)
        self.assertTrue(any(row['status']=='review' for row in project_rows(result)))

    def test_union_deduplicates_and_preserves_m2_decision_and_evidence(self):
        self.put(1, f'{CODE}/a.txt', '相同附件')
        self.put(7, f'{CODE}/a.txt', '相同附件')
        m2, cache = self.scan(['M2'])
        old = m2['links'][0]
        decisions = {old['id']: dict(status='confirmed',signature=old['signature'],rule_version=m2['rule_version'],history=[{'status':'pending'}])}
        with patch('folder_mount.digest', side_effect=AssertionError('不应重新算哈希')):
            combined, _ = self.scan(['M1', 'M2'], cache)
        self.assertEqual(len(combined['links']), 1)
        link = combined['links'][0]
        self.assertEqual(link['methods'], ['M1', 'M2'])
        self.assertEqual(link['method_signatures']['M2'], old['signature'])
        self.assertEqual(link['method_evidence']['M2'], old['evidence'])
        self.assertEqual(len(link['method_evidence']['M1']), 1)
        view, loaded = self.view(combined, decisions, {'view':['projects'],'method':['M1'],'pair':['S1-S7']})
        reviewed = loaded['links'][0]
        self.assertEqual(reviewed['status'], 'review')
        self.assertEqual(reviewed['method_statuses']['M2'], 'confirmed')
        self.assertEqual(reviewed['confirmation'], decisions[old['id']])
        self.assertEqual(view['count'], 1)
        self.assertNotIn('method_evidence', view['rows'][0]['links'][0])
        for method in ('M1','M2'):
            handler=server.Handler.__new__(server.Handler)
            handler.path=f"/api/folder-mount/links/{link['id']}?method={method}"
            responses=[]
            handler.json=lambda value,status=200:responses.append((value,status))
            with patch.object(server,'MOUNT_DIR',self.storage),patch.object(server,'SOURCE',self.root): handler.do_GET()
            response,status=responses[-1]
            self.assertEqual(status,200)
            self.assertEqual(response['count'],1)
            self.assertEqual(response['evidence'][0].get('method','M2'),method)
        decisions[old['id']] = dict(status='rejected', signature=link['signature'], method_signatures=link['method_signatures'])
        _, loaded = self.view(combined, decisions)
        self.assertEqual(loaded['links'][0]['status'], 'rejected')
        with patch('folder_mount.digest', side_effect=AssertionError('缓存未复用')):
            standalone, _ = self.scan(['M2'], cache)
        self.assertEqual(standalone['links'], m2['links'])

    def test_s2_year_classification_and_legacy_cache_do_not_absorb_projects(self):
        self.put(2, f'规划条件盖章版/2024年用地规划条件/甲/{CODE}.txt', 'a')
        self.put(2, f'规划条件盖章版/2024年用地规划条件/乙/{OTHER}.txt', 'b')
        cat = ProjectCatalog(self.storage, self.root)
        legacy = dict(id='legacy',stage_id='S2',stage_name=STAGES[1][1],project_root='规划条件盖章版/2024年用地规划条件',project_name='2024年用地规划条件',physical_path=str(self.root/STAGES[1][1]),depth=2,reason='old',confidence='medium',status='review',updated_at='old')
        cat.upsert_db(legacy); cat.save(); cat.close()
        result, _ = self.scan()
        self.assertEqual({f['name'] for f in result['folders']}, {'甲', '乙', '未识别大目录材料'})
        self.assertEqual({e['project_name'] for e in result['project_code_index']}, {'甲', '乙'})
        self.assertEqual(len({e['directory_id'] for e in result['project_code_index']}), 2)

    def test_progress_and_skip_statistics(self):
        self.put(1, '甲/a.txt', 'a'); self.put(7, '乙/a.txt', 'a')
        self.put(1, '甲/empty.txt', ''); self.put(1, '甲/map.prj', 'coord')
        result, _ = self.scan(['M2'])
        self.assertEqual([e['current_stage'] for e in self.events if e['phase']=='扫描目录'], [sid for sid,_ in STAGES])
        self.assertEqual(result['skip_statistics']['reasons'], {'empty_file':1,'sidecar_or_temporary':1})
        self.assertEqual(result['file_count'] - result['hashed_count'], 2)

    def test_archive_policy_shared_by_web_and_script_and_security_blocks(self):
        self.put(1, '甲/broken.zip', b'broken')
        secret = self.base/'secret.txt'; secret.write_text('encrypted material',encoding='utf-8')
        subprocess.run([seven_zip(),'a','-tzip','-ppassword',str(self.root/STAGES[0][1]/'甲/encrypted.zip'),str(secret)],check=True,capture_output=True)
        self.put(1, '甲/a.txt', '项目代码：' + CODE)
        self.put(7, '乙/a.txt', '项目代码：' + CODE)
        result, _ = self.scan()
        self.assertEqual(len(result['links']), 1)
        self.assertTrue(all(not e['blocking'] for e in result['errors']))
        self.assertTrue(any(e['error']=='加密成员，未提供密码' for e in result['errors']))
        state = dict(status='idle',phase='idle')
        with patch.object(server,'BASE',self.base), patch.object(server,'SOURCE',self.root), patch.object(server,'MOUNT_DIR',self.storage), patch.object(server,'MOUNT_STATE',state), patch.object(server,'organize_independent_documents'):
            server.mount_scan(['M1'])
        self.assertEqual(state['status'], 'ready')
        with patch.object(run_stage_mount,'BASE',self.base), patch.object(run_stage_mount,'organize_independent_documents'):
            scripted = run_stage_mount.run_pipeline(self.root, ['M1'], verbose=False)
        self.assertEqual(scripted['errors'], result['errors'])
        unsafe = self.put(1, '甲/unsafe.zip', zip_bytes({'../outside.txt': 'bad'}))
        for tolerate in (False, True):
            with self.assertRaises(PreparationError):
                scan_folders(self.root, self.base/'index/解压记录.sqlite', {}, lambda p:None, ['M1'], self.storage, tolerate)
        self.assertFalse((unsafe.parent/'outside.txt').exists())
        with patch.object(server,'BASE',self.base), patch.object(server,'SOURCE',self.root), patch.object(server,'MOUNT_DIR',self.storage), patch.object(server,'MOUNT_STATE',state), patch.object(server,'organize_independent_documents'):
            server.mount_scan(['M1'])
        self.assertEqual(state['status'],'failed')
        snapshot=(self.base/'work/folder_mount/result.json').read_bytes()
        with patch.object(run_stage_mount,'BASE',self.base), patch.object(run_stage_mount,'organize_independent_documents'):
            with self.assertRaises(PreparationError): run_stage_mount.run_pipeline(self.root,['M1'],verbose=False)
        self.assertEqual((self.base/'work/folder_mount/result.json').read_bytes(),snapshot)
        with patch.object(run_stage_mount,'BASE',self.base), patch.object(run_stage_mount,'organize_independent_documents'), patch.object(run_stage_mount,'scan_folders',side_effect=OSError('文件发生变化')):
            with self.assertRaises(OSError): run_stage_mount.run_pipeline(self.root,['M1'],verbose=False)
        self.assertEqual(json.loads((self.base/'work/folder_mount/task.json').read_text(encoding='utf-8'))['status'],'failed')
        self.assertEqual((self.base/'work/folder_mount/result.json').read_bytes(),snapshot)

    def test_method_status_pair_filters_and_pagination(self):
        for n in range(51):
            code = f'2017-360199-27-03-{n:06d}'
            self.put(1, f'{code}/a.txt', 'a')
            self.put(7, f'{code}/a.txt', 'b')
        result, _ = self.scan()
        view, _ = self.view(result, params={'view':['projects'],'method':['M1'],'pair':['S1-S7'],'page':['2'],'status':['pending']})
        self.assertEqual((view['count'],view['pages'],len(view['rows'])), (51,2,1))
        view, _ = self.view(result, params={'view':['projects'],'method':['M2']})
        self.assertEqual(view['count'], 0)
        html = (BASE/'app/static/index.html').read_text(encoding='utf-8')
        self.assertEqual(len(set(re.findall(r'value="(S\d-S\d)"', html))), 21)
        self.assertRegex(html, r'value="M1">')

    def test_manual_boundary_saved_and_source_move_requires_review(self):
        self.put(1, f'{CODE}/a.txt', 'a'); self.put(7, f'{CODE}/a.txt', 'b')
        result, _ = self.scan()
        folder = result['folders'][0]
        cat = ProjectCatalog(self.storage, self.root)
        with self.assertRaises(ValueError): cat.update_project_boundary(folder['id'],'../outside')
        new = self.root/STAGES[0][1]/'新边界'
        (self.root/STAGES[0][1]/CODE).rename(new)
        cat.update_project_boundary(folder['id'],'新边界')
        cat.close()
        cat = ProjectCatalog(self.storage,self.root)
        try: self.assertEqual(cat.projects[folder['id']]['project_root'], '新边界')
        finally: cat.close()
        changed, _ = self.scan()
        self.assertFalse(changed['links'])
        # 项目代码不能随人工改名被推测补回。


if __name__ == '__main__': unittest.main()
