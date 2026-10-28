import hashlib
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / 'scripts'))
from folder_mount import folder_entry
from project_catalog import ProjectCatalog
from source_config import SOURCE, STAGES
from extract_archives import disk_path

DEST = Path(r'F:\项目数据\赣江一张图_整理')
PLAN = BASE / 'work' / '整理复制清单.jsonl'
CATALOG = BASE / 'work' / '整理临时目录库'


def plan():
    CATALOG.mkdir(exist_ok=True)
    with closing(sqlite3.connect(f'file:{(BASE / "work/folder_mount/project_catalog.sqlite").as_posix()}?mode=ro', uri=True)) as src:
        with closing(sqlite3.connect(CATALOG / 'project_catalog.sqlite')) as dst:
            src.backup(dst)
    catalog = ProjectCatalog(storage_dir=CATALOG, root=SOURCE)
    folders = {}
    stats = {sid: {'files': 0, 'bytes': 0, 'unassigned': 0} for sid, _ in STAGES}
    with PLAN.open('w', encoding='utf-8') as output:
        for sid, stage in STAGES:
            stage_root = SOURCE / stage
            def fail(error):
                raise error
            for directory, dirs, files in os.walk(disk_path(stage_root), onerror=fail, followlinks=False):
                base = stage_root / Path(directory).relative_to(disk_path(stage_root))
                for name in dirs:
                    item = base / name
                    if item.is_symlink() or item.is_junction():
                        raise ValueError(f'不支持的链接目录: {item}')
                entry = folder_entry(base / '__node__', str(base / '__node__'), SOURCE, catalog) if base != stage_root else None
                project = Path(entry['folder']) if entry and entry['kind'] != 'ungrouped' else None
                if project and not base.is_relative_to(project):
                    project = None
                if project:
                    ident = entry['business_directory_id']
                    # 所有项目附加稳定标识，避免同名目录被合并或覆盖。
                    label = project.name.rstrip(' .') + ' [' + ident[:8] + ']'
                    folders[ident] = {'stage': stage, 'name': label, 'source': str(project),
                                      'reason': entry['reason'], 'decision_source': entry['decision_source'],
                                      'boundary_review': entry['boundary_review']}
                for name in files:
                    source = base / name
                    if source.is_symlink():
                        raise ValueError(f'不支持的链接文件: {source}')
                    info = disk_path(source).stat()
                    if project:
                        rel = Path(stage) / label / source.relative_to(project)
                    else:
                        rel = Path(stage) / '_待归属资料' / source.relative_to(stage_root)
                        stats[sid]['unassigned'] += 1
                    output.write(json.dumps({'source': str(source), 'target': str(rel), 'size': info.st_size,
                                             'mtime_ns': info.st_mtime_ns}, ensure_ascii=False) + '\n')
                    stats[sid]['files'] += 1
                    stats[sid]['bytes'] += info.st_size
            print(sid, stats[sid], flush=True)
    catalog.close()
    (BASE / 'work' / '整理项目索引.json').write_text(json.dumps({'projects': folders, 'stats': stats}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('project_count', len(folders), flush=True)


def copy():
    if DEST.resolve().is_relative_to(SOURCE.resolve()) or SOURCE.resolve().is_relative_to(DEST.resolve()):
        raise ValueError('源目录与目标目录不能相同或互相包含')
    if DEST.is_symlink() or DEST.is_junction() or not DEST.is_dir():
        raise ValueError('目标必须是已存在的普通目录')
    if any(DEST.iterdir()):
        raise ValueError('目标目录非空，停止以防覆盖')
    total = 0
    start = time.monotonic()
    with PLAN.open(encoding='utf-8') as records:
        for line in records:
            row = json.loads(line)
            source = Path(row['source'])
            relative = Path(row['target'])
            target = DEST / relative
            if (relative.is_absolute() or '..' in relative.parts or
                    not source.resolve().is_relative_to(SOURCE.resolve()) or
                    not target.resolve().is_relative_to(DEST.resolve())):
                raise ValueError('清单路径越界')
            before = disk_path(source).stat()
            if (before.st_size, before.st_mtime_ns) != (row['size'], row['mtime_ns']):
                raise ValueError(f'源文件在清单生成后发生变化: {source}')
            disk_path(target.parent).mkdir(parents=True, exist_ok=True)
            with open(disk_path(source), 'rb') as src, open(disk_path(target), 'xb') as dst:
                source_hash = hashlib.sha256()
                for chunk in iter(lambda: src.read(1024 * 1024), b''):
                    source_hash.update(chunk)
                    dst.write(chunk)
            after = disk_path(source).stat()
            if (after.st_size, after.st_mtime_ns) != (row['size'], row['mtime_ns']):
                raise ValueError(f'复制时源文件发生变化: {source}')
            target_hash = hashlib.sha256()
            with open(disk_path(target), 'rb') as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''):
                    target_hash.update(chunk)
            if target_hash.digest() != source_hash.digest():
                raise ValueError(f'复制校验失败: {target}')
            shutil.copystat(disk_path(source), disk_path(target))
            total += 1
            if total % 500 == 0:
                print('verified', total, 'elapsed', round(time.monotonic() - start), 's', flush=True)
    shutil.copy2(BASE / 'work' / '整理项目索引.json', DEST / '整理项目索引.json')
    shutil.copy2(PLAN, DEST / '整理复制清单.jsonl')
    print('verified_total', total, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='按项目边界生成清单并复制原始资料')
    parser.add_argument('action', choices=['plan', 'copy'])
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--destination', type=Path, default=DEST)
    args = parser.parse_args()
    SOURCE = args.source.resolve()
    DEST = args.destination.absolute()
    {'plan': plan, 'copy': copy}[args.action]()
