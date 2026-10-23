"""合并 PDF 原生文字与 OCR，按页内归属生成可复核字段候选。"""
import json
from pathlib import Path
from link_stages import target_records,DEST
from inspect_sources import ROOT,OUT
from material_evidence import PATTERNS,IDENTIFIERS,page_evidence,field_candidates,material_stage
from match_engine import normalize,area_m2
from cross_stage_materials import name_similarity
from address_match import address_match
from entity_evidence import entity_blocks,ocr_identifier_candidates
from relationship_resolution import valid_judgements


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    targets=target_records();result=[];seen=set();judgements=valid_judgements(Path.cwd())
    supply_path=OUT/'供地矢量1016.json'
    supply=json.loads(supply_path.read_text(encoding='utf-8')) if supply_path.exists() else []
    for target in targets:
        aliases=set(target.get('name_aliases',[]))
        for record in supply:
            pairs=(('regulator_id','DZJGH'),('contract_id','HTHBBH'))
            exact=any(normalize(target.get(k)) and normalize(target[k])==normalize(record.get(c)) for k,c in pairs)
            conflict=any(normalize(target.get(k)) and normalize(record.get(c)) and normalize(target[k])!=normalize(record[c]) for k,c in pairs)
            if exact and not conflict and record.get('XMMC'):aliases.add(record['XMMC'])
        target['name_aliases']=sorted(aliases)
    by_project={target['project']:target for target in targets}
    manifest=json.loads((OUT/'ocr_manifest.json').read_text(encoding='utf-8'))
    native_path=OUT/'pdf_text.json'
    native=json.loads(native_path.read_text(encoding='utf-8')) if native_path.exists() else []
    images={x['source']:x for x in manifest}
    pages=[]
    for page in native:
        source=page['source']+'#page='+str(page['page'])
        item=images.get(source,{})
        pages.append(dict(project=page['project'],retrieval_projects=item.get('retrieval_projects',page.get('retrieval_projects',[page['project']])),source=source,image=item.get('path'),storage_stage=item.get('storage_stage',page.get('storage_stage')),text=page['text'],text_method='PDF原生文字',retrieval_reason=page.get('retrieval_reason')))
    for item in manifest:
        path=Path(item['text'])
        if path.exists():pages.append(dict(project=item['project'],retrieval_projects=item.get('retrieval_projects',[item['project']]),source=item['source'],image=item['path'],storage_stage=item.get('storage_stage'),text=path.read_text(encoding='utf-8'),layout=json.loads(Path(str(path)+'.layout.json').read_text(encoding='utf-8')) if Path(str(path)+'.layout.json').exists() else None,text_method='本机OCR'))
    expanded=[]
    for page in pages:
        for index,entity in enumerate(entity_blocks(page['text'],page.get('layout')),1):
            expanded.append(dict(page,text=entity['text'],block_id=f'block-{index}',segmentation=entity['method']))
    for page in expanded:
        if not page['text'].strip():continue
        key=(page['source'],page['text_method'],page['block_id'])
        if key in seen:continue
        seen.add(key)
        evidence=page_evidence(page['text'],targets)
        corrections=ocr_identifier_candidates(page['text'],targets,evidence['fields']) if page['text_method']=='本机OCR' else []
        if corrections:
            corrected=page_evidence(page['text'],[dict(t,inferred_keys=list(set(t.get('inferred_keys',[]))|{c['key'] for c in corrections})) for t in targets])
            evidence['projects']=corrected['projects']
        candidates=field_candidates(page['text'],evidence['fields'])
        hints=set(page['retrieval_projects']) & by_project.keys()
        matches=[]
        amounts=[c for c in candidates if c['column'] in (32,40,48,61)]
        units=[c['value'] for c in candidates if c['column'] in (37,46,54,62)]
        for target in targets:
            name_score=max(name_similarity(n,page['text']) for n in [target.get('name'),*target.get('name_aliases',[])])
            location=address_match(target.get('address'),page['text'],target.get('road_aliases'))
            address_score=max([location['score'],*[address_match(v,page['text'],target.get('road_aliases'))['score'] for v in target.get('address_aliases',[])]])
            page_area=area_m2(amounts[0]['value'],units[0]) if len(amounts)==1 and len(set(units))==1 else None
            a=target.get('area_m2');area_error=abs(a-page_area)/max(a,page_area) if a and page_area else None
            corrected_hint=any(normalize(target.get(c['key']))==normalize(c['candidate']) for c in corrections)
            if not corrected_hint and target['project'] not in hints|set(evidence['page_project_candidates']) and name_score<.8 and address_score<.8 and (area_error is None or area_error>.02):continue
            conflicts=[k for k in IDENTIFIERS if normalize(target.get(k)) and evidence['fields'].get(k) and any(normalize(v)!=normalize(target[k]) for v in evidence['fields'][k])]
            if location['conflicts']:conflicts.append('address_direction')
            if conflicts:
                evidence['identity_conflicts']=sorted(set(evidence['identity_conflicts'])|{target['project']});continue
            corrected_keys=[c['key'] for c in corrections]
            corrected_match=any(normalize(target.get(k)) and normalize(target[k]) in [normalize(v) for v in evidence['fields'].get(k,[])] for k in corrected_keys)
            matches.append(dict(ocr_correction=corrected_match,address_constraints=location,project=target['project'],name=target.get('name'),name_score=round(name_score,4),address_score=round(address_score,4),
                                area_error=area_error,score=round(.55*name_score+.25*address_score+.2*(1-area_error if area_error is not None else 0),4),
                                basis='正文名称/位置及面积候选' if name_score>=.8 or address_score>=.8 else '路径或编号检索线索' if target['project'] in hints else '仅面积近似，项目归属需核实'))
        matches.sort(key=lambda m:m['score'],reverse=True)
        manual={}
        for j in judgements:
            if j['source_label']==page['source'] and (j.get('source_block')==page['block_id'] or not j.get('source_block') and page['segmentation']=='单实体或无法可靠分段'):manual[j['project']]=j['verdict']
        rejected={p for p,v in manual.items() if v=='不一致'}
        evidence['projects']=[p for p in evidence['projects'] if p not in rejected]
        confirmed=[p for p,v in manual.items() if v=='一致' and p not in evidence['identity_conflicts']]
        if len(confirmed)==1:evidence['projects']=confirmed;evidence['identity_reason']='人工核对原件与图斑归属一致，几何及原件版本未变化'
        matches=[m for m in matches if m['project'] not in rejected]
        pending=[m['project'] for m in matches] if not evidence['projects'] else []
        if pending:evidence['identity_reason']='检索或正文模糊匹配候选，需逐页确认项目归属'
        try:storage_stage=Path(page['source'].split('!',1)[0].split('#page=',1)[0]).relative_to(ROOT).parts[0]
        except ValueError:storage_stage=page.get('storage_stage')
        record=dict(block_id=page['block_id'],segmentation=page['segmentation'],ocr_corrections=corrections,folder_project=page['project'],pending_projects=pending,source=page['source'],image=page['image'],storage_stage=storage_stage,text_method=page['text_method'],
                    material_stage=material_stage(page['text']),field_candidates=candidates,project_matches=matches,retrieval_reason=page.get('retrieval_reason'),text=page['text'],
                    status='NEEDS_LLM',reason='识别值仅作候选，需核实页内名称与字段位置',**evidence)
        result.append(record)
    (DEST/'识别字段候选.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in result),encoding='utf-8')
    (DEST/'未关联材料.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in result if not r['projects']),encoding='utf-8')
    summary=dict(text_records=len(result),with_fields=sum(bool(r['fields']) for r in result),associated_records=sum(bool(r['projects']) for r in result),
                 candidate_values=sum(len(r['field_candidates']) for r in result if r['projects']),unassociated_records=sum(not r['projects'] for r in result),
                 unique_pages=len({r['source'] for r in result}),pages_with_fields=len({r['source'] for r in result if r['fields']}),
                 candidate_project_fields=len({(p,c['column']) for r in result for p in r['projects'] for c in r['field_candidates']}))
    input_pages=set(images)|{p['source']+'#page='+str(p['page']) for p in native}
    summary.update(input_pages=len(input_pages),unreadable_pages=len(input_pages-{r['source'] for r in result}),
                   ocr_missing_pages=sum(not Path(p['text']).exists() for p in manifest),
                   pending_project_fields=len({(p,c['column']) for r in result for p in r['pending_projects'] for c in r['field_candidates']}))
    (DEST/'材料识别统计.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
