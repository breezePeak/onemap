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
from source_config import STAGES, STAGE_DICT
from project_catalog import ProjectCatalog

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'app'))
import server


def zip_bytes(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return stream.getvalue()


def stored_rar(path):
    def header(value):
        return struct.pack('<H', zlib.crc32(value) & 0xffff) + value
    data = b'rar-test-content'; name = b'item.pdf'
    main = header(struct.pack('<BHHHI', 0x73, 0, 13, 0, 0))
    item = header(struct.pack('<BHHIIBIIBBHI', 0x74, 0x8000, 32 + len(name), len(data), len(data), 2, zlib.crc32(data), 0, 20, 0x30, len(name), 0x20) + name)
    path.write_bytes(b'Rar!\x1a\x07\x00' + main + item + data + header(struct.pack('<BHH', 0x7b, 0x4000, 7)))


class FolderMountTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=BASE / 'work')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'source'
        self.database = self.base / 'index/解压记录.sqlite'
        for _, name in STAGES:
            (self.root / name).mkdir(parents=True)
        self.events = []
        source_patch = patch.object(server, 'SOURCE', self.root)
        source_patch.start()
        self.addCleanup(source_patch.stop)

    def put(self, stage, path, value):
        target = self.root / STAGES[stage - 1][1] / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
        return target

    def scan(self, cache=None):
        return scan_folders(self.root, self.database, cache or {}, self.events.append, storage_dir=self.base / 'mount')

    def api(self, path, payload=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = path
        handler.headers = {}
        handler.body = lambda *_: json.dumps(payload).encode()
        responses = []
        handler.json = lambda value, status=200: responses.append((value, status))
        if payload is None:
            handler.do_GET()
        else:
            handler.do_POST()
        return responses[-1]

    def test_content_business_aggregation_ambiguity_arbitrary_pairing_and_cache(self):
        # 测试跨阶段任意两两匹配（支持任意阶段比较，不限相邻配对）
        for stage, folder in ((1, 'A'), (2, 'B'), (2, 'C'), (3, 'D'), (4, 'E')):
            for n in range(3):
                self.put(stage, f'{folder}/附件/扫描件/{stage}-{n}.pdf', f'content-{n}'.encode())
        self.put(1, 'A/same.pdf', b'left')
        self.put(2, 'B/same.pdf', b'right')
        self.put(1, 'A/~$lock.xlsx', b'content-0')
        self.put(1, 'A/empty.pdf', b'')
        self.put(1, 'A/test.prj', b'coordinate')
        self.put(2, 'B/test.cpg', b'coordinate')
        self.put(1, 'loose.pdf', b'content-0')
        self.put(2, 'loose.pdf', b'content-0')
        self.put(1, 'orphan/a.pdf', b'orphan')
        self.put(2, 'unmatched/a.pdf', b'unmatched')
        ignored = self.root / '5.其他/X.pdf'
        ignored.parent.mkdir(exist_ok=True)
        ignored.write_bytes(b'content-0')

        result, cache = self.scan()
        # 任意两阶段比较下，生成所有跨阶段有效候选，且各关系包含3份证据
        self.assertGreater(len(result['links']), 0)
        self.assertTrue(all(e['evidence_count'] == 3 for e in result['links']))
        self.assertTrue(all(e['left']['name'] not in ('附件', '扫描件', '未分组材料') for e in result['links']))
        # 验证歧义检测
        self.assertTrue(any(e['ambiguous'] for e in result['links']))
        # 验证缓存复用
        with patch('folder_mount.digest', side_effect=AssertionError('缓存未复用')):
            self.assertEqual(self.scan(cache)[0], result)
        self.assertTrue(all(e['validation'] == 'verified' and e['physical_path'] for e in result['materials']))
        self.assertEqual(len({e['id'] for e in result['materials']}), len(result['materials']))

    def test_seven_stages_arbitrary_s1_and_s7_linking_and_small_dir_evidence(self):
        # 验证 S1 与 S7 的跨阶段精确关联，且证据保留小目录，关系挂在大目录
        self.put(1, 'G105改建工程/报件/设计附件.pdf', b'shared-s1-s7-content')
        self.put(1, 'G105改建工程/选址专章/图件.pdf', b'shared-s1-s7-second-content')
        self.put(7, '2017-360199-27-03-014458/建设工程设计方案/设计附件.pdf', b'shared-s1-s7-content')
        self.put(7, '2017-360199-27-03-014458/使用土地证明/图件.pdf', b'shared-s1-s7-second-content')

        result, _ = self.scan()
        s1_s7_links = [l for l in result['links'] if l['left']['stage'] == 'S1' and l['right']['stage'] == 'S7']
        self.assertEqual(len(s1_s7_links), 1)
        link = s1_s7_links[0]
        # 关系挂到双方大目录
        self.assertEqual(link['left']['name'], 'G105改建工程')
        self.assertEqual(link['right']['name'], '2017-360199-27-03-014458')
        # 聚合为一条候选关系，包含 2 条证据
        self.assertEqual(link['evidence_count'], 2)
        # 证据保留命中小目录
        ev0 = link['evidence'][0]
        self.assertTrue(any('报件' in d or '选址专章' in d for d in ev0['left_small_dirs']))
        self.assertTrue(any('建设工程设计方案' in d or '使用土地证明' in d for d in ev0['right_small_dirs']))

    def test_nested_in_place_legacy_reuse_and_incremental_changes(self):
        inner = zip_bytes({'nested.pdf': b'nested'})
        archive = self.put(1, 'A/material.zip', zip_bytes({'keep.pdf': b'keep', 'nested.zip': inner, 'change.pdf': b'one'}))
        original = self.put(1, 'A/material/keep.pdf', b'keep')
        self.put(2, 'B/keep.pdf', b'keep')
        self.put(2, 'B/change.pdf', b'one')
        self.put(2, 'B/nested.pdf', b'nested')
        before = hashlib.sha256(archive.read_bytes()).hexdigest()
        original_hash = hashlib.sha256(original.read_bytes()).hexdigest()

        result, cache = self.scan()
        self.assertEqual(result['links'][0]['evidence_count'], 3)
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), before)
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), original_hash)
        self.assertFalse(archive.with_name(archive.name + '.解压').joinpath('keep.pdf').exists())
        self.assertTrue(archive.with_name(archive.name + '.解压').joinpath('nested.zip.解压/nested.pdf').is_file())
        physical = [e['physical_path'] for e in result['materials']]
        self.assertEqual(len(physical), len(set(physical)))
        self.assertTrue(any('material.zip!nested.zip!nested.pdf' in e['source'] for e in result['materials']))
        again, _ = self.scan(cache)
        self.assertEqual(again['stages'][0]['extracted'], 0)
        self.assertEqual(again['links'], result['links'])
        archive.write_bytes(zip_bytes({'keep.pdf': b'keep', 'nested.zip': inner, 'change.pdf': b'two'}))
        changed, _ = self.scan(cache)
        self.assertEqual(changed['stages'][0]['extracted'], 1)
        self.assertNotEqual(changed['links'][0]['signature'], result['links'][0]['signature'])
        phases = [e['phase'] for e in self.events]
        self.assertLess(phases.index('校验'), phases.index('目录匹配'))

    def test_wrong_original_never_reused_or_overwritten(self):
        original = self.put(1, 'A/doc/a.pdf', b'wrong')
        archive = self.put(1, 'A/doc.zip', zip_bytes({'a.pdf': b'right'}))
        result, _ = self.scan()
        self.assertEqual(original.read_bytes(), b'wrong')
        self.assertEqual(archive.with_name('doc.zip.解压').joinpath('a.pdf').read_bytes(), b'right')
        self.assertEqual(result['stages'][0]['extracted'], 1)

    def test_real_rar_7z_and_virtual_business_directory(self):
        rar = self.root / STAGES[0][1] / 'root.rar'
        stored_rar(rar)
        source = self.base / 'item.pdf'
        source.write_bytes(b'rar-test-content')
        archive = self.root / STAGES[1][1] / 'root.7z'
        subprocess.run([seven_zip(), 'a', str(archive), str(source)], check=True, capture_output=True)
        result, cache = self.scan()
        self.assertEqual(len(result['links']), 1)
        self.assertEqual(result['links'][0]['left']['kind'], 'archive')
        self.assertEqual(result['links'][0]['right']['kind'], 'archive')
        self.assertEqual((rar.with_name('root.rar.解压') / 'item.pdf').read_bytes(), b'rar-test-content')
        again, _ = self.scan(cache)
        self.assertEqual(again['stages'][0]['reused'], 1)
        self.assertEqual(again['stages'][1]['reused'], 1)

    def test_api_decisions_details_filters_chains_restart_and_failed_snapshot(self):
        for n in range(23):
            self.put(1, f'A/deep/{n}.pdf', str(n).encode())
            self.put(2, f'B/{n}.pdf', str(n).encode())
        directory = self.base / 'state'
        state = dict(status='ready', phase='完成')
        with patch.object(server, 'MOUNT_DIR', directory), patch.object(server, 'MOUNT_STATE', state):
            result, cache = self.scan()
            server.write_json(directory / 'result.json', dict(result, scanned_at='test'))
            link = result['links'][0]
            payload = dict(id=link['id'], signature=link['signature'])
            self.assertFalse(server.mount_view()['chains'])
            for status in ('confirmed', 'rejected', 'pending', 'review', 'confirmed'):
                response, code = self.api('/api/folder-mount/decision', dict(payload, status=status))
                self.assertEqual(code, 200, response)
                self.assertEqual(server.mount_view()['links'][0]['status'], status)
            self.assertGreaterEqual(len(server.read_json(directory / 'decisions.json')[link['id']]['history']), 4)
            response, code = self.api('/api/folder-mount')
            self.assertEqual(code, 200)
            self.assertNotIn('evidence', response['links'][0])
            evidence, code = self.api('/api/folder-mount/links/' + link['id'] + '?page=2')
            self.assertEqual(code, 200)
            self.assertEqual(len(evidence['evidence']), 3)
            evidence, _ = self.api('/api/folder-mount/links/' + link['id'] + '?q=deep%2F10.pdf')
            self.assertEqual(evidence['count'], 1)
            response, _ = self.api('/api/folder-mount?status=confirmed')
            self.assertEqual(response['count'], 1)

            again, _ = self.scan(cache)
            server.write_json(directory / 'result.json', dict(again, scanned_at='again'))
            self.assertEqual(server.mount_view()['summary']['confirmed'], 1)
            with patch.object(server, 'MOUNT_STATE', dict(status='idle', phase='restart')):
                self.assertEqual(server.mount_view()['summary']['confirmed'], 1)
            self.put(1, 'A/deep/0.pdf', b'changed')
            changed, _ = self.scan(cache)
            server.write_json(directory / 'result.json', dict(changed, scanned_at='changed'))
            self.assertEqual(server.mount_view()['links'][0]['status'], 'review')
            _, code = self.api('/api/folder-mount/decision', dict(payload, status='confirmed'))
            self.assertEqual(code, 400)

            self.put(1, 'A/broken.zip', b'broken')
            old = (directory / 'result.json').read_bytes()
            with patch.object(server, 'SOURCE', self.root), patch.object(server, 'BASE', self.base):
                server.mount_scan(['M2'])
            self.assertEqual(state['status'], 'failed')
            self.assertEqual((directory / 'result.json').read_bytes(), old)
            self.assertTrue(server.mount_view()['historical'])
            self.assertTrue(server.mount_view()['errors'])

    def test_methods_and_duplicate_submissions_rejected(self):
        for methods in ([], ['M1'], ['M2', 'M3'], None, 'M2'):
            with self.subTest(methods=methods):
                with self.assertRaises(ValueError):
                    validate_methods(methods)

    def test_project_nodes_skip_classification_year_and_attachment_directories(self):
        path = self.put(4, '出让/04出让档案整理/2023年/2023-9 中宝/06 测量/附件/a.pdf', b'x')
        entry = folder_entry(path, str(path), self.root)
        self.assertEqual(entry['name'], '2023-9 中宝')
        self.assertTrue(entry['folder'].endswith('2023-9 中宝'))
        source = str(self.root / STAGES[2][1] / '2019——2023年报批附件材料.zip') + '!2021/赣江新区2021年报批材料/幸福河项目.zip!幸福河项目/附件/a.pdf'
        physical = self.put(3, '2019——2023年报批附件材料.zip.解压/2021/幸福河项目.zip.解压/幸福河项目/附件/a.pdf', b'x')
        entry = folder_entry(physical, source, self.root)
        self.assertEqual(entry['name'], '幸福河项目')
        self.assertTrue(Path(entry['folder']).is_relative_to(self.root))
        self.assertNotIn('work', Path(entry['folder']).relative_to(self.root).parts)

    def test_shared_project_candidates_are_kept_separate_in_stage_cells(self):
        self.put(1, '项目甲/a.pdf', b'shared')
        self.put(3, '项目乙/a.pdf', b'shared')
        self.put(3, '项目丙/a.pdf', b'shared')
        result, _ = self.scan()
        row = project_rows(result)[0]
        self.assertTrue(row['ambiguous'])
        self.assertEqual(len(row['stages']['S3']), 2)
        self.assertTrue(all(e['status'] == 'pending' for e in result['links']))

    def test_project_boundary_adjustment_persists_and_triggers_rescan(self):
        # 验证人工修改大目录边界并持久化
        catalog = ProjectCatalog(storage_dir=self.base / 'mount', root=self.root)
        p = catalog.register_or_find('S1', '原项目/附件/a.pdf', is_dir=False)
        self.assertEqual(p['project_name'], '原项目')
        updated = catalog.update_project_boundary(p['id'], '新项目大目录', '新项目大目录')
        self.assertEqual(updated['project_name'], '新项目大目录')
        self.assertEqual(updated['status'], 'manual')
        # 重新加载验证持久化存库
        reloaded = ProjectCatalog(storage_dir=self.base / 'mount', root=self.root)
        self.assertEqual(reloaded.projects[p['id']]['project_name'], '新项目大目录')
        catalog.close()
        reloaded.close()

    def test_database_priority_cache_avoids_repeated_analysis(self):
        storage = self.base / 'cache_test_mount'
        cat1 = ProjectCatalog(storage_dir=storage, root=self.root)
        p1 = cat1.register_or_find('S1', 'G105改建工程/材料/doc.pdf', is_dir=False)
        cat1.save()
        cat1.close()

        # 再次实例化，必须优先查库
        cat2 = ProjectCatalog(storage_dir=storage, root=self.root)
        # 覆盖研判函数：若触发研判直接抛出异常
        cat2.determine_project_root = lambda *args: (_ for _ in ()).throw(AssertionError('不应调用研判，必须优先命中数据库！'))
        # 检查该项目下其他子目录或材料，必须直接命中数据库已有项目大目录
        hit = cat2.register_or_find('S1', 'G105改建工程/另一子目录/深层附件/another.pdf', is_dir=False)
        self.assertEqual(hit['id'], p1['id'])
        self.assertEqual(hit['project_name'], 'G105改建工程')
        cat2.close()



if __name__ == '__main__':
    unittest.main()
