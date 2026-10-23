"""独立于字段提取的跨阶段文件夹挂载；相同字节只生成候选。"""
from collections import defaultdict
from itertools import combinations
from pathlib import Path
import hashlib
import os

from inspect_sources import extracted_files, is_office_lock
from link_stages import digest
from extract_archives import disk_path


def folder_entry(path, source, root):
    origin = Path(source.split('!', 1)[0])
    relative = origin.relative_to(root)
    if len(relative.parts) < 2:
        return None
    # shortcut: 以直接包含文件的目录为节点；业务项目目录层级明确后再归并子目录。
    folder = str(path.parent)
    if '!' in source:
        archive, member = source.rsplit('!', 1)
        folder = archive + '!' + (member.rsplit('/', 1)[0] if '/' in member else '')
    return dict(path=str(path), source=source, folder=folder, stage=relative.parts[0])


def scan_folders(root, database, cache, progress):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('数据源目录不存在或不可访问。')
    entries = []; errors = []; known = set()
    def walk_error(error):
        errors.append(dict(path=error.filename, error=str(error)))
    for directory, dirs, names in os.walk(root, onerror=walk_error, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory)/name).is_symlink() and not (Path(directory)/name).is_junction()]
        for name in names:
            path = Path(directory)/name
            if is_office_lock(path) or path.is_symlink() or path.suffix.lower() in ('.zip', '.rar', '.7z'):
                continue
            entry = folder_entry(path, str(path), root)
            if entry:
                entries.append(entry); known.add(str(path.resolve()))
    for record in extracted_files(database, root):
        path = Path(record['path'])
        if str(path.resolve()) in known or is_office_lock(path) or path.suffix.lower() in ('.zip', '.rar', '.7z'):
            continue
        entry = folder_entry(path, record['source'].replace('\\', '/'), root)
        if entry:
            entries.append(entry)
    folders = {}; sizes = defaultdict(list)
    for entry in entries:
        folder = entry['folder']
        folders.setdefault(folder, dict(folder=folder, stage=entry['stage'], files=0))['files'] += 1
        try:
            stat = disk_path(entry['path']).stat()
            # 坐标系和编码说明可被任意项目共用，不能作为项目关联证据。
            if stat.st_size and Path(entry['source']).suffix.lower() not in ('.prj', '.cpg'):
                entry.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
                sizes[stat.st_size].append(entry)
        except OSError as error:
            errors.append(dict(path=entry['path'], error=str(error)))
    hashes = defaultdict(list); updated = {}; done = 0
    candidates = [e for group in sizes.values() if len({e['stage'] for e in group}) > 1 for e in group]
    for entry in candidates:
        path = Path(entry['path']); key = str(path)
        try:
            saved = cache.get(key, {})
            if saved.get('size') == entry['size'] and saved.get('mtime_ns') == entry['mtime_ns']:
                fingerprint = saved['sha256']
            else:
                fingerprint = digest(disk_path(path))
            stat = disk_path(path).stat()
            if (stat.st_size, stat.st_mtime_ns) != (entry['size'], entry['mtime_ns']):
                raise OSError('扫描时文件发生变化，请重新扫描')
            updated[key] = dict(size=entry['size'], mtime_ns=entry['mtime_ns'], sha256=fingerprint)
            hashes[fingerprint].append(entry)
        except OSError as error:
            errors.append(dict(path=key, error=str(error)))
        done += 1
        if done % 100 == 0:
            progress(done, len(candidates))
    links = {}
    for fingerprint, group in hashes.items():
        by_folder = defaultdict(list)
        for entry in group:
            by_folder[entry['folder']].append(entry)
        for left, right in combinations(sorted(by_folder), 2):
            if folders[left]['stage'] == folders[right]['stage']:
                continue
            ident = hashlib.sha256((left+'\n'+right).encode()).hexdigest()[:24]
            link = links.setdefault(ident, dict(id=ident, left=folders[left], right=folders[right], evidence=[]))
            link['evidence'].append(dict(sha256=fingerprint, left=[e['source'] for e in by_folder[left]], right=[e['source'] for e in by_folder[right]]))
    for link in links.values():
        link['signature'] = hashlib.sha256('\n'.join(sorted(e['sha256'] for e in link['evidence'])).encode()).hexdigest()
    return dict(folders=list(folders.values()), links=list(links.values()), errors=errors, file_count=len(entries), hashed_count=len(candidates)), updated
