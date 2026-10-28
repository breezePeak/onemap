"""七阶段材料准备、大目录存库识别和任意跨阶段 M2 精确关联。"""
from collections import defaultdict, deque
from pathlib import Path
import hashlib
import json
import os
import re
import zipfile

from extract_archives import ArchiveRecords, disk_path, file_version
from inspect_sources import extracted_files, is_office_lock
from link_stages import digest
from source_config import STAGES, EXCLUDED_DIRECTORIES
from project_catalog import ProjectCatalog, normalize_dir_name
from directory_mount_store import save_directory_mounts
from project_codes import build_code_index, RULE_VERSION as M1_RULE_VERSION

RULE_VERSION = 'M2-project-seven-stage-catalog-v3-project-boundaries'
ARCHIVES = {'.zip', '.rar', '.7z'}


class PreparationError(ValueError):
    def __init__(self, result, cache=None):
        super().__init__('材料准备失败，未执行目录匹配；请查看异常并重试')
        self.result = result
        self.cache = cache or {}


def validate_methods(methods):
    if not isinstance(methods, list) or not methods or any(m not in ('M1', 'M2') for m in methods) or len(set(methods)) != len(methods):
        raise ValueError('请选择 M1、M2 或两种方法联合执行')


def preparation_issue(path, value, stage):
    message = str(value)
    # 只放行已知单文件损坏或加密；路径、链接、并发变化、工具缺失等仍阻断。
    skippable = isinstance(value, zipfile.BadZipFile) or message == '加密成员，未提供密码' or message.startswith((
        '压缩包读取失败（损坏、加密或格式不支持）', '成员解压/校验失败：', 'CRC 校验失败', '成员大小校验失败'))
    return dict(path=str(path), error=message, stage=stage, category='unreadable_archive' if skippable else 'preparation_safety', blocking=not skippable)


def identifier(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def directory_name(value):
    return normalize_dir_name(value)


def folder_entry(path, source, root, catalog=None):
    root = Path(root); path = Path(path)
    def without_archive_wrappers(parts):
        # 包内同名根目录属于同一层，避免“出让.zip!出让”绕过已存库边界。
        return [part for i, part in enumerate(parts) if not (
            i + 1 < len(parts) and part.lower().endswith(('.zip', '.rar', '.7z', '.解压'))
            and normalize_dir_name(part) == normalize_dir_name(parts[i + 1]))]

    relative = source.replace('\\', '/')[len(root.as_posix()) + 1:]
    parts = without_archive_wrappers([p for p in re.split(r'[/!]', relative) if p])
    stage = next((sid for sid, name in STAGES if parts and name == parts[0]), None)
    if not stage:
        return None

    # 计算相对阶段根目录的相对路径
    sub_parts = parts[1:] if source.endswith('!') else parts[1:]
    if path.name == '__node__' and sub_parts and sub_parts[-1] == '__node__':
        sub_parts = sub_parts[:-1]
    rel_stage_path = '/'.join(sub_parts)
    is_dir = path.name == '__node__' or (path.is_dir() if path.exists() else False)

    # 提取材料直接所在的小目录（命中小目录）
    if is_dir:
        small_dir = '/'.join(sub_parts)
    else:
        small_dir = '/'.join(sub_parts[:-1]) if len(sub_parts) > 1 else (sub_parts[0] if sub_parts else '')

    # 使用项目大目录仓储引擎（Agent 存库决策 / 树结构感知研判）识别其所属项目大目录
    if catalog is None:
        catalog = ProjectCatalog(root=root)
    stage_root = root / dict(STAGES)[stage]
    physical_parts = path.relative_to(stage_root).parts if path.is_relative_to(stage_root) else ()
    phys_dir_parts = without_archive_wrappers(physical_parts if is_dir else physical_parts[:-1])
    # 复用原地已有成员时，实体目录边界优先，避免外包名重复造成同目录双重登记。
    proj_info = catalog.find_in_database(stage, phys_dir_parts)
    if not proj_info and phys_dir_parts and '!' in source and not any(p.endswith('.解压') for p in phys_dir_parts):
        proj_info = catalog.register_or_find(stage, '/'.join(phys_dir_parts), is_dir=True)
    if not proj_info:
        proj_info = catalog.register_or_find(stage, rel_stage_path, is_dir=is_dir)

    is_archive_node = path.suffix.lower() in ARCHIVES or source.endswith('!') or '!' in source
    if proj_info:
        ident = proj_info['id']
        name = proj_info['project_name']
        is_archive_proj = any(part.lower().endswith(('.zip', '.rar', '.7z', '.解压')) for part in rel_stage_path.split('/')) or is_archive_node
        kind = 'archive' if is_archive_proj and (len(sub_parts) <= 2 or is_archive_node) else 'project'
        folder = proj_info['physical_path']
        # 压缩包逻辑路径不等于解压实体路径，沿实际材料父目录定位项目边界。
        if not Path(folder).is_dir():
            stage_root = root / dict(STAGES)[stage]
            for parent in (path, *path.parents):
                if parent == stage_root or not parent.is_relative_to(stage_root):
                    break
                if parent.is_dir() and normalize_dir_name(parent.name) == normalize_dir_name(proj_info['project_root'].split('/')[-1]):
                    folder = str(parent)
                    proj_info['physical_path'] = folder
                    catalog.upsert_db(proj_info)
                    break
        reason = proj_info['reason']
        confidence = proj_info['confidence']
        decision_source = proj_info.get('decision_source', 'rule')
        boundary_review = bool(proj_info.get('boundary_review', False))
    else:
        ident = identifier(f'{stage}:ungrouped')
        name = '未识别大目录材料'
        kind = 'ungrouped'
        folder = str(root / STAGES[[s[0] for s in STAGES].index(stage)][1])
        reason = '未识别到项目大目录，需人工复核'
        confidence = 'low'
        decision_source = 'unresolved'
        boundary_review = True

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
        decision_source=decision_source,
        boundary_review=boundary_review,
    )


def material_detail(entry):
    return {key: entry[key] for key in ('id', 'logical_path', 'physical_path', 'archive_chain', 'validation', 'small_dir')}


def scan_folders(root, database, cache, progress, methods=None, storage_dir=None, tolerate_corrupted=False, organized=False):
    methods = ['M2'] if methods is None else methods
    validate_methods(methods)
    root = Path(root).resolve(); database = Path(database)
    if not root.is_dir():
        raise ValueError('数据源目录不存在或不可访问。')

    catalog = ProjectCatalog(storage_dir=storage_dir, root=root)
    entries = []; errors = []; folders = {}; summaries = []; queue = deque(); processed = set(); active = set(); archive_versions = {}
    records = ArchiveRecords(database.parent, root=root)
    summary = None
    organized_index = {}
    if organized and (root / '整理项目索引.json').is_file():
        index = json.loads((root / '整理项目索引.json').read_text(encoding='utf-8'))
        organized_index = {(p['stage'], p['name']): p for p in index.get('projects', {}).values()}

    def organized_entry(path, stage_id, stage_name):
        parts = path.relative_to(root / stage_name).parts
        project = root / stage_name / parts[0]
        ident = identifier(stage_id + ':' + parts[0])
        if ident not in catalog.projects:
            info = organized_index.get((stage_name, parts[0]), {})
            review = bool(info.get('boundary_review'))
            proj = dict(id=ident, stage_id=stage_id, stage_name=stage_name, project_root=parts[0],
                        project_name=parts[0], physical_path=str(project), depth=1,
                        reason='整理目录中的项目文件夹', confidence='high', status='review' if review else 'confirmed', files=0,
                        updated_at='', node_type='project', decision_source='organized', evidence_paths=[],
                        structure_version='', boundary_review=review,
                        boundary_review_reason=info.get('reason', '') if review else '')
            catalog.projects[ident] = proj
            catalog.upsert_db(proj)
        return dict(path=str(path), source=str(path), folder=str(project), stage=stage_id,
                    business_directory_id=ident, name=parts[0], kind='project', paths=[str(project)],
                    small_dir='/'.join(parts[:-1]), reason='整理目录中的项目文件夹', confidence='high',
                    decision_source='organized', boundary_review=bool(catalog.projects[ident].get('boundary_review')))

    def report(phase, archive='', member='', done=0, total=0, stage=None):
        current_sid = stage or (summary['stage_id'] if summary else '')
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
            decision_source=entry.get('decision_source', 'rule'),
            boundary_review=bool(entry.get('boundary_review', False)),
            files=0,
            paths=[]
        ))
        folder['boundary_review'] = bool(folder.get('boundary_review')) or bool(entry.get('boundary_review'))
        folder['paths'] = sorted(set(folder['paths'] + entry['paths']))
        directories = [p for p in folder['paths'] if Path(p).is_dir()]
        if directories:
            folder.update(folder=directories[0], paths=directories)

    def error(path, value, summary):
        errors.append(preparation_issue(path, value, summary['stage_id']))
        summary['failed'] += 1

    try:
        if not organized:
            catalog.ensure_agent_decisions_applied()
        # 第一步：扫描 7 个阶段目录
        for stage_id, name in STAGES:
            summary = dict(stage_id=stage_id, name=name, business_directories=0, archives=0, reused=0, extracted=0, failed=0, valid_materials=0, skipped={})
            summaries.append(summary)
            branch = root / name
            report('扫描目录')
            if not branch.is_dir() or branch.is_symlink() or branch.is_junction() or not branch.resolve().is_relative_to(root):
                error(branch, '阶段目录不存在、不可访问或为链接', summary)
                continue

            def walk_error(value):
                error(value.filename, value, summary)

            for directory, dirs, names in os.walk(disk_path(branch), onerror=walk_error, followlinks=False):
                dirs.sort()
                base = branch / Path(directory).relative_to(disk_path(branch))
                # 排除非业务目录或已有解压目录直接遍历
                for child in dirs[:]:
                    path = base / child
                    if organized and base == branch and child == '_待归属资料':
                        dirs.remove(child)
                    elif child in EXCLUDED_DIRECTORIES and not organized:
                        dirs.remove(child)
                    elif disk_path(path).is_symlink() or disk_path(path).is_junction():
                        error(path, '跳过链接目录，无法保证材料完整', summary)
                        dirs.remove(child)
                    elif child.endswith('.解压') and not organized:
                        dirs.remove(child)
                    else:
                        entry = organized_entry(path / '__node__', stage_id, name) if organized else folder_entry(path / '__node__', str(path / '__node__'), root, catalog)
                        add_folder(entry)

                for filename in sorted(names):
                    path = base / filename
                    if organized and base == branch:
                        continue
                    if is_office_lock(path):
                        continue
                    if disk_path(path).is_symlink() or not path.resolve().is_relative_to(root):
                        error(path, '文件链接超出安全扫描范围', summary)
                        continue
                    if path.suffix.lower() in ARCHIVES and not organized:
                        # 压缩包独立入队，先就地解压，待内部目录结构清晰后再判定边界
                        queue.append((path, summary, str(path), 0))
                    else:
                        entry = organized_entry(path, stage_id, name) if organized else folder_entry(path, str(path), root, catalog)
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
                errors.extend(preparation_issue(item['path'], ValueError(item['error']), summary['stage_id']) for item in records.errors)

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
        indexed = [] if organized else extracted_files(database, root, active=active)
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
        report('建索引', done=0, total=len(entries), stage=entries[0]['stage'] if entries else None)
        for number, entry in enumerate(entries, 1):
            path = Path(entry['path'])
            summary = next(s for s in summaries if s['stage_id'] == entry['stage'])
            try:
                if not path.resolve().is_relative_to(root):
                    raise ValueError('实际材料路径超出数据根目录')
                version = file_version(path)
                folders[entry['business_directory_id']]['files'] += 1
                if not version[0] or path.suffix.lower() in ('.prj', '.cpg', '.tmp') or path.name.startswith('.onemap-'):
                    reason = 'empty_file' if not version[0] else 'sidecar_or_temporary'
                    summary['skipped'][reason] = summary['skipped'].get(reason, 0) + 1
                    continue
                fingerprint = next((saved.get('sha256') for saved in (updated.get(str(path), {}), cache.get(str(path), {}), verified_cache.get(str(path), {})) if saved.get('version') == version and saved.get('sha256')), None)
                if fingerprint is None and 'M2' in methods:
                    fingerprint = digest(disk_path(path))
                if file_version(path) != version:
                    raise OSError('扫描时文件发生变化，请重试')
                if fingerprint: updated[str(path)] = dict(version=version, sha256=fingerprint)
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
            hashed_count=sum(bool(e['sha256']) for e in materials),
            methods=methods,
            rule_version=RULE_VERSION
        )

        blocking_errors = [e for e in errors if e['blocking']]
        if blocking_errors:
            raise PreparationError(result, updated)

        # 第五步：M2 相同附件精确匹配与项目大目录聚合挂接（支持 7 阶段任意两两匹配，最多21组）
        report('目录匹配', done=len(materials), total=len(materials))
        hashes = defaultdict(lambda: defaultdict(list))
        for entry in materials:
            if 'M2' in methods and entry['kind'] != 'ungrouped':
                hashes[entry['sha256']][entry['stage']].append(entry)

        all_stage_ids = [sid for sid, _ in STAGES]
        links = {}

        for fingerprint, stages_map in hashes.items():
            # 同阶段多项目共用同一附件时，反向触发项目边界复核（不自动合并项目或宗地）
            for sid, stage_entries in stages_map.items():
                dir_ids = sorted({e['business_directory_id'] for e in stage_entries if e['kind'] != 'ungrouped'})
                if len(dir_ids) > 1:
                    for did in dir_ids:
                        catalog.flag_boundary_review(did, f'M2发现同阶段({sid})多个项目目录共用相同附件，需复核项目边界')
                        if did in folders:
                            folders[did]['boundary_review'] = True

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
            link['methods'] = ['M2']
            link['method_signatures'] = {'M2': link['signature']}
            link['method_evidence'] = {'M2': link['evidence']}

        if 'M1' in methods:
            code_index, code_links, stats, code_errors = build_code_index(list(folders.values()), materials, catalog, report)
            result.update(project_code_index=code_index, m1_statistics=stats, m1_rule_version=M1_RULE_VERSION)
            errors.extend(code_errors)
            for candidate in code_links:
                left, right = candidate['left'], candidate['right']
                ident = identifier(left['id'] + ':' + right['id'])
                link = links.setdefault(ident, dict(id=ident, left=left, right=right, method='M1', methods=[],
                    rule_version=RULE_VERSION, skipped_stages=all_stage_ids[all_stage_ids.index(left['stage']) + 1:all_stage_ids.index(right['stage'])],
                    evidence=[], ambiguous=False, status='pending', method_signatures={}, method_evidence={}))
                link['methods'] = sorted(set(link['methods']) | {'M1'})
                link['method'] = '+'.join(link['methods'])
                link['ambiguous'] |= candidate['ambiguous']
                link['conflict'] = candidate['conflict']
                if candidate['conflict']: link['status'] = 'review'
                signature = hashlib.sha256(json.dumps([M1_RULE_VERSION, left, right, candidate['evidence'], candidate['ambiguous']], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                link['method_signatures']['M1'] = signature
                link['method_evidence']['M1'] = candidate['evidence']
                link['evidence'] = candidate['evidence'] + link['evidence']
                link['signature'] = signature if link['methods'] == ['M1'] else hashlib.sha256(json.dumps(link['method_signatures'], sort_keys=True).encode()).hexdigest()
                link['evidence_version'] = link['signature']
                link['evidence_count'] = len(link['evidence'])

        result['skip_statistics'] = dict(materials_seen=len(entries), valid_materials=len(materials),
            hashed_materials=result['hashed_count'], hashes_disabled='M2' not in methods,
            reasons={reason: sum(s['skipped'].get(reason, 0) for s in summaries) for reason in {r for s in summaries for r in s['skipped']}},
            unreadable_archives=sum(e['category'] == 'unreadable_archive' for e in errors))

        code_rows = defaultdict(list)
        for row in result.get('project_code_index', []):
            code_rows[row['directory_id']].append(row)
        for link in links.values():
            if 'M1' in methods:
                sides = [code_rows[f['id']] for f in (link['left'], link['right'])]
                reliable = [{r['normalized_code'] for r in rows if r['valid'] and r['grade'] in ('A', 'B')
                             and r['ownership_status'] == 'current_project'} for rows in sides]
                uncertain = any(r['ownership_status'] != 'current_project' for rows in sides for r in rows)
                conflict = bool(all(reliable) and reliable[0].isdisjoint(reliable[1]))
                validation = ('conflict' if conflict else 'corroborated' if reliable[0] & reliable[1]
                              else 'unresolved_ownership' if uncertain else 'missing_both' if not any(reliable)
                              else 'missing_left' if not reliable[0] else 'missing_right')
                link['m1_validation'] = dict(state=validation, left_codes=sorted(reliable[0]), right_codes=sorted(reliable[1]),
                                             left_sources=sides[0], right_sources=sides[1])
                link['conflict'] = conflict
                if conflict or uncertain or any(r['needs_review'] for rows in sides for r in rows):
                    link['status'] = 'review'
                # 校验结论参与联合签名，原 M2 方法签名和附件证据不变。
                link['signature'] = hashlib.sha256(json.dumps([link['method_signatures'], link['m1_validation']],
                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                link['evidence_version'] = link['signature']
            link['evidence_type'] = 'conflict' if link.get('conflict') else link['method']
            link['review_context'] = identifier(json.dumps([link['ambiguous'], link.get('conflict',False),
                [[f['id'],f['folder'],f.get('reason'),Path(f['folder']).stat().st_ino if Path(f['folder']).exists() else 0,
                  f.get('code_evidence_version'),f.get('boundary_review',False)] for f in (link['left'],link['right'])]],ensure_ascii=False))
        for fid, f_obj in folders.items():
            proj_in_cat = catalog.projects.get(fid)
            if proj_in_cat:
                if proj_in_cat.get('boundary_review'):
                    f_obj['boundary_review'] = True
                if proj_in_cat.get('boundary_review_reason'):
                    f_obj['boundary_review_reason'] = proj_in_cat['boundary_review_reason']
        for m_entry in materials:
            f_obj = folders.get(m_entry['business_directory_id'])
            if f_obj and f_obj.get('boundary_review'):
                m_entry['boundary_review'] = True
        catalog.save()
        result['links'] = sorted(links.values(), key=lambda e: (e['left']['stage'], e['left']['folder'], e['right']['stage'], e['right']['folder']))
        for path, version in [(e['path'], e['version']) for e in materials] + list(archive_versions.items()):
            try:
                if not Path(path).resolve().is_relative_to(root) or file_version(path) != version:
                    raise OSError('本轮准备期间文件路径或版本发生变化，请重试')
            except OSError as value:
                errors.append(preparation_issue(path, value, ''))
        if any(e['blocking'] for e in errors):
            raise PreparationError(result, updated)
        catalog.db.execute('CREATE TABLE IF NOT EXISTS relation_snapshots (id TEXT, signature TEXT, payload TEXT, recorded_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (id, signature))')
        for link in result['links']:
            catalog.db.execute('INSERT OR IGNORE INTO relation_snapshots (id,signature,payload) VALUES (?,?,?)',
                (link['id'], link['signature'], json.dumps(link, ensure_ascii=False, sort_keys=True)))
        catalog.db.commit()
        if organized:
            save_directory_mounts(catalog.storage_dir, root, result['links'], scan=result)
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

    ordering = lambda key: min((f['stage'], f['name'], f['folder']) for f in groups[key])
    rows = []
    for root in sorted(groups, key=ordering):
        stages = {sid: [] for sid, _ in STAGES}
        for folder in sorted(groups[root], key=lambda f: (f['stage'], f['name'], f['folder'])):
            stages[folder['stage']].append(dict(folder, candidate=False))

        evidence = [link for link in links if root in (find(link['left']['id']), find(link['right']['id'])) and link.get('status') != 'rejected']
        rejected = [link for link in links if root in (find(link['left']['id']), find(link['right']['id'])) and link.get('status') == 'rejected']
        statuses = {link.get('status', 'pending') for link in evidence}
        status = 'review' if 'review' in statuses else 'pending' if 'pending' in statuses else 'confirmed' if evidence else 'rejected' if rejected else 'unlinked'
        if not evidence and any(f.get('code_review') for f in groups[root]):
            status = 'review'
        if result.get('inventory_only'):
            status = 'unscanned'
        anchor = min(groups[root], key=lambda f: (f['stage'], f['name']))
        rows.append(dict(
            id=identifier('|'.join(sorted(f['id'] for f in groups[root]))),
            name=anchor['name'],
            stages=stages,
            status=status,
            unlinked=not evidence,
            ambiguous=any(len(values) > 1 for values in stages.values()) or any(e.get('ambiguous') for e in evidence),
            links=[{k: v for k, v in link.items() if k not in ('evidence', 'method_evidence')} for link in evidence + rejected],
            evidence_count=sum(link['evidence_count'] for link in evidence)
        ))
    return rows
