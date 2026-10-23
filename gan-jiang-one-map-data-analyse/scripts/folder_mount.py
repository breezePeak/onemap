"""四阶段材料准备、固定快照和相邻业务目录的 M2 候选。"""
from collections import defaultdict, deque
from pathlib import Path
import hashlib
import json
import os
import re

from extract_archives import ArchiveRecords, disk_path, file_version
from inspect_sources import extracted_files, is_office_lock
from link_stages import digest
from source_config import STAGES

RULE_VERSION = 'M2-project-four-stage-gaps-v3'
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
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def directory_name(value):
    value=re.sub(r'\.解压$','',value)
    return re.sub(r'\.(?:zip|rar|7z)$','',value,flags=re.I)


def classification_directory(stage, value):
    name=re.sub(r'[(（]\d+[)）]$','',directory_name(value)).strip()
    # shortcut: 分类层按当前数据目录识别；新增分类命名时补充此处规则。
    common={'出让','划拨','01划拨档案整理','04出让档案整理','用地预审及选址论证',
            '规划红线图','规划条件出具','建设用地涉及的详细规划批复及图则','用地红线','有关部门意见',
            '一码管地报批相关材料汇总提供'}
    return name in common or bool(re.fullmatch(r'\d{4}(?:年度|年)?',name)) or bool(re.fullmatch(r'\d{4}[-—至]+\d{4}.*报批.*',name)) or bool(re.fullmatch(r'赣江新区\d{4}年报批材料',name))


def folder_entry(path, source, root):
    root=Path(root); path=Path(path)
    relative=source.replace('\\','/')[len(root.as_posix())+1:]
    parts=[p for p in re.split(r'[/!]',relative) if p]
    stage=next((sid for sid,name in STAGES if parts and name==parts[0]),None)
    if not stage: return None
    body=parts[1:] if source.endswith('!') else parts[1:-1]
    selected=next((i for i,value in enumerate(body) if value!='__node__' and not classification_directory(stage,value)),None)
    if selected is None:
        if path.name=='__node__': return None
        name='未分组材料'; kind='ungrouped'; folder=root/parts[0]
        key='ungrouped'
    else:
        value=body[selected]; name=directory_name(value); kind='directory'
        # 分类层保留在来源中，业务节点落在真实项目层；包内项目不以附件目录建项。
        prefix=[]
        for token in body[:selected+1]:
            normalized=directory_name(token)
            if not prefix or prefix[-1]!=normalized: prefix.append(normalized)
        if stage in ('S1','S3'): prefix=[name]
        key='/'.join(prefix)
        folder=None
        physical=path.relative_to(root).parts
        for i,part in enumerate(physical[1:-1],1):
            if directory_name(part)==name:
                folder=root.joinpath(*physical[:i+1]); break
        if folder is None:
            archive=Path(source.split('!',1)[0])
            folder=archive.with_name(archive.name+'.解压') if archive.with_name(archive.name+'.解压').is_dir() else archive
            kind='archive'
    ident=identifier(stage+':'+key)
    return dict(path=str(path),source=source,folder=str(folder),stage=stage,
                business_directory_id=ident,name=name,kind=kind,paths=[str(folder)])


def material_detail(entry):
    return {key:entry[key] for key in ('id','logical_path','physical_path','archive_chain','validation')}


def scan_folders(root, database, cache, progress, methods=None):
    validate_methods(['M2'] if methods is None else methods)
    root = Path(root).resolve(); database = Path(database)
    if not root.is_dir(): raise ValueError('数据源目录不存在或不可访问。')
    entries=[]; errors=[]; folders={}; summaries=[]; queue=deque(); processed=set(); active=set(); archive_versions={}
    records = ArchiveRecords(database.parent, root=root)
    def report(phase, archive='', member='', done=0, total=0):
        progress(dict(phase=phase, current_stage=summary['stage_id'], current_archive=archive, current_member=member, processed=done, total=total, stages=summaries, errors=list(errors)))
    def add_folder(entry):
        if entry is None: return
        ident=entry['business_directory_id']
        folder=folders.setdefault(ident, dict(id=ident, business_directory_id=ident, folder=entry['folder'], stage=entry['stage'], name=entry['name'], kind=entry['kind'], files=0,paths=[]))
        folder['paths']=sorted(set(folder['paths']+entry['paths']))
        directories=[p for p in folder['paths'] if Path(p).is_dir()]
        if directories: folder.update(folder=directories[0],paths=directories)
    def error(path, value, summary):
        errors.append(dict(path=str(path),error=str(value),stage=summary['stage_id'])); summary['failed']+=1
    try:
        for stage_id, name in STAGES:
            summary=dict(stage_id=stage_id,name=name,business_directories=0,archives=0,reused=0,extracted=0,failed=0,valid_materials=0)
            summaries.append(summary); branch=root/name
            report('扫描目录')
            if not branch.is_dir() or branch.is_symlink() or branch.is_junction() or not branch.resolve().is_relative_to(root):
                error(branch,'阶段目录不存在、不可访问或为链接',summary); continue
            def walk_error(value): error(value.filename,value,summary)
            for directory, dirs, names in os.walk(branch,onerror=walk_error,followlinks=False):
                dirs.sort()
                base=Path(directory)
                for child in dirs[:]:
                    path=base/child
                    if path.is_symlink() or path.is_junction():
                        error(path,'跳过链接目录，无法保证材料完整',summary); dirs.remove(child)
                    elif child.endswith('.解压'): dirs.remove(child)
                    else:
                        entry=folder_entry(path/'__node__',str(path/'__node__'),root); add_folder(entry)
                for filename in sorted(names):
                    path=base/filename
                    if is_office_lock(path): continue
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        error(path,'文件链接超出安全扫描范围',summary); continue
                    entry=folder_entry(path,str(path),root)
                    if path.suffix.lower() in ARCHIVES:
                        # 阶段根部的包独立成为虚拟业务目录。
                        entry=folder_entry(path,str(path)+'!',root); add_folder(entry)
                        queue.append((path,summary,str(path),0))
                    else: add_folder(entry); entries.append(entry)
            summary['business_directories']=sum(f['stage']==stage_id and f['kind']!='ungrouped' for f in folders.values())
        while queue:
            archive,summary,source,depth=queue.popleft(); key=str(archive.resolve())
            if key in processed: continue
            processed.add(key); summary['archives']+=1
            try:
                if depth>32: raise ValueError('嵌套压缩包超过32层，请人工核对')
                archive_versions[key]=file_version(archive)
                previous={field:summary[field] for field in ('reused','extracted','failed')}
                def archive_progress(phase,arc,member,counts,total):
                    for field in previous: summary[field]=previous[field]+counts[field]
                    report(phase,source,member,sum(counts.values()),total)
                counts,nested=records.extract(archive, progress=archive_progress)
                for field in previous: summary[field]=previous[field]+counts[field]
                errors.extend(dict(item,stage=summary['stage_id']) for item in records.errors)
                for member in records.members(archive):
                    if member['directory']: continue
                    row=records.db.execute("SELECT dest FROM files WHERE archive=? AND raw=? AND status='done'",(key,member['raw'])).fetchone()
                    if not row: continue
                    active.add((key,member['raw']))
                    path=Path(row[0])
                    if path in nested: queue.append((path,summary,source+'!'+member['name'],depth+1))
                report('校验',source)
            except Exception as value: error(source,value,summary)
        # 使用统一 SQLite 来源索引覆盖实体路径，保留包来源链且不重复计数。
        indexed=extracted_files(database,root,active=active)
        by_path={str(Path(e['path']).resolve()):e for e in entries}
        indexed_entries={}
        for record in indexed:
            if Path(record['path']).suffix.lower() in ARCHIVES or is_office_lock(record['path']): continue
            entry=folder_entry(Path(record['path']),record['source'],root)
            if entry:
                add_folder(entry)
                by_path.pop(str(Path(record['path']).resolve()),None)
                indexed_entries[entry['source']]=entry
        entries=sorted([*by_path.values(),*indexed_entries.values()],key=lambda e:(e['stage'],e['source'],e['path'])); updated={}; materials=[]
        verified_cache={dest:dict(version=json.loads(version)[0],sha256=sha)
                        for dest,version,sha in records.db.execute('SELECT dest,version,sha256 FROM verified')}
        report('建索引',done=0,total=len(entries))
        for number,entry in enumerate(entries,1):
            path=Path(entry['path']); summary=next(s for s in summaries if s['stage_id']==entry['stage'])
            try:
                if not path.resolve().is_relative_to(root): raise ValueError('实际材料路径超出数据根目录')
                version=file_version(path); folders[entry['business_directory_id']]['files']+=1
                if not version[0] or path.suffix.lower() in ('.prj','.cpg','.tmp') or path.name.startswith('.onemap-'): continue
                fingerprint=next((saved['sha256'] for saved in (updated.get(str(path),{}),cache.get(str(path),{}),verified_cache.get(str(path),{})) if saved.get('version')==version),None)
                if fingerprint is None: fingerprint=digest(disk_path(path))
                if file_version(path)!=version: raise OSError('扫描时文件发生变化，请重试')
                updated[str(path)]=dict(version=version,sha256=fingerprint)
                entry.update(id=identifier(entry['stage']+':'+entry['source']),stage_id=entry['stage'],logical_path=entry['source'],physical_path=str(path),archive_chain=entry['source'].split('!')[:-1],size=version[0],version=version,sha256=fingerprint,validation='verified')
                materials.append(entry); summary['valid_materials']+=1
            except (OSError,ValueError) as value: error(path,value,summary)
            if number%100==0: report('建索引',done=number,total=len(entries))
        # 归并历史同名解压目录后移除不再拥有材料的重复节点；真正空业务目录仍保留。
        used={e['business_directory_id'] for e in entries}
        for ident,folder in list(folders.items()):
            physical=Path(folder['folder'])
            if ident not in used and any(str(physical)==str(p.with_suffix('')) for p in map(Path,processed)):
                del folders[ident]
        for summary in summaries: summary['business_directories']=sum(f['stage']==summary['stage_id'] and f['kind']!='ungrouped' for f in folders.values())
        result=dict(folders=list(folders.values()),links=[],errors=errors,stages=summaries,materials=materials,file_count=len(entries),hashed_count=len(materials),rule_version=RULE_VERSION)
        for path,version in [(e['path'],e['version']) for e in materials]+list(archive_versions.items()):
            try:
                if file_version(path)!=version: raise OSError('本轮准备期间文件版本发生变化，请重试')
            except OSError as value: errors.append(dict(path=path,error=str(value)))
        if any(e.get('error')!='加密成员，未提供密码' for e in errors): raise PreparationError(result,updated)
        report('目录匹配',done=len(materials),total=len(materials))
        hashes=defaultdict(lambda:defaultdict(list))
        for entry in materials:
            if entry['kind']!='ungrouped': hashes[entry['sha256']][entry['stage']].append(entry)
        links={}
        for fingerprint,stages in hashes.items():
            present=[sid for sid,_ in STAGES if stages[sid]]
            for left_stage,right_stage in zip(present,present[1:]):
                lefts=defaultdict(list); rights=defaultdict(list)
                for e in stages[left_stage]: lefts[e['business_directory_id']].append(e)
                for e in stages[right_stage]: rights[e['business_directory_id']].append(e)
                for left,ls in lefts.items():
                    for right,rs in rights.items():
                        ident=identifier(left+':'+right)
                        link=links.setdefault(ident,dict(id=ident,left=folders[left],right=folders[right],method='M2',rule_version=RULE_VERSION,skipped_stages=[sid for sid,_ in STAGES if left_stage<sid<right_stage],evidence=[],ambiguous=False,status='pending'))
                        link['ambiguous'] |= len(rights)>1 or len(lefts)>1
                        link['evidence'].append(dict(sha256=fingerprint,left=sorted(e['source'] for e in ls),right=sorted(e['source'] for e in rs),left_materials=[e['id'] for e in ls],right_materials=[e['id'] for e in rs],left_details=[material_detail(e) for e in ls],right_details=[material_detail(e) for e in rs]))
        for link in links.values():
            link['evidence'].sort(key=lambda e:e['sha256'])
            identities=[]
            for folder in (link['left'],link['right']):
                identities.append([folder['id'],Path(folder['folder']).stat().st_ino])
            link['signature']=hashlib.sha256(json.dumps([RULE_VERSION,identities,link['evidence']],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            link['evidence_version']=link['signature']; link['evidence_count']=len(link['evidence'])
        result['links']=sorted(links.values(),key=lambda e:(e['left']['stage'],e['left']['folder'],e['right']['folder']))
        report('完成',done=len(materials),total=len(materials))
        return result,updated
    finally: records.db.close()


def project_rows(result):
    """确认关系归并为项目，待确认关系只在四阶段单元格中列为候选。"""
    folders={f['id']:f for f in result['folders'] if f.get('kind')!='ungrouped' and f['stage'] in {sid for sid,_ in STAGES}}
    parent={key:key for key in folders}
    def find(key):
        while parent[key]!=key:
            parent[key]=parent[parent[key]]; key=parent[key]
        return key
    links=[link for link in result['links'] if link['left']['id'] in folders and link['right']['id'] in folders]
    for link in links:
        if link.get('status')=='confirmed': parent[find(link['right']['id'])]=find(link['left']['id'])
    groups=defaultdict(list)
    for key,folder in folders.items(): groups[find(key)].append(folder)
    outgoing=defaultdict(set); incoming=set()
    for link in links:
        left=find(link['left']['id']); right=find(link['right']['id'])
        if left!=right and link.get('status')!='rejected': outgoing[left].add(right); incoming.add(right)
    ordering=lambda key:min((f['stage'],f['name'],f['folder']) for f in groups[key])
    roots=sorted((key for key in groups if key not in incoming),key=ordering)
    covered=set(); rows=[]
    for root in roots+sorted(groups,key=ordering):
        if root in covered: continue
        reached=set(); queue=[root]
        while queue:
            key=queue.pop()
            if key in reached: continue
            reached.add(key); queue.extend(outgoing[key])
        covered.update(reached)
        stages={sid:[] for sid,_ in STAGES}
        for key in sorted(reached,key=ordering):
            for folder in sorted(groups[key],key=lambda f:(f['stage'],f['name'],f['folder'])):
                stages[folder['stage']].append(dict(folder,candidate=key!=root))
        evidence=[link for link in links if find(link['left']['id']) in reached and find(link['right']['id']) in reached and link.get('status')!='rejected']
        rejected=[link for link in links if find(link['left']['id'])==root and link.get('status')=='rejected']
        statuses={link.get('status','pending') for link in evidence}
        status='review' if 'review' in statuses else 'pending' if 'pending' in statuses else 'confirmed' if evidence else 'rejected' if rejected else 'unlinked'
        if result.get('inventory_only'): status='unscanned'
        anchor=min(groups[root],key=lambda f:(f['stage'],f['name']))
        rows.append(dict(id=identifier('|'.join(sorted(f['id'] for f in groups[root]))),name=anchor['name'],stages=stages,
                         status=status,ambiguous=any(len(values)>1 for values in stages.values()) or any(e.get('ambiguous') for e in evidence),
                         links=[{k:v for k,v in link.items() if k!='evidence'} for link in evidence+rejected],
                         evidence_count=sum(link['evidence_count'] for link in evidence)))
    return rows
