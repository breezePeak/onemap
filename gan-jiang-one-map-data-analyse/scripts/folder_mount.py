"""七阶段材料准备、大目录存库识别和任意跨阶段 M2 精确关联。"""
from collections import defaultdict, deque
from pathlib import Path
import hashlib
import json
import os
import re

from extract_archives import ArchiveRecords, disk_path, file_version
from inspect_sources import extracted_files, is_office_lock
from link_stages import digest
from source_config import STAGES, EXCLUDED_DIRECTORIES
from project_catalog import ProjectCatalog, normalize_dir_name

RULE_VERSION = 'M2-project-seven-stage-catalog-v1'
ARCHIVES = {'.zip', '.rar', '.7z'}


class PreparationError(ValueError):
    def __init__(self, result, cache=None):
        super().__init__('材料准备失败，未执行目录匹配；请查看异常并重试')
        self.result = result
        self.cache = cache or {}


def validate_methods(methods):
    if not isinstance(methods, list) or methods != ['M2']:
        raise ValueError('本轮必须且只能选择 M2 相同附件材料精确关联')


def identifier(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def directory_name(value):
    return normalize_dir_name(value)


def folder_entry(path, source, root, catalog=None):
    root = Path(root); path = Path(path)
    relative = source.replace('\\', '/')[len(root.as_posix()) + 1:]
    parts = [p for p in re.split(r'[/!]', relative) if p]
    stage = next((sid for sid, name in STAGES if parts and name == parts[0]), None)
    if not stage:
        return None

    # 计算相对阶段根目录的相对路径
    sub_parts = parts[1:] if source.endswith('!') else parts[1:]
    rel_stage_path = '/'.join(sub_parts)
    is_dir = path.name == '__node__' or (path.is_dir() if path.exists() else False)

    # 提取材料直接所在的小目录（命中小目录）
    if is_dir:
        small_dir = '/'.join(sub_parts)
    else:
        small_dir = '/'.join(sub_parts[:-1]) if len(sub_parts) > 1 else (sub_parts[0] if sub_parts else '')

    # 使用项目大目录仓储引擎（大模型分析/存库规则）识别其所属项目大目录
    if catalog is None:
        catalog = ProjectCatalog(root=root)
    proj_info = catalog.register_or_find(stage, rel_stage_path, is_dir=is_dir)

    is_archive_node = path.suffix.lower() in ARCHIVES or source.endswith('!') or '!' in source
    if proj_info:
        ident = proj_info['id']
        name = proj_info['project_name']
        is_archive_proj = any(part.lower().endswith(('.zip', '.rar', '.7z', '.解压')) for part in rel_stage_path.split('/')) or is_archive_node
        kind = 'archive' if is_archive_proj and (len(sub_parts) <= 2 or is_archive_node) else 'project'
        folder = proj_info['physical_path']
        reason = proj_info['reason']
        confidence = proj_info['confidence']
    else:
        ident = identifier(f'{stage}:ungrouped')
        name = '未识别大目录材料'
        kind = 'archive' if (is_archive_node and len(sub_parts) <= 1) else 'ungrouped'
        folder = str(root / STAGES[[s[0] for s in STAGES].index(stage)][1])
        reason = '未识别到项目大目录，需人工复核'
        confidence = 'low'

    return dict(
        path=str(path),
        source=source,
        folder=str(folder),
        stage=stage,
        business_directory_id=ident,
        name=name,
        kind=kind,
        paths=[str(folder)],
        small_dir=small_dir,
        reason=reason,
        confidence=confidence,
    )


def material_detail(entry):
    return {key: entry[key] for key in ('id', 'logical_path', 'physical_path', 'archive_chain', 'validation', 'small_dir')}


def scan_folders(root, database, cache, progress, methods=None, storage_dir=None):
    validate_methods(['M2'] if methods is None else methods)
    root = Path(root).resolve(); database = Path(database)
    if not root.is_dir():
        raise ValueError('数据源目录不存在或不可访问。')

    catalog = ProjectCatalog(storage_dir=storage_dir, root=root)
    entries = []; errors = []; folders = {}; summaries = []; queue = deque(); processed = set(); active = set(); archive_versions = {}
    records = ArchiveRecords(database.parent, root=root)

    def report(phase, archive='', member='', done=0, total=0):
        current_sid = summary['stage_id'] if 'summary' in locals() else 'S1'
        progress(dict(phase=phase, current_stage=current_sid, current_archive=archive, current_member=member, processed=done, total=total, stages=summaries, errors=list(errors)))

    def add_folder(entry):
        if entry is None:
            return
        ident = entry['business_directory_id']
        folder = folders.setdefault(ident, dict(
            id=ident,
            business_directory_id=ident,
            folder=entry['folder'],
            stage=entry['stage'],
            name=entry['name'],
            kind=entry['kind'],
            reason=entry.get('reason', ''),
            confidence=entry.get('confidence', 'medium'),
            files=0,
            paths=[]
        ))
        folder['paths'] = sorted(set(folder['paths'] + entry['paths']))
        directories = [p for p in folder['paths'] if Path(p).is_dir()]
        if directories:
            folder.update(folder=directories[0], paths=directories)

    def error(path, value, summary):
        errors.append(dict(path=str(path), error=str(value), stage=summary['stage_id']))
        summary['failed'] += 1

    try:
        # 第一步：扫描 7 个阶段目录
        for stage_id, name in STAGES:
            summary = dict(stage_id=stage_id, name=name, business_directories=0, archives=0, reused=0, extracted=0, failed=0, valid_materials=0)
            summaries.append(summary)
            branch = root / name
            report('扫描目录')
            if not branch.is_dir() or branch.is_symlink() or branch.is_junction() or not branch.resolve().is_relative_to(root):
                error(branch, '阶段目录不存在、不可访问或为链接', summary)
                continue

            def walk_error(value):
                error(value.filename, value, summary)

            for directory, dirs, names in os.walk(branch, onerror=walk_error, followlinks=False):
                dirs.sort()
                base = Path(directory)
                # 排除非业务目录或已有解压目录直接遍历
                for child in dirs[:]:
                    path = base / child
                    if child in EXCLUDED_DIRECTORIES:
                        dirs.remove(child)
                    elif path.is_symlink() or path.is_junction():
                        error(path, '跳过链接目录，无法保证材料完整', summary)
                        dirs.remove(child)
                    elif child.endswith('.解压'):
                        dirs.remove(child)
                    else:
                        entry = folder_entry(path / '__node__', str(path / '__node__'), root, catalog)
                        add_folder(entry)

                for filename in sorted(names):
                    path = base / filename
                    if is_office_lock(path):
                        continue
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        error(path, '文件链接超出安全扫描范围', summary)
                        continue
                    entry = folder_entry(path, str(path), root, catalog)
                    if path.suffix.lower() in ARCHIVES:
                        # 压缩包独立入队，就地解压并追踪
                        entry = folder_entry(path, str(path) + '!', root, catalog)
                        add_folder(entry)
                        queue.append((path, summary, str(path), 0))
                    else:
                        add_folder(entry)
                        entries.append(entry)

            summary['business_directories'] = sum(f['stage'] == stage_id and f['kind'] != 'ungrouped' for f in folders.values())

        # 第二步：就地增量解压与包成员校验
        while queue:
            archive, summary, source, depth = queue.popleft()
            key = str(archive.resolve())
            if key in processed:
                continue
            processed.add(key)
            summary['archives'] += 1
            try:
                if depth > 32:
                    raise ValueError('嵌套压缩包超过32层，请人工核对')
                archive_versions[key] = file_version(archive)
                previous = {field: summary[field] for field in ('reused', 'extracted', 'failed')}

                def archive_progress(phase, arc, member, counts, total):
                    for field in previous:
                        summary[field] = previous[field] + counts[field]
                    report(phase, source, member, sum(counts.values()), total)

                counts, nested = records.extract(archive, progress=archive_progress)
                for field in previous:
                    summary[field] = previous[field] + counts[field]
                errors.extend(dict(item, stage=summary['stage_id']) for item in records.errors)

                for member in records.members(archive):
                    if member['directory']:
                        continue
                    row = records.db.execute("SELECT dest FROM files WHERE archive=? AND raw=? AND status='done'", (key, member['raw'])).fetchone()
                    if not row:
                        continue
                    active.add((key, member['raw']))
                    path = Path(row[0])
                    if path in nested:
                        queue.append((path, summary, source + '!' + member['name'], depth + 1))
                report('校验', source)
            except Exception as value:
                error(source, value, summary)

        # 第三步：统一 SQLite 覆盖实体路径，防止解压文件与原包重复计数
        indexed = extracted_files(database, root, active=active)
        by_path = {str(Path(e['path']).resolve()): e for e in entries}
        indexed_entries = {}
        for record in indexed:
            if Path(record['path']).suffix.lower() in ARCHIVES or is_office_lock(record['path']):
                continue
            entry = folder_entry(Path(record['path']), record['source'], root, catalog)
            if entry:
                add_folder(entry)
                by_path.pop(str(Path(record['path']).resolve()), None)
                indexed_entries[entry['source']] = entry

        entries = sorted([*by_path.values(), *indexed_entries.values()], key=lambda e: (e['stage'], e['source'], e['path']))
        updated = {}; materials = []
        verified_cache = {dest: dict(version=json.loads(version)[0], sha256=sha)
                          for dest, version, sha in records.db.execute('SELECT dest,version,sha256 FROM verified')}

        # 第四步：计算有效材料 SHA-256 并归属于项目大目录
        report('建索引', done=0, total=len(entries))
        for number, entry in enumerate(entries, 1):
            path = Path(entry['path'])
            summary = next(s for s in summaries if s['stage_id'] == entry['stage'])
            try:
                if not path.resolve().is_relative_to(root):
                    raise ValueError('实际材料路径超出数据根目录')
                version = file_version(path)
                folders[entry['business_directory_id']]['files'] += 1
                if not version[0] or path.suffix.lower() in ('.prj', '.cpg', '.tmp') or path.name.startswith('.onemap-'):
                    continue
                fingerprint = next((saved['sha256'] for saved in (updated.get(str(path), {}), cache.get(str(path), {}), verified_cache.get(str(path), {})) if saved.get('version') == version), None)
                if fingerprint is None:
                    fingerprint = digest(disk_path(path))
                if file_version(path) != version:
                    raise OSError('扫描时文件发生变化，请重试')
                updated[str(path)] = dict(version=version, sha256=fingerprint)
                entry.update(
                    id=identifier(entry['stage'] + ':' + entry['source']),
                    stage_id=entry['stage'],
                    logical_path=entry['source'],
                    physical_path=str(path),
                    archive_chain=entry['source'].split('!')[:-1],
                    size=version[0],
                    version=version,
                    sha256=fingerprint,
                    validation='verified'
                )
                materials.append(entry)
                summary['valid_materials'] += 1
            except (OSError, ValueError) as value:
                error(path, value, summary)
            if number % 100 == 0:
                report('建索引', done=number, total=len(entries))

        # 持久化保存已研判/识别的项目大目录仓储
        catalog.save()

        # 归并历史同名解压目录后移除不再拥有材料的重复节点
        used = {e['business_directory_id'] for e in entries}
        for ident, folder in list(folders.items()):
            physical = Path(folder['folder'])
            if ident not in used and any(str(physical) == str(p.with_suffix('')) for p in map(Path, processed)):
                del folders[ident]

        for summary in summaries:
            summary['business_directories'] = sum(f['stage'] == summary['stage_id'] and f['kind'] != 'ungrouped' for f in folders.values())

        result = dict(
            folders=list(folders.values()),
            links=[],
            errors=errors,
            stages=summaries,
            materials=materials,
            file_count=len(entries),
            hashed_count=len(materials),
            rule_version=RULE_VERSION
        )

        for path, version in [(e['path'], e['version']) for e in materials] + list(archive_versions.items()):
            try:
                if file_version(path) != version:
                    raise OSError('本轮准备期间文件版本发生变化，请重试')
            except OSError as value:
                errors.append(dict(path=path, error=str(value)))

        if any(e.get('error') != '加密成员，未提供密码' for e in errors):
            raise PreparationError(result, updated)

        # 第五步：M2 相同附件精确匹配与项目大目录聚合挂接（支持 7 阶段任意两两匹配，最多21组）
        report('目录匹配', done=len(materials), total=len(materials))
        hashes = defaultdict(lambda: defaultdict(list))
        for entry in materials:
            if entry['kind'] != 'ungrouped':
                hashes[entry['sha256']][entry['stage']].append(entry)

        all_stage_ids = [sid for sid, _ in STAGES]
        links = {}

        for fingerprint, stages_map in hashes.items():
            # 获取拥有该哈希附件的所有阶段
            present = [sid for sid, _ in STAGES if stages_map[sid]]
            if len(present) < 2:
                continue

            # 跨阶段比较任意两个不同阶段（最多 21 组）
            for i, left_stage in enumerate(present):
                for right_stage in present[i + 1:]:
                    lefts = defaultdict(list)
                    rights = defaultdict(list)
                    for e in stages_map[left_stage]:
                        lefts[e['business_directory_id']].append(e)
                    for e in stages_map[right_stage]:
                        rights[e['business_directory_id']].append(e)

                    l_idx = all_stage_ids.index(left_stage)
                    r_idx = all_stage_ids.index(right_stage)
                    skipped = all_stage_ids[l_idx + 1:r_idx]

                    for left_id, ls in lefts.items():
                        for right_id, rs in rights.items():
                            ident = identifier(left_id + ':' + right_id)
                            link = links.setdefault(ident, dict(
                                id=ident,
                                left=folders[left_id],
                                right=folders[right_id],
                                method='M2',
                                rule_version=RULE_VERSION,
                                skipped_stages=skipped,
                                evidence=[],
                                ambiguous=False,
                                status='pending'
                            ))
                            link['ambiguous'] |= len(rights) > 1 or len(lefts) > 1
                            link['evidence'].append(dict(
                                sha256=fingerprint,
                                left=sorted(e['source'] for e in ls),
                                right=sorted(e['source'] for e in rs),
                                left_small_dirs=sorted(set(e.get('small_dir', '') for e in ls)),
                                right_small_dirs=sorted(set(e.get('small_dir', '') for e in rs)),
                                left_materials=[e['id'] for e in ls],
                                right_materials=[e['id'] for e in rs],
                                left_details=[material_detail(e) for e in ls],
                                right_details=[material_detail(e) for e in rs],
                            ))

        for link in links.values():
            link['evidence'].sort(key=lambda e: e['sha256'])
            identities = []
            for folder in (link['left'], link['right']):
                inode = Path(folder['folder']).stat().st_ino if Path(folder['folder']).exists() else 0
                identities.append([folder['id'], inode])
            link['signature'] = hashlib.sha256(json.dumps([RULE_VERSION, identities, link['evidence']], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            link['evidence_version'] = link['signature']
            link['evidence_count'] = len(link['evidence'])

        result['links'] = sorted(links.values(), key=lambda e: (e['left']['stage'], e['left']['folder'], e['right']['stage'], e['right']['folder']))
        report('完成', done=len(materials), total=len(materials))
        return result, updated
    finally:
        records.db.close()
        if 'catalog' in locals() and catalog:
            catalog.close()



def project_rows(result):
    """大目录行组织：仅基于逐条确认的直接关联进行组合视图展示，保留直接证据。"""
    stage_set = {sid for sid, _ in STAGES}
    folders = {f['id']: f for f in result['folders'] if f.get('kind') != 'ungrouped' and f['stage'] in stage_set}
    parent = {key: key for key in folders}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    links = [link for link in result['links'] if link['left']['id'] in folders and link['right']['id'] in folders]
    for link in links:
        if link.get('status') == 'confirmed':
            parent[find(link['right']['id'])] = find(link['left']['id'])

    groups = defaultdict(list)
    for key, folder in folders.items():
        groups[find(key)].append(folder)

    outgoing = defaultdict(set); incoming = set()
    for link in links:
        left = find(link['left']['id']); right = find(link['right']['id'])
        if left != right and link.get('status') != 'rejected':
            outgoing[left].add(right)
            incoming.add(right)

    ordering = lambda key: min((f['stage'], f['name'], f['folder']) for f in groups[key])
    roots = sorted((key for key in groups if key not in incoming), key=ordering)
    covered = set(); rows = []

    for root in roots + sorted(groups, key=ordering):
        if root in covered:
            continue
        reached = set(); queue = [root]
        while queue:
            key = queue.pop()
            if key in reached:
                continue
            reached.add(key)
            queue.extend(outgoing[key])
        covered.update(reached)
        stages = {sid: [] for sid, _ in STAGES}
        for key in sorted(reached, key=ordering):
            for folder in sorted(groups[key], key=lambda f: (f['stage'], f['name'], f['folder'])):
                stages[folder['stage']].append(dict(folder, candidate=key != root))

        evidence = [link for link in links if find(link['left']['id']) in reached and find(link['right']['id']) in reached and link.get('status') != 'rejected']
        rejected = [link for link in links if find(link['left']['id']) == root and link.get('status') == 'rejected']
        statuses = {link.get('status', 'pending') for link in evidence}
        status = 'review' if 'review' in statuses else 'pending' if 'pending' in statuses else 'confirmed' if evidence else 'rejected' if rejected else 'unlinked'
        if result.get('inventory_only'):
            status = 'unscanned'
        anchor = min(groups[root], key=lambda f: (f['stage'], f['name']))
        rows.append(dict(
            id=identifier('|'.join(sorted(f['id'] for f in groups[root]))),
            name=anchor['name'],
            stages=stages,
            status=status,
            ambiguous=any(len(values) > 1 for values in stages.values()) or any(e.get('ambiguous') for e in evidence),
            links=[{k: v for k, v in link.items() if k != 'evidence'} for link in evidence + rejected],
            evidence_count=sum(link['evidence_count'] for link in evidence)
        ))
    return rows
