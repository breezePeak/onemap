"""盘点附件 SHP 及其组成文件，输出待导入副本和缺图层/缺监管号清单。"""
from collections import defaultdict
import io
import json
from pathlib import Path
import shutil
import shapefile
from shapely.geometry import shape,mapping
from shapely.ops import polygonize
from gis_geometry import reproject,cad_entities
from cross_stage_materials import path_projects,search_terms,read_json,SENSITIVE
from extract_archives import RecordedZip,decoded_name
from material_archives import open_archive
from governance_outputs import safe_name,table
from link_stages import target_records
from match_engine import normalize


COMPONENTS={'.shp','.shx','.dbf','.prj','.cpg','.sbn','.sbx'}


def attribute_projects(records,targets):
    result=set()
    for record in records:
        for target in targets:
            pairs=(('regulator_id','DZJGH'),('contract_id','HTHBBH'),('unit_id','BDCDYH'))
            exact=any(normalize(target.get(k)) and normalize(target[k])==normalize(record.get(col)) for k,col in pairs)
            conflict=any(normalize(target.get(k)) and normalize(record.get(col)) and normalize(target[k])!=normalize(record[col]) for k,col in pairs)
            if exact and not conflict:result.add(target['project'])
    return result


def build(job):
    job=Path(job);work=job/'work';out=job/'outputs/governance';out.mkdir(parents=True,exist_ok=True)
    targets=target_records(job/'test_result.xlsx');terms=search_terms(targets,work,out)
    inventory=[Path(x['path']) for x in read_json(work/'files.json',[])]
    rows=[];errors=[];attached=set();confirmed=set()
    geometry_features=[];cad_rows=[];cad_status=[];coordinate_pending=[]
    reference_path=__import__('inspect_sources').ROOT/'数据图层/4.供地图层/供地矢量1016.prj'
    reference=reference_path.read_text() if reference_path.exists() else None
    overrides=read_json(work/'coordinate_systems.json',{})
    def geometries(source,polygons,crs,projects,exact,partial=False):
        import hashlib
        for i,(attrs,g) in enumerate(polygons):
            if g.is_empty or not g.area>0:continue
            status='PARTIAL' if partial else 'READY' if crs and reference else 'CRS_UNKNOWN'
            ident=hashlib.sha256((source+'|'+str(i)).encode()).hexdigest()[:20]
            converted=reproject(g,crs,reference) if crs and reference else g
            geometry_features.append(dict(id=ident,source=source,attributes=dict(attrs,OBJECTID=ident),geometry=mapping(converted),source_crs=crs,
                                          reference_crs=reference,status=status,projects=sorted(projects),exact_projects=sorted(exact)))
        if not crs:coordinate_pending.append([source,'未声明坐标系；请在工作台指定实际坐标系，不能按位置猜测'])
    def collect(source,components,read):
        missing=sorted({'.shp','.shx','.dbf','.prj'}-components.keys())
        projects=set(path_projects(source,targets,terms));records=[];exact=set()
        try:
            if {'.shp','.shx','.dbf'}<=components.keys():
                data={ext:read(components[ext]) for ext in ('.shp','.shx','.dbf')}
                for encoding in ('utf-8','gb18030'):
                    try:
                        with shapefile.Reader(shp=io.BytesIO(data['.shp']),shx=io.BytesIO(data['.shx']),dbf=io.BytesIO(data['.dbf']),encoding=encoding) as reader:
                            records=[r.as_dict() for r in reader.records()]
                            shapes=reader.shapes()
                            if len(records)!=len(shapes):raise ValueError('DBF含删除记录，须核实物理序号，不按错位记录挂接')
                            polygons=[]
                            for attrs,g in zip(records,shapes):
                                if not g.shapeType:continue
                                geometry=shape(g.__geo_interface__)
                                if geometry.geom_type in ('Polygon','MultiPolygon'):polygons.append((attrs,geometry))
                                elif geometry.geom_type in ('LineString','MultiLineString'):polygons.extend((attrs,p) for p in polygonize([geometry]))
                        break
                    except UnicodeError:
                        if encoding=='gb18030':raise
                exact=attribute_projects(records,targets);projects.update(exact)
                for record in records:
                    projects.update(path_projects(' '.join(str(record.get(k) or '') for k in ('XMMC','ZL','TDZL')),targets,terms))
            names=[t.get('name') or t['project'] for t in targets if t['project'] in projects]
            folder=out/'附件矢量'/safe_name(names[0] if len(names)==1 else '多项目待核对' if names else '未关联附件')
            # 同名附件可以来自不同项目，使用来源指纹避免互相覆盖。
            import hashlib
            folder=folder/hashlib.sha256(source.encode()).hexdigest()[:12];folder.mkdir(parents=True,exist_ok=True)
            for ext,value in components.items():(folder/('附件'+ext)).write_bytes(read(value))
            status='缺组成文件：'+','.join(missing) if missing else '组成文件齐全，坐标及项目归属待核对后导入'
            if not missing:attached.update(projects)
            if records:
                crs=overrides.get(source) or (read(components['.prj']).decode('utf-8-sig') if '.prj' in components else None)
                geometries(source,polygons,crs,projects,exact)
                if not missing and any(f['source']==source and f['status']=='READY' for f in geometry_features):confirmed.update(exact)
                if not polygons:coordinate_pending.append([source,'附件无面或闭合线边界，不能作为用地范围'])
            rows.append([source,'；'.join(sorted(projects)),len(records),'；'.join(sorted(components)),str(folder.relative_to(out)),status])
        except Exception as error:errors.append(dict(source=source,error=str(error)));rows.append([source,'；'.join(sorted(projects)),None,None,None,'解析失败：'+str(error)])
    groups=defaultdict(dict)
    for path in inventory:
        if path.suffix.lower() in COMPONENTS and not SENSITIVE.search(str(path)) and '数据图层' not in path.parts:groups[str(path.with_suffix(''))][path.suffix.lower()]=path
    for source,components in groups.items():
        if '.shp' in components:collect(source,components,lambda path:path.read_bytes())
    def collect_cad(source,path):
        try:
            import hashlib
            fingerprint=hashlib.sha256(Path(path).read_bytes()+b'libredwg-0.14-gdal-3.9-cad-v2').hexdigest()
            cache=work/'cad_entity_cache'/(fingerprint+'.json');stored=read_json(cache,None)
            if stored is None:
                entities,polygons=cad_entities(path)
                cache.parent.mkdir(exist_ok=True)
                cache.write_text(json.dumps(dict(entities=entities,polygons=[dict(attributes=a,geometry=mapping(g)) for a,g in polygons]),ensure_ascii=False),encoding='utf-8')
            else:entities=stored['entities'];polygons=[(p['attributes'],shape(p['geometry'])) for p in stored['polygons']]
            partial=any(e.get('read_warning') for e in entities)
            if partial:errors.append(dict(source=source,error='部分实体读取失败，保留已读实体但不参与自动空间匹配：'+'；'.join(e['read_warning'] for e in entities if e.get('read_warning'))))
            projects=set(path_projects(source,targets,terms))
            for entity in entities:cad_rows.append([source,entity['id'],entity['layer'],entity['geometry']['type'],json.dumps(entity['attributes'],ensure_ascii=False)])
            prj=Path(path).with_suffix('.prj')
            crs=overrides.get(source) or (prj.read_text(encoding='utf-8-sig') if prj.exists() else None)
            geometries(source,polygons,crs,projects,set(),partial)
            cad_status.append([source,len(entities),len(polygons),'部分读取，禁止自动空间采用' if partial else '未取得实体，待原CAD核查' if not entities else '已读实体，缺坐标系' if not crs else '已读取并转换坐标'])
            folder=out/'CAD实体';folder.mkdir(exist_ok=True)
            import hashlib
            (folder/(hashlib.sha256(source.encode()).hexdigest()[:16]+'.geojson')).write_text(json.dumps(dict(type='FeatureCollection',features=[dict(type='Feature',properties=e['attributes'],geometry=e['geometry']) for e in entities]),ensure_ascii=False),encoding='utf-8')
            if not polygons:coordinate_pending.append([source,'已读取实体，未找到可闭合的面边界；不能作为用地范围' if entities else '转换后无可读取实体，可能含外参或代理对象；需使用原CAD软件核实'])
        except Exception as error:errors.append(dict(source=source,error=str(error)));cad_status.append([source,None,None,'读取失败，原件保留，详见矢量整理统计.json'])
    for path in inventory:
        if path.suffix.lower() in ('.dwg','.dxf') and not SENSITIVE.search(str(path)):collect_cad(str(path),path)
    for archive in inventory:
        if archive.suffix.lower() not in ('.zip','.rar'):continue
        try:
            with open_archive(archive) as z:
                groups=defaultdict(dict)
                for info in z.infolist():
                    name=decoded_name(info);ext=Path(name).suffix.lower()
                    if ext in COMPONENTS and not SENSITIVE.search(name):groups[str(Path(name).with_suffix(''))][ext]=info
                for name,components in groups.items():
                    if '.shp' not in components:continue
                    if any(i.file_size>100*1024*1024 for i in components.values()):
                        errors.append(dict(source=str(archive)+'!'+name,error='单个矢量组成文件超过100MB，待单独处理'));continue
                    def read_member(info):
                        with z.open(info) as stream:return stream.read()
                    collect(str(archive)+'!'+name,components,read_member)
                import tempfile
                for info in z.infolist():
                    name=decoded_name(info)
                    if Path(name).suffix.lower() not in ('.dwg','.dxf') or SENSITIVE.search(name):continue
                    if info.file_size>100*1024*1024:errors.append(dict(source=str(archive)+'!'+name,error='CAD超过100MB'));continue
                    with tempfile.TemporaryDirectory(dir=work) as temp:
                        path=Path(temp)/('drawing'+Path(name).suffix.lower())
                        with z.open(info) as stream:path.write_bytes(stream.read(100*1024*1024+1))
                        if path.stat().st_size>100*1024*1024:raise ValueError('CAD超过100MB')
                        collect_cad(str(archive)+'!'+name,path)
        except Exception as error:errors.append(dict(source=str(archive),error=str(error)))
    for registration in read_json(out/'图件配准.json',[]):
        path=Path(registration['source_path'])
        import hashlib
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=registration['source_signature']:continue
        polygons=[(dict(配准图件=registration['source_label']),shape(f['geometry'])) for f in registration.get('boundaries',[])]
        geometries('图件配准:'+registration['id'],polygons,registration['crs'],{registration['project']},set())
    supply=read_json(work/'供地矢量1016.json',[]);missing=[];regulator=[]
    for target in targets:
        exact=attribute_projects(supply,[target])
        if not exact and target['project'] not in confirmed:missing.append([target['project'],target['archive_id'],target.get('name'),'存在完整附件SHP候选，项目归属待核实' if target['project'] in attached else '无精确匹配的供地图斑及完整附件SHP；模糊候选另行核对'])
        for record in supply:
            if normalize(record.get('DZJGH')) or not target.get('regulator_id'):continue
            hints=path_projects(' '.join(str(record.get(k) or '') for k in ('XMMC','TDZL')),[target],{target['project']:set()})
            if hints:regulator.append([record.get('OBJECTID'),target['project'],target.get('name'),target['regulator_id'],'名称/位置候选，需核对材料和图斑后补齐；未修改原图层'])
    table(out/'附件矢量清单.xlsx',['原始来源','候选项目','记录数','组成文件','待导入目录','状态'],rows)
    table(out/'缺少SHP项目.xlsx',['内部行标识','档案编号','项目名称','状态'],missing)
    table(out/'监管号补齐候选.xlsx',['图斑OBJECTID','候选项目','项目名称','建议监管号','核对要求'],regulator)
    table(out/'CAD实体清单.xlsx',['原始来源','实体编号','CAD图层','几何类型','属性'],cad_rows)
    table(out/'CAD读取状态.xlsx',['原始来源','已读实体数','闭合面数','状态'],cad_status)
    table(out/'矢量坐标待确认.xlsx',['原始来源','待办'],coordinate_pending)
    (work/'attachment_geometry.json').write_text(json.dumps(geometry_features,ensure_ascii=False),encoding='utf-8')
    (work/'coordinate_inputs.json').write_text(json.dumps(sorted({r['source'] for r in geometry_features}|{r[0] for r in coordinate_pending}),ensure_ascii=False),encoding='utf-8')
    report=dict(attachment_groups=len(rows),missing_shp_projects=len(missing),regulator_candidates=len(regulator),cad_files=len(cad_status),cad_entities=len(cad_rows),geometry_status_counts={s:sum(r['status']==s for r in geometry_features) for s in sorted({r['status'] for r in geometry_features})},errors=errors)
    (out/'矢量整理统计.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':print(json.dumps(build(Path.cwd()),ensure_ascii=False))
