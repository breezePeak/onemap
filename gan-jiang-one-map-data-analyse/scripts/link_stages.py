"""多路挂接、附件指纹和大模型待办队列；所有日志均落在 outputs 内。"""
import csv
import hashlib
import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path
import openpyxl
from inspect_sources import ROOT,OUT,is_office_lock
from match_engine import normalize,area_m2,decide

DEST=Path('outputs/governance')

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def target_records(path="test_result.xlsx"):
    review_path=Path(path).parent/'review.json'
    review=json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else {}
    w=openpyxl.load_workbook(path,read_only=True,data_only=True)
    try:
        targets=[]
        for i,record in enumerate(w.worksheets[0].iter_rows(min_row=3,max_col=69,values_only=True),3):
            if not is_project_row(record):continue
            values=list(record)
            for cell,candidate_id in review.get('decisions',{}).items():
                entry=review.get('cells',{}).get(cell,{})
                if entry.get('project')!=f'row-{i}':continue
                candidate=next((c for c in entry.get('candidates',[]) if c['id']==candidate_id),None)
                if candidate and (not candidate.get('project_pending') or review.get('project_confirmations',{}).get(cell)==candidate_id):
                    values[entry['column']-1]=candidate['value']
            targets.append(dict(row_identity(values),row=i,project=f'row-{i}'))
        evidence_path=Path(path).parent/'outputs/governance/识别字段候选.jsonl'
        inferred={}
        if evidence_path.exists():
            for line in evidence_path.read_text(encoding='utf-8').splitlines():
                if not line.strip():continue
                item=json.loads(line)
                if len(item.get('projects',[]))!=1 or item.get('identity_conflicts'):continue
                for key in ('project_code','regulator_id','contract_id','unit_id','condition_id','approval_id','certificate_id'):
                    for value in item.get('fields',{}).get(key,[]):
                        inferred.setdefault((item['projects'][0],key),{}).setdefault(normalize(value),value)
        for target in targets:
            target['inferred_keys']=[]
            for (project,key),values in inferred.items():
                if project==target['project'] and not normalize(target.get(key)) and len(values)==1 and next(iter(values)):
                    target[key]=next(iter(values.values()));target['inferred_keys'].append(key)
        alias_path=Path(path).parent/'outputs/governance/人工别名.json'
        aliases=json.loads(alias_path.read_text(encoding='utf-8')) if alias_path.exists() else []
        for target in targets:
            selected=[a for a in aliases if a['project']==target['project']]
            target['name_aliases']=[a['alias'] for a in selected if a['kind']=='name']
            target['address_aliases']=[a['alias'] for a in selected if a['kind']=='address']
            target['road_aliases']={a['alias']:a['canonical'] for a in selected if a['kind']=='road'}
        return targets
    finally:w.close()


def row_identity(record):
    return dict(archive_id=str(record[1] or ''),name=record[23] or record[9] or record[2],project_code=record[24],regulator_id=record[17],contract_id=record[18],
                unit_id=record[14] if len(str(record[14]))==28 else None,condition_id=record[42],approval_id=record[51],certificate_id=record[34],
                address=record[58] or record[11],area_m2=area_m2(record[60],record[61]))


def is_project_row(record):
    return any(normalize(record[i]) for i in (1,2,5,6,7,8,9,11,12,13,14,17,18,23,24))


def required_parser(path):
    if is_office_lock(path):return None
    extension=path.suffix.lower()
    parser={'.xls':'旧版 XLS 解析器','.doc':'旧版 Word 文本解析器','.dwg':'CAD 实体/属性提取器','.rar':'RAR 解包器'}.get(extension)
    if extension=='.dwg' and any((Path(__file__).resolve().parent.parent/'work/cad_runtime').rglob('dwg2dxf.exe')):parser=None
    if extension in ('.xls','.doc') and shutil.which('soffice'):parser=None
    if extension=='.rar' and shutil.which('tar'):parser=None
    if extension=='.xlsx':
        with path.open('rb') as f:signature=f.read(8)
        if signature==b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':parser='文件实际为 OLE/XLS，不能使用 XLSX 解析器'
    return parser

def main():
    DEST.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(message)s',handlers=[logging.StreamHandler(),logging.FileHandler(DEST/'挂接日志.log',encoding='utf-8',mode='w')],force=True)
    events=[]; tasks=[]
    def emit(status,project,stage,reason,**details):
        item=dict(status=status,project=project,stage=stage,reason=reason,**details);events.append(item)
        logging.info('[%s] 项目=%s 阶段=%s %s %s',status,project,stage,reason,json.dumps(details,ensure_ascii=False,default=str))
        if status in ('NEEDS_LLM','NEEDS_FUZZY','NEEDS_PARSER','AMBIGUOUS','CONFLICT','NOT_FOUND'):tasks.append(item)
    for item in json.loads((OUT/'files.json').read_text(encoding='utf-8')):
        p=Path(item['path'])
        if is_office_lock(p):continue
        try:parser=required_parser(p)
        except OSError as error:
            emit('NEEDS_PARSER','来源盘点',p.relative_to(ROOT).parts[0],'单个来源文件无法读取，记录后继续其他材料',source=str(p),error=str(error))
            continue
        if parser:
            emit('NEEDS_PARSER','来源盘点',p.relative_to(ROOT).parts[0],'当前流水线尚未解析该格式，不能当作无数据',source=str(p),required_parser=parser)
    for item in json.loads((OUT/'pdf_errors.json').read_text(encoding='utf-8')):
        emit('NEEDS_LLM','来源盘点','材料解析','材料读取失败，需单独打开原件核实',**item)
    for filename,stage in [('material_errors.json','供应扫描件'),('ocr_errors.json','OCR')]:
        path=OUT/filename
        if path.exists():
            for item in json.loads(path.read_text(encoding='utf-8')):emit('NEEDS_PARSER','来源盘点',stage,'单个材料未读取，保留原件并继续其他材料',**item)
    vector_report=OUT.parent/'outputs/governance/矢量整理统计.json'
    if vector_report.exists():
        for error in json.loads(vector_report.read_text(encoding='utf-8')).get('errors',[]):emit('NEEDS_PARSER','来源盘点','附件图形','单个矢量/CAD读取失败，原件保留待核对',**error)
    render_errors=OUT/'pdf_render_errors.json'
    if render_errors.exists():
        for item in json.loads(render_errors.read_text(encoding='utf-8')):
            emit('NEEDS_LLM','来源盘点','PDF渲染','部分页面未能渲染，保留原件和已完成页供复核',**item)
    targets=target_records()
    supply=json.loads((OUT/'供地矢量1016.json').read_text(encoding='utf-8'))
    sources={
        '土地供应':[dict(name=r['XMMC'],regulator_id=r['DZJGH'],contract_id=r['HTHBBH'],address=r['TDZL'],area_m2=float(r['XMZMJ']) if float(r['XMZMJ'])>0 else None,source='供地矢量1016.dbf',object_id=r['OBJECTID']) for r in supply],
        '土地首登':[dict(name=r['QLRMC'],unit_id=r['BDCDYH'],address=r['ZL'],area_m2=float(r['ZDMJ']),source='地表宗地.dbf',object_id=r['OBJECTID']) for r in json.loads((OUT/'地表宗地.json').read_text(encoding='utf-8'))],
    }
    # 台账多列由不同列表拼接，原始 XMDM/XMMC 配对读取，不与人工补充列横向拼接。
    for filename in ('6.用地规划_台账.json','7.工程规划_台账.json'):
        rows=json.loads((OUT/filename).read_text(encoding='utf-8'))
        sources[filename.split('_')[0]]=[dict(name=r[12],project_code=r[9],address=r[15],source=filename,row=i,construction_unit=r[18]) for i,r in enumerate(rows[1:],2) if r[12]]
        for i,r in enumerate(rows[1:],2):
            if r[4] and r[12] and normalize(r[4])!=normalize(r[12]):
                emit('CONFLICT','台账第'+str(i)+'行',filename,'人工补充项目名称与原始 XMMC 不一致，禁止按同行挂接',manual_name=r[4],raw_name=r[12])
    for t in targets:
        for stage,records in sources.items():
            status,candidates=decide(t,records)
            emit(status,t['project'],stage,'精确标识优先；模糊仅生成候选，不自动写入',candidates=candidates)
    spatial=json.loads((OUT/'spatial_matches.json').read_text(encoding='utf-8'))
    for result in spatial:
        if result.get('supply_pending'):
            emit('NEEDS_FUZZY',result['project'],'供地图斑模糊挂接','无精确供地标识，逐条比较名称、位置和面积候选，不自动采用',candidates=result['candidates'])
            continue
        t=next(t for t in targets if t['project']==result['project'])
        if result.get('anchor_layer','supply')=='supply' and t.get('area_m2') and abs(result['area']-t['area_m2'])/t['area_m2']>.02:
            emit('CONFLICT',result['project'],'供地面积核对','供地图斑面积与表内供地面积差异超过2%，禁止将局部空间挂接当作完整项目',geometry_area_m2=result['area'],declared_area_m2=t['area_m2'])
        for key,stage in [('approvals','报批空间挂接'),('parcels','首登空间挂接')]:
            hits=[h for h in result[key] if h.get('manual_verdict')!='不一致']
            good=[x for x in hits if x['supply_coverage']>=.98 and x['target_coverage']>=.98]
            status='MATCH_SPATIAL' if len(good)==1 and result.get('resolution',{}).get('automatic_allowed',True) else 'NEEDS_FUZZY' if hits else 'NOT_FOUND'
            emit(status,result['project'],stage,'参考范围与目标双向覆盖均≥98%；批次范围不代表项目独占范围',anchor_layer=result.get('anchor_layer','supply'),anchor_method=result.get('anchor_method'),reference_area_m2=result['area'],candidates=hits)
    # 完整文件指纹：共用文件或同一文件出现多个项目时只提示，不把它作为独占证据。
    documents=json.loads((OUT/'pdf_documents.json').read_text(encoding='utf-8'))
    hashes=defaultdict(list)
    for d in documents:hashes[digest(Path(d['path']))].append(d)
    by_hash=defaultdict(list)
    cache_path=OUT/'hash_cache.json'
    cache=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    processed=0
    for branch in ('6.用地规划','7.工程规划'):
        for p in (ROOT/branch).rglob('*.pdf'):
            stat=p.stat();cache_key=str(p)
            cached=cache.get(cache_key)
            if cached and cached['size']==stat.st_size and cached['mtime_ns']==stat.st_mtime_ns:
                fingerprint=cached['sha256']
            else:
                fingerprint=digest(p);cache[cache_key]=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,sha256=fingerprint)
            if fingerprint in hashes:
                by_hash[fingerprint].append(dict(source=str(p),project_code=p.relative_to(ROOT/branch).parts[0]))
            processed+=1
            if processed%200==0:logging.info('[INDEX_PROGRESS] 附件指纹已检查 %s 个文件',processed)
    cache_path.write_text(json.dumps(cache,ensure_ascii=False),encoding='utf-8')
    for fingerprint,docs in hashes.items():
        if fingerprint not in by_hash:continue
        projects={d['project'] for d in docs};codes={d['project_code'] for d in by_hash[fingerprint]}
        status='MATCH_ATTACHMENT' if len(projects)==len(codes)==1 else 'AMBIGUOUS'
        emit(status,'；'.join(sorted(projects)),'附件哈希','同字节附件匹配；共用材料不能唯一指向项目',sha256=fingerprint,candidates=by_hash[fingerprint])
    reviewed={str(Path(x['evidence_image']).resolve()) for x in json.loads(Path('reviewed_fields.json').read_text(encoding='utf-8'))} if Path('reviewed_fields.json').exists() else set()
    seen=set()
    for x in json.loads((OUT/'ocr_manifest.json').read_text(encoding='utf-8')):
        if x['path'] in seen:continue
        seen.add(x['path']);p=Path(x['text'])
        text=p.read_text(encoding='utf-8') if p.exists() else ''
        compact=''.join(text.split())
        business=any(k in x['source'] for k in ['选址意见','规划条件','规划红线','划拨决定','批复','报批','抄告'])
        if str(Path(x['path']).resolve()) in reviewed:
            emit('REVIEWED_VISUAL',x['project'],'附件识别','已逐页视觉核验，回填见填充溯源',source=x['source'],image=x['path']);continue
        if not text or (business and (len(compact)<60 or not any(k in compact for k in ['号','用地','项目','宗地','面积']))):
            emit('NEEDS_LLM',x['project'],'附件识别','OCR 缺失或关键字段质量不足，需图像识别/版面理解',source=x['source'],image=x['path'],ocr_text=str(p),action='先核实本页项目名称，再识别编号、面积、单位；不可用所在目录替代本页名称')
        elif business:
            emit('NEEDS_LLM',x['project'],'附件字段核对','OCR 已完成；扫描件字段尚需核对，合订本可能含其他道路',source=x['source'],image=x['path'],ocr_text=str(p),action='识别本页所属项目；提取字段并返回原文证据；低置信度不回填')
    for name,data in [('挂接明细.jsonl',events),('待复核队列.jsonl',tasks)]:
        (DEST/name).write_text('\n'.join(json.dumps(x,ensure_ascii=False,default=str) for x in data)+'\n',encoding='utf-8')
    counts={s:sum(x['status']==s for x in events) for s in sorted({x['status'] for x in events})}
    (DEST/'挂接统计.json').write_text(json.dumps(counts,ensure_ascii=False,indent=2),encoding='utf-8')
    logging.info('挂接统计 %s',json.dumps(counts,ensure_ascii=False))

if __name__=='__main__':main()
