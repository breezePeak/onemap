"""以供应档案编号、监管号和合同号为起点，扩展跨阶段材料检索。"""
import hashlib
import json
from pathlib import Path
import re
from difflib import SequenceMatcher
from match_engine import normalize
from material_evidence import IDENTIFIERS
from inspect_sources import is_office_lock

MEDIA={'.pdf','.jpg','.jpeg','.png','.bmp','.doc','.docx','.xls','.dwg','.dxf'}
SENSITIVE=re.compile(r'身份证|身份复印|授权|委托|营业执照|户口|人证|法人证明')


def media_header(header):
    if header.startswith(b'%PDF'):return '.pdf'
    if header.startswith(b'\xff\xd8'):return '.jpg'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):return '.png'
    if header.startswith(b'BM'):return '.bmp'
    return None


def read_json(path,default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def contains_token(text,token):
    if not token:return False
    return re.search(r'(?<![A-Z0-9])'+re.escape(str(token).upper())+r'(?![A-Z0-9])',str(text).upper()) is not None


def search_terms(targets,work,output):
    terms={t['project']:{str(v) for k,v in t.items() if (k in (*IDENTIFIERS,'archive_id') or (k=='project' and 'archive_id' not in t)) and v} for t in targets}
    for target in targets:terms[target['project']].update([*target.get('name_aliases',[]),*target.get('address_aliases',[]),*target.get('road_aliases',{}),*target.get('road_aliases',{}).values()])
    supply=read_json(work/'供地矢量1016.json',[])
    ledgers=[read_json(work/(branch+'_台账.json'),[])[1:] for branch in ('6.用地规划','7.工程规划')]
    # 供应图层只补检索别名，不改目标表；监管号/合同号冲突的记录不传播。
    for target in targets:
        for record in supply:
            pairs=(('regulator_id','DZJGH'),('contract_id','HTHBBH'))
            exact=any(normalize(target.get(k)) and normalize(target[k])==normalize(record.get(c)) for k,c in pairs)
            conflict=any(normalize(target.get(k)) and normalize(record.get(c)) and normalize(target[k])!=normalize(record[c]) for k,c in pairs)
            if exact and not conflict and record.get('XMMC'):terms[target['project']].add(record['XMMC'])
    by_project={t['project']:t for t in targets}
    for spatial in read_json(work/'spatial_matches.json',[]):
        if spatial.get('supply_pending') or spatial.get('anchor_layer','supply')!='supply':continue
        target=by_project.get(spatial['project'])
        if not target:continue
        if target.get('area_m2') and abs(spatial['area']-target['area_m2'])/target['area_m2']>.02:continue
        parcels=[p for p in spatial['parcels'] if p.get('manual_verdict')!='不一致' and p['supply_coverage']>=.98 and p['target_coverage']>=.98]
        if len(parcels)==1:
            unit=parcels[0]['attributes'].get('BDCDYH')
            if unit and (not target.get('unit_id') or normalize(target['unit_id'])==normalize(unit)):terms[target['project']].add(unit)
    # 原始台账名称与代码配对；共享名称的不同代码不擅自选一个。
    for target in targets:
        codes=set()
        for rows in ledgers:
            for row in rows:
                if normalize(row[12])==normalize(target['name']) and row[9]:codes.add(str(row[9]))
        if len(codes)==1 and (not target.get('project_code') or normalize(next(iter(codes)))==normalize(target['project_code'])):terms[target['project']].update(codes)
    candidates=output/'识别字段候选.jsonl'
    if candidates.exists():
        for line in candidates.read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            item=json.loads(line)
            if len(item.get('projects',[]))!=1:continue
            project=item['projects'][0]
            if project not in by_project:continue
            for key in IDENTIFIERS:
                values=item['fields'].get(key,[])
                old=by_project[project].get(key)
                if len(values)==1 and (not old or normalize(old)==normalize(values[0])):
                    terms[project].update(values)
    return terms


def name_in_path(value,label):
    return name_similarity(value,label)>=.8


def name_similarity(value,label):
    name,text=normalize(value),normalize(label)
    if len(name)<3:return 0
    if name in text:return 1
    if len(name)<4:return 0
    # shortcut: 80% 字符相似度仅用于扩大检索，积累标注后校准；不得据此自动确认归属。
    return max((SequenceMatcher(None,name,text[i:i+size],autojunk=False).ratio()
               for size in (len(name)-1,len(name),len(name)+1)
               for i in range(max(0,len(text)-size+1))),default=0)


def path_projects(label,targets,terms):
    def matches(value):
        if re.match(r'^[A-Z0-9]',str(value).upper()):return contains_token(label,value)
        return name_in_path(value,label)
    return [t['project'] for t in targets if any(matches(v) for v in terms[t['project']])
            or name_in_path(t.get('name'),label) or name_in_path(t.get('address'),label)]


def discover_files(root,work,output,targets,documents):
    """附件指纹仅扩展检索；候选页仍需独立验证项目归属。"""
    terms=search_terms(targets,work,output)
    entries={Path(x['path']):x for x in read_json(work/'files.json',[]) if not is_office_lock(x['path'])}
    files=list(entries)
    # 供应最完整，先读取供应；其他目录仅按已有供应关联线索扩展。
    files.sort(key=lambda p:(0 if '4.土地供应阶段' in p.parts else 1,str(p)))
    cache=read_json(work/'hash_cache.json',{});seeds={}
    def digest(path):
        stat=path.stat();key=str(path);old=cache.get(key)
        if old and old['size']==stat.st_size and old['mtime_ns']==stat.st_mtime_ns:return old['sha256']
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        cache[key]=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,sha256=h.hexdigest())
        return cache[key]['sha256']
    for doc in documents:
        path=Path(doc['path'])
        if path.is_file():seeds.setdefault(digest(path),set()).update(doc.get('retrieval_projects',[doc['project']]))
    fingerprint_hits={}
    for path in files:
        if not path.is_relative_to(root) or path.suffix.lower()!='.pdf' or SENSITIVE.search(str(path)) or not any(b in path.parts for b in ('6.用地规划','7.工程规划')):continue
        if seeds:
            projects=seeds.get(digest(path),set())
            if len(projects)==1:
                relative=path.relative_to(root)
                if len(relative.parts)>2:fingerprint_hits.setdefault(digest(path),[]).append((path,projects,relative.parts[1]))
    links=[]
    # 同一附件出现在多个项目代码目录时不将其当作唯一关联。
    for fingerprint,hits in fingerprint_hits.items():
        if len({code for path,projects,code in hits})!=1:continue
        for path,projects,code in hits:
            for project in projects:
                terms[project].add(code);links.append(dict(project=project,source=str(path),method='同字节供应关联附件，扩展所在项目目录',retrieval_only=True))
    selected=[];errors=[]
    for path in files:
        if SENSITIVE.search(str(path)):continue
        entry=entries[path]
        if (not path.is_relative_to(root) and 'source' not in entry) or '数据图层' in path.parts:continue
        source=entry.get('source',str(path))
        if SENSITIVE.search(source):continue
        kind=path.suffix.lower()
        if kind not in MEDIA:
            try:
                with path.open('rb') as stream:kind=media_header(stream.read(16))
            except OSError as error:errors.append(dict(source=source,error=str(error)));continue
        if not kind:continue
        projects=path_projects(source,targets,terms)
        selected.append(dict(path=path,kind=kind,projects=projects,source=source,storage_stage=entry.get('storage_stage') or path.relative_to(root).parts[0],
                             retrieval_reason='路径名称、位置或编号命中' if projects else '全文扫描：路径无项目线索'))
    selected.sort(key=lambda item:(not bool(item['projects']),not bool(re.search(r'选址|规划条件|红线|合同|决定书|批复',item['source'])),item['source']))
    work.mkdir(parents=True,exist_ok=True)
    (work/'hash_cache.json').write_text(json.dumps(cache,ensure_ascii=False),encoding='utf-8')
    (work/'cross_stage_links.json').write_text(json.dumps(dict(search_terms={k:sorted(v) for k,v in terms.items()},attachment_links=links),ensure_ascii=False,indent=2),encoding='utf-8')
    (work/'source_read_errors.json').write_text(json.dumps(errors,ensure_ascii=False),encoding='utf-8')
    return selected,terms
