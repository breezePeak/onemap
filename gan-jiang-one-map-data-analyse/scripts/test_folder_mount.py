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

            self.put(1, 'A/unsafe.zip', zip_bytes({'../outside.pdf': b'unsafe'}))
            old = (directory / 'result.json').read_bytes()
            with patch.object(server, 'SOURCE', self.root), patch.object(server, 'BASE', self.base):
                server.mount_scan(['M2'])
            self.assertEqual(state['status'], 'failed')
            self.assertEqual((directory / 'result.json').read_bytes(), old)
            self.assertTrue(server.mount_view()['historical'])
            self.assertTrue(server.mount_view()['errors'])

    def test_methods_and_duplicate_submissions_rejected(self):
        for methods in ([], ['M1', 'M1'], ['M2', 'M3'], None, 'M2'):
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
        rows = project_rows(result)
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0]['ambiguous'])
        self.assertTrue(all(sum(map(len, row['stages'].values())) == 1 for row in rows))
        self.assertTrue(all(e['status'] == 'pending' for e in result['links']))

    def test_format_directories_and_cached_pdf_are_not_projects(self):
        storage = self.base / 'mount'
        catalog = ProjectCatalog(storage_dir=storage, root=self.root)
        stale = catalog.register_or_find('S3', '2021/批次甲/旧项目', is_dir=True)
        stale.update(project_root='2021/批次甲/PDF', project_name='PDF')
        catalog.upsert_db(stale)
        catalog.save()
        catalog.close()
        catalog = ProjectCatalog(storage_dir=storage, root=self.root)
        self.assertNotIn(stale['id'], catalog.projects)
        for name in ('PDF', 'pdf', 'CAD', 'SHP', 'Word', 'Excel'):
            project = catalog.register_or_find('S3', f'2021/批次甲/{name}/材料.pdf', is_dir=False)
            self.assertEqual(project['project_name'], '批次甲')
        catalog.close()

    def test_archive_project_paths_are_real_and_stale_snapshot_is_filtered(self):
        self.put(3, '2019——2023年报批附件材料.zip', zip_bytes({'2021/批次甲/PDF/a.pdf': b'shared'}))
        self.put(4, '项目乙/a.pdf', b'shared')
        result, _ = self.scan()
        link = result['links'][0]
        self.assertEqual(link['left']['name'], '批次甲')
        self.assertTrue(Path(link['left']['folder']).is_dir())
        self.assertIn('.zip.解压', link['left']['folder'])
        self.assertTrue(all(Path(p).is_dir() for p in link['left']['paths']))
        stale = dict(link['left'], id='stale', name='PDF', folder=str(self.root / 'missing.zip/PDF'))
        file_node = dict(link['right'], id='file', folder=str(self.root / STAGES[3][1] / '项目乙/a.pdf'))
        result['folders'].extend([stale, file_node])
        result['links'].append(dict(link, id='stale-link', left=stale))
        with patch.object(server, 'MOUNT_DIR', self.base / 'mount'):
            server.write_json(self.base / 'mount/result.json', result)
            visible = server.mount_result()
        self.assertFalse({'stale', 'file'} & {f['id'] for f in visible['folders']})
        self.assertNotIn('stale-link', {e['id'] for e in visible['links']})

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

    def test_m2_format_and_year_containers_never_become_project_roots(self):
        for number, (stage, _) in enumerate(STAGES, 1):
            self.put(number, 'PDF/2024年/工程甲/a.pdf', b'shared')
        result, _ = self.scan()
        projects = [f for f in result['folders'] if f['kind'] != 'ungrouped']
        self.assertEqual({f['name'] for f in projects}, {'工程甲'})
        self.assertEqual(len(projects), 7)
        self.assertEqual(len(result['links']), 21)
        with patch.object(server, 'MOUNT_DIR', self.base / 'mount'):
            server.write_json(self.base / 'mount/result.json', dict(result, scanned_at='test'))
            visible = server.mount_result()
        self.assertEqual(len(visible['links']), 21)
        self.assertTrue(all(Path(f['folder']).is_dir() for f in visible['folders']))

    def test_m2_unassigned_compilation_archive_cannot_link_a_stage_root(self):
        self.put(3, '2019——2023年报批附件材料.zip', zip_bytes({'loose.pdf': b'shared'}))
        self.put(4, '项目乙/a.pdf', b'shared')
        result, _ = self.scan()
        self.assertEqual(result['links'], [])
        material = next(e for e in result['materials'] if e['stage'] == 'S3')
        self.assertEqual(material['kind'], 'ungrouped')
        folder = next(f for f in result['folders'] if f['id'] == material['business_directory_id'])
        self.assertEqual(folder['kind'], 'ungrouped')

    def test_m2_successful_history_survives_obsolete_inventory(self):
        self.put(1, '项目甲/a.pdf', b'shared')
        self.put(7, '项目乙/a.pdf', b'shared')
        result, _ = self.scan()
        result.update(rule_version='previous-seven-stage-rule', scanned_at='previous-success')
        link = result['links'][0]
        storage = self.base / 'mount'
        decisions = {link['id']: dict(status='confirmed', signature=link['signature'], history=[{'status':'pending'}])}
        with patch.object(server, 'MOUNT_DIR', storage), patch.object(server, 'MOUNT_STATE', dict(status='ready', phase='完成')):
            server.write_json(storage / 'result.json', result)
            server.write_json(storage / 'inventory.json', dict(folders=[], stages=[{'stage_id':'S1'}], errors=[], rule_version='old-four-stage'))
            server.write_json(storage / 'decisions.json', decisions)
            view = server.mount_view()
            loaded = server.mount_result()
        self.assertEqual(len(loaded['links']), 1)
        self.assertEqual(len(loaded['stages']), 7)
        self.assertTrue(view['historical'])
        self.assertEqual(loaded['links'][0]['status'], 'review')
        self.assertEqual(loaded['links'][0]['confirmation'], decisions[link['id']])
        self.assertEqual(server.read_json(storage / 'decisions.json'), decisions)

    def test_m2_inventory_fallback_requires_current_rule_and_no_success(self):
        self.put(1, '项目甲/a.pdf', b'one')
        result, _ = self.scan()
        storage = self.base / 'mount'
        with patch.object(server, 'MOUNT_DIR', storage):
            inventory = {k:result[k] for k in ('folders', 'stages', 'errors', 'rule_version', 'methods')}
            server.write_json(storage / 'inventory.json', inventory)
            loaded = server.mount_result()
            self.assertTrue(loaded.get('inventory_only'))
            self.assertEqual(loaded['folders'], result['folders'])
            inventory['rule_version'] = 'old-four-stage'
            server.write_json(storage / 'inventory.json', inventory)
            self.assertEqual(server.mount_result()['folders'], [])

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

    def test_agent_semantic_batch_collection_multi_projects_and_materials(self):
        # 测试点 1, 2, 4, 5: 批次目录包含多个项目和多层材料目录、项目名不含“项目”二字、同一项目材料分布在不同子目录
        self.put(3, '2024年第五批次/儒乐湖大道北延工程/报批材料/红线图/a.pdf', b'shared-road-1')
        self.put(3, '2024年第五批次/儒乐湖大道北延工程/用地批复/b.pdf', b'shared-road-2')
        self.put(3, '2024年第五批次/DXJJ2024001/报批材料/c.pdf', b'shared-parcel-1')
        self.put(6, '2024-360199-48-01-099999/建设用地规划许可证申请表/a.pdf', b'shared-road-1')
        self.put(6, '2024-360199-48-01-099999/项目四址范围（附图）、用地权属及拆迁情况/b.pdf', b'shared-road-2')
        self.put(4, '出让/2024年度/DXJJ2024001/出让合同/c.pdf', b'shared-parcel-1')

        storage = self.base / 'mount'
        catalog = ProjectCatalog(storage_dir=storage, root=self.root)
        # Agent 先查看目录树结构并提交语义判断
        tree = catalog.inspect_stage_tree('S3', max_depth=3)
        self.assertEqual(tree[0]['children'][0]['name'], '2024年第五批次')
        applied = catalog.apply_agent_decisions([
            {
                'stage_id': 'S3',
                'rel_path': '2024年第五批次',
                'node_type': 'collection',
                'reason': '2024年第五批次为批次汇总目录，包含儒乐湖大道北延工程与DXJJ2024001两个独立项目',
                'evidence_paths': ['2024年第五批次/儒乐湖大道北延工程', '2024年第五批次/DXJJ2024001'],
                'children': [
                    {
                        'stage_id': 'S3',
                        'rel_path': '2024年第五批次/儒乐湖大道北延工程',
                        'node_type': 'project',
                        'project_name': '儒乐湖大道北延工程',
                        'reason': '不含“项目”二字的独立道路工程大目录',
                        'evidence_paths': ['2024年第五批次/儒乐湖大道北延工程/报批材料/红线图/a.pdf'],
                        'children': [
                            {
                                'stage_id': 'S3',
                                'rel_path': '2024年第五批次/儒乐湖大道北延工程/报批材料',
                                'node_type': 'material',
                                'parent_project_root': '2024年第五批次/儒乐湖大道北延工程',
                                'reason': '项目内部多层报批材料目录',
                                'evidence_paths': ['2024年第五批次/儒乐湖大道北延工程/报批材料/红线图/a.pdf'],
                            }
                        ],
                    },
                    {
                        'stage_id': 'S3',
                        'rel_path': '2024年第五批次/DXJJ2024001',
                        'node_type': 'project',
                        'project_name': 'DXJJ2024001',
                        'reason': '以宗地交易编号命名的独立项目目录',
                        'evidence_paths': ['2024年第五批次/DXJJ2024001/报批材料/c.pdf'],
                    },
                ],
            }
        ])
        self.assertEqual(len(applied), 4)
        catalog.close()

        result, _ = self.scan()
        s3_names = {f['name'] for f in result['folders'] if f['stage'] == 'S3' and f['kind'] != 'ungrouped'}
        self.assertEqual(s3_names, {'儒乐湖大道北延工程', 'DXJJ2024001'})
        self.assertNotIn('2024年第五批次', s3_names)
        # 验证同一项目材料分布在不同子目录时聚合到同一项目大目录关系，且包含2份证据
        s3_s6 = [l for l in result['links'] if l['left']['stage'] == 'S3' and l['right']['stage'] == 'S6']
        self.assertEqual(len(s3_s6), 1)
        self.assertEqual(s3_s6[0]['left']['name'], '儒乐湖大道北延工程')
        self.assertEqual(s3_s6[0]['evidence_count'], 2)

    def test_archive_containing_multiple_projects_and_legacy_parent_lock_eviction(self):
        # 测试点 3, 6: 压缩包内包含多个独立项目 + 旧数据库把父目录误存为项目时不锁死子项目发现
        self.put(3, '一码管地报批相关材料汇总提供.zip', zip_bytes({
            '01赣江新区2025年度第01批次集镇建设用地/1项目红线与权属/红线.pdf': b'zip-proj-1',
            '02赣能南昌赣江新区60MW渔光互补光伏发电项目/4用地批复/批复.pdf': b'zip-proj-2',
        }))
        self.put(4, '出让/2018年度/02 赣国土资网交地【2018】XJ002号/DXJJ2018002/合同.pdf', b'parcel-2018-2')
        self.put(4, '出让/2018年度/02 赣国土资网交地【2018】XJ002号/DXJJ2018003/合同.pdf', b'parcel-2018-3')
        self.put(5, '土地首次登记附件/360122009006GB00002W00000000/合同.pdf', b'parcel-2018-2')
        self.put(6, '2025-360199-44-01-012345/项目审批、核准备案文件/批复.pdf', b'zip-proj-2')

        # 模拟旧数据库错误地把父级交地目录锁死为了单一项目
        storage = self.base / 'mount'
        cat = ProjectCatalog(storage_dir=storage, root=self.root)
        wrong_parent = {
            'id': 'legacy_wrong_parent',
            'stage_id': 'S4',
            'stage_name': STAGES[3][1],
            'project_root': '出让/2018年度/02 赣国土资网交地【2018】XJ002号',
            'project_name': '02 赣国土资网交地【2018】XJ002号',
            'physical_path': str(self.root / STAGES[3][1] / '出让/2018年度/02 赣国土资网交地【2018】XJ002号'),
            'depth': 3,
            'reason': '旧规则误判父目录',
            'confidence': 'high',
            'status': 'confirmed',
            'files': 2,
            'updated_at': '2026-01-01T00:00:00',
            'node_type': 'project',
            'decision_source': 'rule',
        }
        cat.upsert_db(wrong_parent)
        cat.db.commit()
        cat.close()

        result, _ = self.scan()
        s3_projects = {f['name'] for f in result['folders'] if f['stage'] == 'S3' and f['kind'] != 'ungrouped'}
        self.assertEqual(s3_projects, {
            '01赣江新区2025年度第01批次集镇建设用地',
            '02赣能南昌赣江新区60MW渔光互补光伏发电项目',
        })
        s4_projects = {f['name'] for f in result['folders'] if f['stage'] == 'S4' and f['kind'] != 'ungrouped'}
        self.assertEqual(s4_projects, {'DXJJ2018002', 'DXJJ2018003'})
        self.assertNotIn('02 赣国土资网交地【2018】XJ002号', s4_projects)

    def test_agent_decision_rejects_nonexistent_paths_and_protects_manual_boundary(self):
        # 测试点 7, 8, 13: Agent 引用不存在目录或证据被拒绝；人工确认受保护；边界变更触发关系复核且新边界生效
        self.put(1, '外层汇编/真实子项目A/选址.pdf', b'shared-boundary-change')
        self.put(2, '规划条件项目A/选址.pdf', b'shared-boundary-change')

        storage = self.base / 'mount'
        cat = ProjectCatalog(storage_dir=storage, root=self.root)
        # 1. 引用不存在的目录 -> 拒绝
        with self.assertRaises(ValueError):
            cat.apply_agent_decisions([{
                'stage_id': 'S1',
                'rel_path': '根本不存在的虚拟项目',
                'node_type': 'project',
                'reason': '测试虚构目录',
            }])
        # 2. 引用不存在的证据文件 -> 拒绝
        with self.assertRaises(ValueError):
            cat.apply_agent_decisions([{
                'stage_id': 'S1',
                'rel_path': '外层汇编/真实子项目A',
                'node_type': 'project',
                'reason': '测试虚构证据路径',
                'evidence_paths': ['外层汇编/真实子项目A/不存在的批复.pdf'],
            }])
        # 3. 缺少判断依据 reason -> 拒绝
        with self.assertRaises(ValueError):
            cat.apply_agent_decisions([{
                'stage_id': 'S1',
                'rel_path': '外层汇编/真实子项目A',
                'node_type': 'project',
                'reason': '   ',
            }])

        # 4. 保护 status == 'manual' 的人工确认边界
        p_s2 = cat.register_or_find('S2', '规划条件项目A/选址.pdf', is_dir=False)
        cat.update_project_boundary(p_s2['id'], '规划条件项目A', '人工锁定规划项目A')
        res = cat.apply_agent_decisions([{
            'stage_id': 'S2',
            'rel_path': '规划条件项目A',
            'node_type': 'unresolved',
            'reason': '尝试覆盖人工确认边界',
        }])
        self.assertEqual(res[0].get('skipped'), 'manual_protected')
        self.assertEqual(cat.projects[p_s2['id']]['status'], 'manual')
        self.assertEqual(cat.projects[p_s2['id']]['project_name'], '人工锁定规划项目A')

        # 5. Agent 将 S1 外层目录判定为 collection、子目录判定为 project 后，M2 证据挂接到新子项目边界
        cat.apply_agent_decisions([
            {
                'stage_id': 'S1',
                'rel_path': '外层汇编',
                'node_type': 'collection',
                'reason': '外层汇编容器',
                'evidence_paths': ['外层汇编/真实子项目A'],
                'children': [{
                    'stage_id': 'S1',
                    'rel_path': '外层汇编/真实子项目A',
                    'node_type': 'project',
                    'project_name': '真实子项目A',
                    'reason': '真实子项目边界',
                    'evidence_paths': ['外层汇编/真实子项目A/选址.pdf'],
                }],
            }
        ])
        cat.close()

        scan_res, _ = self.scan()
        self.assertEqual(len(scan_res['links']), 1)
        self.assertEqual(scan_res['links'][0]['left']['name'], '真实子项目A')
        self.assertEqual(scan_res['links'][0]['right']['name'], '人工锁定规划项目A')

    def test_stage1_api_isolation_and_conflicts_and_agent_gate(self):
        """测试第一阶段禁用业务数据生成与前端扫描 API（返回 403）、冲突管理聚合视图及 Agent 闭环门禁。"""
        # 1. 构造含多项目批次目录的数据集，验证未做 Agent 研判时 find_pending_complex_directories 能检出
        self.put(3, '2024年第三批次/子项目1/材料1.pdf', b'batch-1')
        self.put(3, '2024年第三批次/子项目2/材料2.pdf', b'batch-2')
        mount_dir = self.base / 'mount'
        cat = ProjectCatalog(storage_dir=mount_dir, root=self.root)
        pending = cat.find_pending_complex_directories()
        self.assertTrue(any(p['stage_id'] == 'S3' and p['rel_path'] == '2024年第三批次' for p in pending))
        cat.close()

        # 2. 验证第一阶段禁用的 HTTP 接口返回 403
        for method, path in [
            ('POST', '/api/folder-mount/scan'),
            ('POST', '/api/jobs'),
            ('POST', '/api/current'),
            ('GET', '/api/jobs'),
            ('GET', '/api/datafiles'),
            ('GET', '/api/storage'),
        ]:
            payload, status = self.api(path, {'methods': ['M2']} if method == 'POST' else None)
            self.assertEqual(status, 403, f'{method} {path} should return 403')
            self.assertIn('第一阶段', payload.get('error', ''))

        # 3. 验证冲突管理接口 /api/folder-mount/conflicts 与审核历史记录
        self.put(1, '项目C1/共用附件.pdf', b'shared-attachment')
        self.put(2, '项目C2-甲/共用附件.pdf', b'shared-attachment')
        self.put(2, '项目C2-乙/共用附件.pdf', b'shared-attachment')
        state = dict(status='ready', phase='完成')
        with patch.multiple(server, SOURCE=self.root, BASE=self.base, MOUNT_DIR=mount_dir, MOUNT_STATE=state):
            result, _ = self.scan()
            server.write_json(mount_dir / 'result.json', dict(result, scanned_at='2026-10-26T15:30:00'))
            conflicts_data, status = self.api('/api/folder-mount/conflicts')
            self.assertEqual(status, 200)
            self.assertGreaterEqual(conflicts_data['counts']['shared_attachment'], 2)
            link_item = next(it for it in conflicts_data['items'] if it['item_type'] == 'link')
            # 确认 -> 标记复核 -> 撤销决定，检查历史记录
            for dec_status in ('confirmed', 'review', 'pending'):
                _, s_code = self.api('/api/folder-mount/decision', {
                    'id': link_item['id'],
                    'signature': link_item['signature'],
                    'status': dec_status,
                })
                self.assertEqual(s_code, 200)
            audited_data, _ = self.api('/api/folder-mount/conflicts?conflict_type=audited')
            audited_item = next(it for it in audited_data['items'] if it['id'] == link_item['id'])
            self.assertEqual(audited_item['status'], 'pending')
            self.assertEqual(len(audited_item['confirmation']['history']), 2)


if __name__ == '__main__':
    unittest.main()
