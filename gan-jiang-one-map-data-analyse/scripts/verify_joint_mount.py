"""对真实七阶段完整副本验收；产物只写工作区，不把样本当作全量。"""
import argparse
from collections import Counter
from datetime import datetime
from itertools import combinations
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from folder_mount import scan_folders, PreparationError
from extract_archives import disk_path
from inspect_sources import is_office_lock
from organize_independent_documents import organize_independent_documents, should_organize_container
from source_config import STAGES, SOURCE, validate_source_stages


def manifest(root):
    entries = {}
    for _, stage in STAGES:
        def fail(error): raise error
        for base, dirs, names in os.walk(disk_path(root / stage), onerror=fail):
            if any((Path(base) / d).is_symlink() or (Path(base) / d).is_junction() for d in dirs):
                raise ValueError('验收数据源含链接目录')
            for name in names:
                path = Path(base) / name
                if is_office_lock(path): continue
                if path.is_symlink(): raise ValueError('验收数据源含链接文件')
                stat = disk_path(path).stat()
                entries[path.relative_to(disk_path(root)).as_posix()] = [stat.st_size, stat.st_mtime_ns]
    return entries


def verify(source, original, output):
    if source.resolve() == original.resolve(): raise ValueError('必须在完整副本执行，不能修改原始资料')
    for root in (source, original):
        if not validate_source_stages(root)['is_valid']: raise ValueError('七阶段目录不完整')
    output.mkdir(parents=True, exist_ok=True)
    before = manifest(original)
    copied = manifest(source)
    checkpoint = output / 'original-manifest.json'
    if checkpoint.is_file():
        if json.loads(checkpoint.read_text(encoding='utf-8')) != before: raise ValueError('原始资料已改变，请使用新的完整副本')
        for relative, version in before.items():
            path = Path(relative)
            moved = (path.parent / path.stem.strip() / path.name).as_posix()
            if copied.get(relative) != version and not (path.parts[0] == STAGES[1][1] and should_organize_container(path.parent) and copied.get(moved) == version):
                raise ValueError('副本原始材料缺失或改变：' + relative)
    elif copied != before:
        raise ValueError('副本不是七阶段完整资料或版本不一致，请重新复制后验收')
    (output / 'original-manifest.json').write_text(json.dumps(before, ensure_ascii=False), encoding='utf-8')
    started = datetime.now().isoformat(timespec='seconds')
    last = [0, '']

    def progress(value):
        if time.monotonic() - last[0] >= 10 or value['phase'] in ('扫描目录', '建索引', '目录匹配', '完成') and value['phase'] != last[1]:
            last[:] = [time.monotonic(), value['phase']]
            (output / 'progress.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            print(value['phase'], value.get('processed'), '/', value.get('total'), flush=True)

    results = {}; cache_path = output / 'hashes.json'
    cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.is_file() else {}
    for label, methods in (('M2', ['M2']), ('M1', ['M1']), ('M1+M2', ['M1', 'M2'])):
        print('START', label, flush=True)
        try:
            if methods == ['M1']:
                with patch('folder_mount.digest', side_effect=AssertionError('M1 单独执行触发了 M2 哈希')):
                    result, new_cache = scan_folders(source, output / '解压记录.sqlite', cache, progress, methods, output / 'catalog')
            else:
                result, new_cache = scan_folders(source, output / '解压记录.sqlite', cache, progress, methods, output / 'catalog')
        except PreparationError as error:
            (output / 'blocked.json').write_text(json.dumps(error.result, ensure_ascii=False), encoding='utf-8')
            raise
        cache.update(new_cache)
        (output / f'result-{label}.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        (output / 'hashes.json').write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        results[label] = result
        print('DONE', label, len(result['links']), flush=True)
    m2, m1, joint = (results[m] for m in ('M2', 'M1', 'M1+M2'))
    pairs = {m: {l['id']: l for l in r['links']} for m, r in results.items()}
    assert pairs['M1+M2'].keys() == pairs['M1'].keys() | pairs['M2'].keys(), '联合候选丢失或新增异常关系'
    assert len(pairs['M1+M2']) == len(joint['links']), '同目录对重复'
    for ident, old in pairs['M2'].items():
        current = pairs['M1+M2'][ident]
        assert current['method_evidence']['M2'] == old['evidence'], 'M2 原始证据被改写'
        assert current['method_signatures']['M2'] == old['signature'], 'M2 原始签名被改写'
    assert all(l['status'] != 'confirmed' for l in joint['links']), '候选被自动确认'
    assert manifest(original) == before, '原始资料发生变化'
    folders = [f for f in joint['folders'] if f['kind'] != 'ungrouped']
    linked = {f['id'] for l in joint['links'] for f in (l['left'], l['right'])}
    report = dict(started_at=started, finished_at=datetime.now().isoformat(timespec='seconds'),
        scope='真实七阶段完整副本全量扫描；默认原生文字提取，OCR关闭，未处理单列', original_root=str(original), source_root=str(source),
        original_files=len(before), original_bytes=sum(v[0] for v in before.values()), original_unchanged=True,
        manifest_scope='完整七阶段文件；排除会被 Office 自动创建/删除且扫描器本就忽略的 ~$ 锁文件',
        projects_by_stage={sid: sum(f['stage'] == sid for f in folders) for sid, _ in STAGES},
        m2_standalone_candidates=len(m2['links']), m1_standalone_candidates=len(m1['links']),
        m2_only=len(pairs['M2'].keys() - pairs['M1'].keys()), m1_only=len(pairs['M1'].keys() - pairs['M2'].keys()),
        dual_hits=len(pairs['M1'].keys() & pairs['M2'].keys()), m1_added=len(pairs['M1'].keys() - pairs['M2'].keys()),
        combined_candidates=len(joint['links']), conflicts=sum(bool(l.get('conflict')) for l in joint['links']),
        m1_validation=dict(Counter(l['m1_validation']['state'] for l in joint['links'])),
        unlinked=len(folders) - len(linked), unlinked_by_stage={sid: sum(f['stage'] == sid and f['id'] not in linked for f in folders) for sid, _ in STAGES},
        errors_by_reason=dict(Counter(e['category'] + ':' + e.get('reason', e.get('state', e['error'].split('：')[0])) for e in joint['errors'])),
        error_records=len(joint['errors']), affected_materials=len({e['path'] for e in joint['errors']}),
        ungrouped_materials=sum(m['kind'] == 'ungrouped' for m in joint['materials']),
        m1_statistics=joint['m1_statistics'], skip_statistics=joint['skip_statistics'], stages=joint['stages'],
        all_21_pairs={a + '-' + b: sum(l['left']['stage'] == a and l['right']['stage'] == b for l in joint['links']) for a, b in combinations([s for s, _ in STAGES], 2)},
        checks=dict(union_preserved=True, m2_evidence_preserved=True, m2_signature_preserved=True, no_duplicates=True, no_auto_confirmation=True, m1_no_hashing=True))
    (output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--original', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.source, args.original, args.output)
