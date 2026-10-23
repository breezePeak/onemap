"""以几何并集核算多宗地/多批次，跨项目共享宗地阻断自动采用。"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from shapely import union_all
from shapely.geometry import mapping,shape

def signature(geometry):
    if isinstance(geometry,dict) and geometry.get('type'):geometry=mapping(shape(geometry).normalize())
    return hashlib.sha256(json.dumps(geometry,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def read(path,default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def valid_judgements(job):
    job=Path(job);views=read(job/'work/spatial_geometry.json',{});valid=[]
    for record in read(job/'outputs/governance/人工图形判读.json',[]):
        root=views.get(record['project'],{})
        candidates=[c['view'] for c in root.get('supply_candidates',[])] if root.get('supply_pending') else [root]
        if not record.get('source_path') or not Path(record['source_path']).is_file():continue
        if hashlib.sha256(Path(record['source_path']).read_bytes()).hexdigest()!=record.get('source_signature'):continue
        if any(signature(v.get('supply'))==record.get('anchor_signature') and record.get('geometry_signatures') and all(any(h['id']==key and signature(h.get('geometry'))==value for h in v.get('hits',[])) for key,value in record['geometry_signatures'].items()) for v in candidates):valid.append(record)
    return valid

def resolve_spatial(results,layers,job):
    job=Path(job);out=job/'outputs/governance';out.mkdir(parents=True,exist_ok=True)
    judgements=read(out/'人工图形判读.json',[]);claims=read(out/'范围归属确认.json',[]);uses=defaultdict(set);flat=[];node_claims=defaultdict(dict);result_nodes={}
    for result in results:
        for r in [c['result'] for c in result.get('candidates',[])] if result.get('supply_pending') else [result]:
            anchor=r.get('anchor_layer','supply');indices=r.get('supply_indices',[])
            footprint=union_all([layers[anchor][i] for i in indices]);anchor_hash=signature(mapping(footprint))
            r['anchor_signature']=anchor_hash;groups=[];nodes=set()
            choices=[c for c in claims if c['project']==r['project'] and c['anchor_signature']==anchor_hash]
            choice=choices[-1]['mode'] if choices else None
            for layer in ('approvals','parcels','attachments'):
                grouped=defaultdict(list)
                for hit in r.get(layer,[]):
                    i=hit['record_index'];g=layers[layer][i];attrs=hit['attributes']
                    hit_id=layer+':'+str(attrs['OBJECTID'])+':'+str(i);geometry_hash=signature(mapping(g))
                    if choice in ('shared','exclusive') and choices[-1].get('geometry_signatures',{}).get(hit_id)==geometry_hash:
                        node_claims[(layer,geometry_hash)][r['project']]=choice
                    applicable=[j for j in judgements if j['project']==r['project'] and j.get('anchor_signature')==anchor_hash and j.get('geometry_signatures',{}).get(hit_id)==geometry_hash]
                    applicable=[j for j in applicable if not j.get('source_path') or Path(j['source_path']).is_file() and hashlib.sha256(Path(j['source_path']).read_bytes()).hexdigest()==j.get('source_signature')]
                    if applicable:hit['manual_verdict']=applicable[-1]['verdict']
                    if hit.get('manual_verdict')=='不一致':continue
                    key=attrs.get('PFWH') or attrs.get('PZWH') or attrs.get('BDCDYH') or str(attrs['OBJECTID'])
                    grouped[str(key)].append(g.intersection(footprint))
                    if layer in ('parcels','attachments') and choice!='reject':
                        node=(layer,signature(mapping(g)));uses[node].add(r['project']);nodes.add(node)
                for key,parts in grouped.items():
                    union=union_all(parts)
                    groups.append(dict(layer=layer,key=key,count=len(parts),intersection_m2=union.area,coverage=union.area/footprint.area if footprint.area else 0,
                                       duplicated_m2=max(0,sum(p.area for p in parts)-union.area)))
            r['groups']=groups
            approval_parts=[layers['approvals'][h['record_index']].intersection(footprint) for h in r.get('approvals',[]) if h.get('manual_verdict')!='不一致']
            union=union_all(approval_parts)
            r['resolution']=dict(automatic_allowed=choice!='reject',excluded=choice=='reject',manual_scope=choice,approval_union_coverage=union.area/footprint.area if footprint.area else 0,
                                 approval_overlap_m2=max(0,sum(p.area for p in approval_parts)-union.area),
                                 structure='多批次组合' if sum(g['layer']=='approvals' for g in groups)>1 else '单批次或缺报批',conflicts=[])
            for i in indices:
                if anchor in ('supply','parcels','attachments'):
                    node=(anchor,signature(mapping(layers[anchor][i])))
                    if choice!='reject':uses[node].add(r['project']);nodes.add(node)
                    if choice in ('shared','exclusive'):node_claims[node][r['project']]=choice
            flat.append(r)
            result_nodes[id(r)]=nodes
    conflicts=[]
    for (layer,index),projects in uses.items():
        if len(projects)<2:continue
        owners={p for p,mode in node_claims[(layer,index)].items() if mode=='exclusive'}
        shared=all(node_claims[(layer,index)].get(p)=='shared' for p in projects)
        status='resolved_exclusive' if len(owners)==1 else 'resolved_shared' if shared else 'unresolved'
        conflict=dict(layer=layer,geometry_signature=index,projects=sorted(projects),status=status,reason='同一宗地/供地/附件范围关联多个项目，核实共用、拆分或合并')
        conflicts.append(conflict)
        for r in flat:
            if (layer,index) in result_nodes[id(r)]:
                r['resolution']['conflicts'].append(conflict)
                if status=='unresolved':r['resolution']['automatic_allowed']=False
                if len(owners)==1 and r['project'] not in owners:r['resolution']['excluded']=True;r['resolution']['automatic_allowed']=False
    # 连通分量保留跨项目整体争议，不能逐单元格各选各的后误报全部消歧。
    adjacency=defaultdict(set)
    for conflict in conflicts:
        if conflict['status']!='unresolved':continue
        for p in conflict['projects']:adjacency[p].update(conflict['projects'])
    components=[];seen=set()
    for p in adjacency:
        if p in seen:continue
        pending=[p];component=set()
        while pending:
            node=pending.pop()
            if node in component:continue
            component.add(node);pending.extend(adjacency[node]-component)
        seen.update(component);components.append(sorted(component))
    report=dict(projects=[dict(project=r['project'],anchor_layer=r.get('anchor_layer','supply'),groups=r['groups'],**r['resolution']) for r in flat],conflicts=conflicts,components=components)
    (out/'整体挂接消歧.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    from governance_outputs import table
    table(out/'整体挂接消歧.xlsx',['项目','参考图层','组合类型','报批并集覆盖率','重复相交平方米','冲突','采用要求'],
          [[r['project'],r.get('anchor_layer','supply'),r['resolution']['structure'],r['resolution']['approval_union_coverage'],r['resolution']['approval_overlap_m2'],json.dumps(r['resolution']['conflicts'],ensure_ascii=False),'需人工消歧' if not r['resolution']['automatic_allowed'] else '结合文书复核'] for r in flat])
    return report
