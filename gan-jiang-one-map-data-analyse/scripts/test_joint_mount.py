"""M1/M2 联合补漏、交叉校验和候选隔离回归。"""
import json
import io
import shutil
import zipfile
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch

import test_project_codes as fixtures
from folder_mount import project_rows
from project_catalog import ProjectCatalog
from organize_independent_documents import organize_independent_documents
from extract_archives import disk_path, ArchiveRecords
from verify_joint_mount import verify
import review_service

CODE, OTHER = fixtures.CODE, fixtures.OTHER


class JointMountTests(unittest.TestCase):
    setUp = fixtures.ProjectCodeTests.setUp
    put = fixtures.ProjectCodeTests.put
    scan = fixtures.ProjectCodeTests.scan
    view = fixtures.ProjectCodeTests.view

    def test_m2_corroboration_and_m1_fills_uncovered_s7(self):
        for stage in (1, 6): self.put(stage, f'{CODE}/shared.txt', '附件')
        self.put(7, f'{CODE}/different.txt', '不相同')
        m2, cache = self.scan(['M2'])
        combined, _ = self.scan(['M1', 'M2'], cache)
        self.assertEqual(len(m2['links']), 1)
        self.assertEqual(len(combined['links']), 3)
        old = m2['links'][0]
        link = next(l for l in combined['links'] if l['id'] == old['id'])
        self.assertEqual(link['m1_validation']['state'], 'corroborated')
        self.assertEqual(link['evidence_type'], 'M1+M2')
        self.assertEqual(link['method_evidence']['M2'], old['evidence'])
        self.assertEqual(link['method_signatures']['M2'], old['signature'])
        self.assertEqual(len(project_rows(combined)), 3)
        link['status'] = 'confirmed'
        self.assertEqual(len(project_rows(combined)), 2)
        cat = ProjectCatalog(self.storage, self.root)
        try:
            snapshots = [json.loads(row[0]) for row in cat.db.execute('SELECT payload FROM relation_snapshots WHERE id=?', (old['id'],))]
            self.assertEqual(len(snapshots), 2)
            self.assertTrue(any(s['signature'] == old['signature'] and s['evidence'] == old['evidence'] for s in snapshots))
        finally: cat.close()

    def test_m2_conflict_missing_and_uncertain_keep_original_evidence(self):
        for case, left, right, state in (
            ('conflict', CODE, OTHER, 'conflict'),
            ('left', None, CODE, 'missing_left'),
            ('right', CODE, None, 'missing_right'),
            ('both', None, None, 'missing_both'),
            ('uncertain', CODE + '\n' + OTHER, None, 'unresolved_ownership'),
        ):
            self.put(1, f'{case}/shared.txt', case)
            self.put(7, f'{case}/shared.txt', case)
            for stage, value in ((1, left), (7, right)):
                if value: self.put(stage, f'{case}/code.txt', '项目代码：' + value)
        m2, cache = self.scan(['M2'])
        combined, _ = self.scan(['M1', 'M2'], cache)
        self.assertEqual(len(m2['links']), len(combined['links']))
        original = {l['id']: l for l in m2['links']}
        states = {l['left']['name']: l['m1_validation']['state'] for l in combined['links'] if l['left']['name'] == l['right']['name']}
        self.assertEqual(states, dict(conflict='conflict', left='missing_left', right='missing_right', both='missing_both', uncertain='unresolved_ownership'))
        for link in combined['links']:
            self.assertEqual(link['method_evidence']['M2'], original[link['id']]['evidence'])
            if link['left']['name'] == link['right']['name']:
                self.assertEqual(link['conflict'], link['left']['name'] == 'conflict')
        conflict = next(l for l in combined['links'] if l['conflict'])
        self.assertEqual((conflict['status'], conflict['evidence_type']), ('review', 'conflict'))

    def test_unlinked_filters_use_selected_method_and_all_21_pairs(self):
        self.put(1, f'{CODE}/a.txt', '相同')
        self.put(6, f'{OTHER}/a.txt', '相同')
        self.put(7, f'{CODE}/b.txt', '不同')
        self.put(3, '待审核/ambiguous.txt', OTHER)
        result, _ = self.scan(['M1', 'M2'])
        for method, stage in (('M1', 'S6'), ('M2', 'S7')):
            for view in ('links', 'projects'):
                response, _ = self.view(result, params=dict(method=[method], status=['unlinked'], view=[view], pair=[f'S3-{stage}']))
                folders = response['unlinked'] if view == 'links' else [f for r in response['rows'] for fs in r['stages'].values() for f in fs]
                self.assertEqual({f['stage'] for f in folders}, {'S3', stage})
        for a in range(1, 8):
            for b in range(a + 1, 8):
                response, _ = self.view(result, params=dict(pair=[f'S{a}-S{b}']))
                self.assertTrue(all(l['left']['stage'] == f'S{a}' and l['right']['stage'] == f'S{b}' for l in response['links']))

    def test_boundary_history_and_review_records_restored(self):
        self.put(1, '甲/a.txt', 'a')
        result, _ = self.scan()
        folder = next(f for f in result['folders'] if f['kind'] != 'ungrouped')
        cat = ProjectCatalog(self.storage, self.root)
        try:
            cat.update_project_boundary(folder['id'], '甲', '人工名称')
            old, new = cat.db.execute('SELECT before_json,after_json FROM boundary_history').fetchone()
            self.assertEqual(json.loads(old)['project_name'], '甲')
            self.assertEqual(json.loads(new)['project_name'], '人工名称')
        finally: cat.close()
        records = json.loads((Path(__file__).resolve().parent.parent / 'reviewed_fields.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(records), 6)
        self.assertTrue(all(r.get('source') and r.get('evidence_image') for r in records))

    def test_ocr_failure_never_blocks_m2_and_m1_never_hashes(self):
        self.put(1, '甲/scan.pdf', b'shared')
        self.put(7, '乙/scan.pdf', b'shared')
        with patch.dict('os.environ', {'ONEMAP_M1_OPTIONS': '{"ocr":true}'}), patch('extract_pdfs.read_pdf_text', return_value=['']), patch('code_recognition.Recognition.ocr_page', side_effect=OSError('OCR unavailable')):
            combined, _ = self.scan(['M1', 'M2'])
        self.assertEqual(len(combined['links']), 1)
        self.assertEqual(combined['links'][0]['methods'], ['M2'])
        self.assertTrue(any(e.get('state') == 'failed' for e in combined['errors']))
        with patch('folder_mount.digest', side_effect=AssertionError('M1 cannot hash')):
            standalone, _ = self.scan(['M1'])
        self.assertEqual(standalone['hashed_count'], 0)

    def test_long_paths_and_container_organizing_preserve_project_materials(self):
        from source_config import STAGES
        deep = self.root / STAGES[0][1] / '项目甲' / ('深目录' * 40) / ('材料' * 40) / 'a.txt'
        disk_path(deep.parent).mkdir(parents=True)
        project = deep.parents[2]
        assert project.resolve().is_relative_to(self.root.resolve())
        self.addCleanup(shutil.rmtree, disk_path(project))
        disk_path(deep).write_text('shared', encoding='utf-8')
        self.assertGreater(len(str(deep)), 260)
        self.put(7, '项目乙/a.txt', 'shared')
        self.put(2, '规划条件盖章版/2024/独立文书.pdf', 'pdf')
        material = self.put(2, '规划条件盖章版/2024/已有项目/材料.pdf', 'pdf')
        organized = organize_independent_documents(self.root, verbose=False)
        self.assertEqual(organized['moved'], 1)
        self.assertTrue(material.is_file())
        result, _ = self.scan(['M2'])
        self.assertTrue(any(l['left']['name'] == '项目甲' and l['right']['name'] == '项目乙' for l in result['links']))

    def test_zip_opens_once_for_listing_and_once_for_all_member_streams(self):
        archive = self.put(1, '批次.zip', fixtures.zip_bytes({f'{i}.txt': f'body-{i}' for i in range(10)}))
        records = ArchiveRecords(self.base / 'archives', self.root)
        try:
            with patch('extract_archives.zipfile.ZipFile', wraps=zipfile.ZipFile) as opened:
                counts, _ = records.extract(archive)
            self.assertEqual(opened.call_count, 2)
            self.assertEqual(counts, dict(extracted=10, reused=0, failed=0))
            for i in range(10):
                self.assertEqual((archive.with_name(archive.name + '.解压') / f'{i}.txt').read_text(), f'body-{i}')
        finally: records.db.close()

    def test_native_text_reuses_available_sha_without_mixing_ownership(self):
        for stage, name in ((1, '甲'), (6, '乙'), (7, '甲')):
            self.put(stage, f'{name}/same.pdf', b'same PDF content')
        with patch('extract_pdfs.read_pdf_text', return_value=['项目名称：甲\n项目代码：' + CODE]) as read:
            result, _ = self.scan(['M1', 'M2'])
        self.assertEqual(read.call_count, 1)
        current = [r['stage'] for r in result['project_code_index'] if r['ownership_status'] == 'current_project']
        self.assertEqual(current, ['S1', 'S7'])

    def test_archive_reused_member_keeps_existing_physical_project_boundary(self):
        relative = '出让/04出让档案整理/2024年/项目甲/a.txt'
        original = self.put(4, relative, 'shared')
        self.put(4, '出让.zip', fixtures.zip_bytes({relative: 'shared'}))
        self.put(7, '项目乙/a.txt', 'shared')
        result, _ = self.scan(['M2'])
        s4 = [f for f in result['folders'] if f['stage'] == 'S4' and f['kind'] != 'ungrouped']
        self.assertEqual(len(s4), 1)
        self.assertEqual(Path(s4[0]['folder']), original.parent)
        self.assertEqual(len(result['links']), 1)
        self.assertEqual(result['links'][0]['left']['id'], s4[0]['id'])

    def test_acceptance_runner_indexes_archive_members_and_checks_union(self):
        for stage in (1, 7): self.put(stage, '项目甲.zip', fixtures.zip_bytes({'a.txt': '项目代码：' + CODE}))
        copied = self.base / 'copy'
        shutil.copytree(self.root, copied)
        with redirect_stdout(io.StringIO()):
            report = verify(copied, self.root, self.base / 'acceptance')
        self.assertEqual((report['m2_standalone_candidates'], report['m1_standalone_candidates'], report['dual_hits']), (1, 1, 1))
        self.assertEqual(report['skip_statistics']['valid_materials'], 2)
        self.assertTrue(all(report['checks'].values()))

    def test_archive_subdirectory_code_has_real_directory_source(self):
        self.put(1, '项目甲.zip', fixtures.zip_bytes({f'{CODE}/附件/a.txt': 'material'}))
        self.put(7, f'{CODE}/b.txt', 'different')
        result, _ = self.scan(['M1'])
        row = next(r for r in result['project_code_index'] if r['source_type'] == 'subdirectory' and r['normalized_code'] == CODE)
        self.assertTrue(Path(row['physical_path']).is_dir())
        self.assertEqual(Path(row['physical_path']).name, CODE)
        self.assertEqual(len(result['links']), 1)

    def test_manual_json_retry_preserves_original_on_persistent_windows_lock(self):
        target = self.base / 'decisions.json'
        review_service.write_json(target, {'history': ['original']})
        error = PermissionError('sharing violation'); error.winerror = 32
        replace = Path.replace
        calls = []
        def briefly_locked(temp, path):
            calls.append(path)
            if len(calls) < 3: raise error
            return replace(temp, path)
        with patch.object(Path, 'replace', briefly_locked), patch('review_service.time.sleep'):
            review_service.write_json(target, {'history': ['original', 'confirmed']})
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['history'], ['original', 'confirmed'])
        with patch.object(Path, 'replace', side_effect=error) as retry, patch('review_service.time.sleep'):
            with self.assertRaises(PermissionError): review_service.write_json(target, {})
        self.assertEqual(retry.call_count, 4)
        self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['history'], ['original', 'confirmed'])

    def test_native_pdf_reads_are_bounded_and_keep_all_evidence(self):
        for number in range(8): self.put(1 if number < 4 else 7, f'项目甲/{number}.pdf', bytes([number]))
        lock = threading.Lock()
        active = peak = 0
        def read(path):
            nonlocal active, peak
            with lock: active += 1; peak = max(peak, active)
            try:
                time.sleep(0.03)
                return ['项目代码：' + CODE]
            finally:
                with lock: active -= 1
        with patch('extract_pdfs.read_pdf_text', side_effect=read) as reader:
            result, _ = self.scan(['M1'])
        self.assertEqual(reader.call_count, 8)
        self.assertTrue(1 < peak <= 4)
        self.assertEqual(len(result['links']), 1)
        self.assertEqual(len(result['project_code_index']), 8)

    def test_reverse_boundary_review_on_m1_m2_conflicts_and_shared_parcel_codes(self):
        # 1. S3 某未拆分目录内同时出现两个完整项目代码 -> M1 反向触发该目录 boundary_review
        self.put(3, '某待核实建设工程/doc.txt', f'项目代码：{CODE}\n另一项目代码：{OTHER}')
        # 2. S4 两个独立宗地共享同一项目代码 CODE 及相同附件 -> 保留两条候选，不自动合并宗地，且触发 boundary_review
        self.put(4, '出让/2023年度/宗地甲/a.txt', f'项目名称：宗地甲\n项目代码：{CODE}')
        self.put(4, '出让/2023年度/宗地甲/shared.txt', '完全相同的共用附件字节')
        self.put(4, '出让/2023年度/宗地乙/a.txt', f'项目名称：宗地乙\n项目代码：{CODE}')
        self.put(4, '出让/2023年度/宗地乙/shared.txt', '完全相同的共用附件字节')
        # 3. S6 对应项目代码 CODE 及相同附件
        self.put(6, f'{CODE}/审批表.txt', f'项目代码：{CODE}')
        self.put(6, f'{CODE}/shared.txt', '完全相同的共用附件字节')

        result, _ = self.scan(['M1', 'M2'])
        folders_by_name = {f['name']: f for f in result['folders'] if f['kind'] != 'ungrouped'}
        self.assertIn('某待核实建设工程', folders_by_name)
        self.assertTrue(folders_by_name['某待核实建设工程']['boundary_review'])
        self.assertIn('多个不同项目代码', folders_by_name['某待核实建设工程']['boundary_review_reason'])

        # 两个宗地保持独立，未合并为一个目录
        self.assertIn('宗地甲', folders_by_name)
        self.assertIn('宗地乙', folders_by_name)
        self.assertTrue(folders_by_name['宗地甲']['boundary_review'])
        self.assertTrue(folders_by_name['宗地乙']['boundary_review'])

        # S4 与 S6 产生 2 条独立的 M1+M2 联合关系（宗地甲<->S6, 宗地乙<->S6）
        s4_s6_links = [l for l in result['links'] if l['left']['stage'] == 'S4' and l['right']['stage'] == 'S6']
        self.assertEqual(len(s4_s6_links), 2)
        self.assertTrue(all(l['evidence_type'] == 'M1+M2' for l in s4_s6_links))


if __name__ == '__main__': unittest.main()
