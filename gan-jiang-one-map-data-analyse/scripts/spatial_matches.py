"""投影一致时按真实交集面积生成阶段挂接证据，不修改原图层。"""
import sys
sys.path.insert(0,str(__import__('pathlib').Path('work/python_packages').resolve()))
import json
from pathlib import Path
import openpyxl
import shapefile
from shapely.geometry import shape, mapping
from shapely import make_valid, union_all
from shapely.strtree import STRtree
from inspect_sources import ROOT, OUT
from link_stages import target_records
from match_engine import area_m2,rank_candidates,normalize
from gis_geometry import reproject,projected_crs
from relationship_resolution import resolve_spatial

ANCHOR_NAMES={'supply':'供地范围','parcels':'首登宗地范围','approvals':'报批批次范围','attachments':'附件矢量范围'}

def load(stem, folder):
    path=ROOT/'数据图层'/folder/(stem+'.shp')
    records=json.loads((OUT/(stem+'.json')).read_text(encoding='utf-8'))
    reader=shapefile.Reader(shp=str(path),shx=str(path.with_suffix('.shx')))
    geometries=[]
    for i in range(len(records)):
        try:
            s=reader.shape(i)
            geometries.append(make_valid(shape(s.__geo_interface__)) if s.shapeType else None)
        except shapefile.ShapefileException as e:
            print('Invalid/empty shape',path.name,i+1,str(e));geometries.append(None)
    assert len(geometries)==len(records),'存在已删除 DBF 记录，需按物理序号读取'
    reader.close()
    return path,records,geometries

def preview(result,supply_records,supply_geometries,approval_records,approval_geometries,parcel_records,parcel_geometries,attachment_records=None,attachment_geometries=None):
    anchor=result.get('anchor_layer','supply')
    base_records,base_geometries={'supply':(supply_records,supply_geometries),'approvals':(approval_records,approval_geometries),'parcels':(parcel_records,parcel_geometries),'attachments':(attachment_records or [],attachment_geometries or [])}[anchor]
    supply_ids={str(x) for x in result['supply_objects']}
    indices=result.get('supply_indices',[i for i,r in enumerate(base_records) if str(r['OBJECTID']) in supply_ids])
    footprint=union_all([base_geometries[i] for i in indices if base_geometries[i] is not None])
    if footprint.is_empty:raise ValueError('未找到本项目对应的参考图斑，无法绘图')
    if 'area' in result and abs(footprint.area-result['area'])>max(1,result['area']*.001):raise ValueError('参考图斑与原结果不一致，请重新检索后查看')
    view=dict(project=result['project'],anchor_signature=result.get('anchor_signature'),reference_crs=result.get('reference_crs'),groups=result.get('groups',[]),resolution=result.get('resolution',{}),anchor_layer=anchor,anchor_name=ANCHOR_NAMES[anchor],anchor_method=result.get('anchor_method','精确供地标识'),supply=mapping(footprint),supply_area_m2=footprint.area,supply_objects=result['supply_objects'],supply_indices=indices,hits=[])
    for layer,records,geometries in [('approvals',approval_records,approval_geometries),('parcels',parcel_records,parcel_geometries),('attachments',attachment_records or [],attachment_geometries or [])]:
        for hit in result.get(layer,[]):
            attributes=hit['attributes'];ident=str(attributes['OBJECTID'])
            candidates=[hit['record_index']] if 'record_index' in hit else [i for i,r in enumerate(records) if r==attributes]
            matches=[]
            for i in candidates:
                g=geometries[i]
                if g is not None and not g.is_empty and records[i]==attributes and abs(footprint.intersection(g).area-hit['intersection_m2'])<=max(1,hit['intersection_m2']*.001):matches.append(i)
            if not matches:raise ValueError('图层已变化，与原空间结果不符，请重新检索后查看')
            index=matches[0];geometry=geometries[index]
            if any(not geometry.equals(geometries[i]) for i in matches[1:]):raise ValueError('同编号存在多个不同图斑，无法唯一定位，请重新检索后查看')
            intersection=footprint.intersection(geometry)
            if abs(intersection.area-hit['intersection_m2'])>max(1,hit['intersection_m2']*.001):raise ValueError('图层已变化，与原空间结果不符，请重新检索后查看')
            view['hits'].append(dict(id=layer+':'+ident+':'+str(index),source_id=layer+':'+ident,layer=layer,object_id=ident,
                title=attributes.get('PFWH') or attributes.get('PZWH') or attributes.get('BDCDYH') or attributes.get('配准图件') or Path(attributes.get('source') or '').name or '未命名图斑',
                name=attributes.get('XMMC') or attributes.get('PCMC') or attributes.get('QLRMC') or '',
                geometry=mapping(geometry),intersection=mapping(intersection),area_m2=geometry.area,
                manual_verdict=hit.get('manual_verdict'),method=hit.get('method','空间相交'),intersection_m2=hit['intersection_m2'],supply_coverage=hit['supply_coverage'],target_coverage=hit['target_coverage']))
    return view


def write_previews(results,sr,sg,ar,ag,pr,pg,vr=None,vg=None):
    views={str(r['project']):(dict(project=r['project'],supply_pending=True,supply_candidates=[dict(name=c['name'],score=c['score'],view=preview(c['result'],sr,sg,ar,ag,pr,pg,vr,vg)) for c in r['candidates']])
                            if r.get('supply_pending') else preview(r,sr,sg,ar,ag,pr,pg,vr,vg)) for r in results}
    temp=OUT/'spatial_geometry.json.tmp';temp.write_text(json.dumps(views,ensure_ascii=False),encoding='utf-8');temp.replace(OUT/'spatial_geometry.json')


def main():
    supply, sr, sg=load('供地矢量1016','4.供地图层')
    approval, ar, ag=load('批文报批矢量1016','3.报批图层')
    parcel, pr, pg=load('地表宗地','5.用地首登')
    reference=supply.with_suffix('.prj').read_text()
    projected_crs(reference)
    for path,geometries in [(approval,ag),(parcel,pg)]:
        crs=path.with_suffix('.prj').read_text()
        if crs!=reference:
            projected_crs(reference)
            geometries[:]=[reproject(g,crs,reference) if g is not None else None for g in geometries]
    attachment_path=OUT/'attachment_geometry.json'
    attachments=json.loads(attachment_path.read_text(encoding='utf-8')) if attachment_path.exists() else []
    attachments=[a for a in attachments if a['status']=='READY' and shape(a['geometry']).area>0]
    vr=[dict(a['attributes'],source=a['source']) for a in attachments];vg=[shape(a['geometry']) for a in attachments]
    if '--preview-only' in sys.argv:
        write_previews(json.loads((OUT/'spatial_matches.json').read_text(encoding='utf-8')),sr,sg,ar,ag,pr,pg,vr,vg)
        return
    at=STRtree(ag);pt=STRtree(pg);vt=STRtree(vg);results=[]
    def intersect(target,indices,anchor='supply',method='精确供地标识'):
        base_records,base_geometries={'supply':(sr,sg),'approvals':(ar,ag),'parcels':(pr,pg),'attachments':(vr,vg)}[anchor]
        footprint=union_all([base_geometries[i] for i in indices if base_geometries[i] is not None])
        result={'project':target['project'],'reference_crs':reference,'anchor_layer':anchor,'anchor_method':method,'supply_objects':[base_records[i]['OBJECTID'] for i in indices],'supply_indices':indices,'area':footprint.area,'approvals':[],'parcels':[],'attachments':[]}
        for tree,geometries,records,name in [(at,ag,ar,'approvals'),(pt,pg,pr,'parcels'),(vt,vg,vr,'attachments')]:
            key,fields=('approval_id',('PFWH','PZWH')) if name=='approvals' else ('unit_id',('BDCDYH',)) if name=='parcels' else ('regulator_id',('DZJGH',))
            direct={i for i,r in enumerate(records) if geometries[i] is not None and not geometries[i].is_empty and normalize(target.get(key)) and any(normalize(r.get(f))==normalize(target[key]) for f in fields)}
            for i in sorted(set(map(int,tree.query(footprint,predicate='intersects')))|direct):
                intersection=footprint.intersection(geometries[i]).area
                if intersection<max(1,footprint.area*.001) and i not in direct:continue
                result[name].append({'attributes':records[i],'record_index':int(i),'method':('标识一致；' if i in direct else '')+('空间相交' if intersection else '与参考范围不相交，需核实'),'intersection_m2':intersection,'supply_coverage':intersection/footprint.area,'target_coverage':intersection/geometries[i].area if geometries[i].area else 0})
            result[name].sort(key=lambda r:r['intersection_m2'],reverse=True)
        return result
    records=[dict(name=r.get('XMMC'),regulator_id=r.get('DZJGH'),contract_id=r.get('HTHBBH'),address=r.get('TDZL'),area_m2=area_m2(r.get('XMZMJ'),'平方米'),record_index=i) for i,r in enumerate(sr) if sg[i] is not None and not sg[i].is_empty]
    parcels=[dict(name=r.get('XMMC'),unit_id=r.get('BDCDYH'),address=r.get('ZL'),area_m2=area_m2(r.get('ZDMJ'),'平方米'),record_index=i) for i,r in enumerate(pr) if pg[i] is not None and not pg[i].is_empty]
    approvals=[dict(name=r.get('XMMC') or r.get('PCMC'),approval_id=doc,address=r.get('DKZL') or r.get('TDZL') or r.get('ZL'),record_index=i) for i,r in enumerate(ar) if ag[i] is not None and not ag[i].is_empty for doc in (set(filter(None,(r.get('PFWH'),r.get('PZWH')))) or {None})]
    vectors=[dict(name=r.get('XMMC') or r.get('Layer'),regulator_id=r.get('DZJGH'),contract_id=r.get('HTHBBH'),unit_id=r.get('BDCDYH'),address=r.get('TDZL') or r.get('ZL'),area_m2=g.area,record_index=i) for i,(r,g) in enumerate(zip(vr,vg)) if not g.is_empty]
    for target in target_records():
        ranked=rank_candidates(target,records)
        exact=[c for c in ranked if c['exact'] and not c['conflicts']]
        anchor='supply';method='精确供地标识'
        if not exact:
            for layer,items,basis in [('parcels',parcels,'单元号一致'),('approvals',approvals,'批复文号一致（批次范围，非项目独占范围）'),('attachments',vectors,'附件矢量属性标识一致')]:
                hits=[c for c in rank_candidates(target,items) if c['exact'] and not c['conflicts']]
                if hits:exact=hits;anchor=layer;method=basis;break
        if exact:
            result=intersect(target,sorted({c['record']['record_index'] for c in exact}),anchor,method)
            confirmed=any(k not in target.get('inferred_keys',[]) for c in exact for k in c['exact'])
            if confirmed:results.append(result)
            else:results.append(dict(project=target['project'],supply_pending=True,candidates=[dict(name=ANCHOR_NAMES[anchor]+'（识别编号待确认）',score=max(c['score'] for c in exact),result=dict(result,anchor_method='识别编号一致，尚未经人工确认'))]))
        else:
            fuzzy=[]
            for layer,items in [('supply',records),('approvals',approvals),('parcels',parcels),('attachments',vectors)]:
                seen=set()
                for c in rank_candidates(target,items):
                    index=c['record']['record_index']
                    if c['conflicts'] or index in seen:continue
                    seen.add(index)
                    fuzzy.append(dict(name=ANCHOR_NAMES[layer]+'：'+str(c['record'].get('name') or c['record'].get('address') or '未命名'),score=c['score'],result=intersect(target,[c['record']['record_index']],layer,'名称、位置、面积模糊候选，未确认归属')))
                    if len(seen)==5:break
            if fuzzy:results.append(dict(project=target['project'],supply_pending=True,candidates=fuzzy))
        print(target['project'],target.get('name'),'定位图层',anchor,'标识命中',len(exact),'候选',0 if exact else len(fuzzy),flush=True)
    resolve_spatial(results,{'supply':sg,'approvals':ag,'parcels':pg,'attachments':vg},OUT.parent if OUT.name=='work' else OUT)
    (OUT/'spatial_matches.json').write_text(json.dumps(results,ensure_ascii=False),encoding='utf-8')
    write_previews(results,sr,sg,ar,ag,pr,pg,vr,vg)

if __name__=='__main__':main()
