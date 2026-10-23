import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
from shapely.geometry import box,mapping
from address_match import address_match
from match_engine import decide
from entity_evidence import entity_blocks,ocr_identifier_candidates
from georeference import fit_control_points,register_image
from gis_geometry import reproject,cad_entities
from relationship_resolution import resolve_spatial,signature,valid_judgements
from review_service import BASE,HEADERS,write_json,read_json,save_judgement,save_alias,save_relationship_decision,save_coordinate_system,save_georeference
from link_stages import target_records
import extract_fields

class AdvancedMatchingTests(unittest.TestCase):
    def test_alias_coordinate_and_georeference_service_validation(self):
        import openpyxl
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            job=Path(folder);wb=openpyxl.Workbook();wb.active.append([None]);wb.active.append(HEADERS);row=[None]*69;row[23]='当前项目道路工程';wb.active.append(row);wb.save(job/'test_result.xlsx');wb.close()
            image=job/'map.png';Image.new('RGB',(100,100),'white').save(image)
            write_json(job/'review.json',dict(rows=[dict(project='row-3')],sources=dict(s=dict(id='s',label='测试图',path=str(image),kind='png'))))
            save_alias(job,dict(project='row-3',kind='name',alias='历史项目道路工程'))
            self.assertIn('历史项目道路工程',target_records(job/'test_result.xlsx')[0]['name_aliases'])
            save_alias(job,dict(project='row-3',kind='name',alias='历史项目道路工程',remove=True));self.assertEqual(target_records(job/'test_result.xlsx')[0]['name_aliases'],[])
            with self.assertRaises(ValueError):save_alias(job,dict(project='absent',kind='name',alias='错误项目'))
            write_json(job/'work/coordinate_inputs.json',['attachment'])
            save_coordinate_system(job,dict(source='attachment',crs='EPSG:4490'))
            with self.assertRaises(ValueError):save_coordinate_system(job,dict(source='other',crs='EPSG:4490'))
            geometry=mapping(box(0,0,100,100));view=dict(reference_crs='EPSG:3857',supply=geometry,supply_indices=[0],hits=[],anchor_signature=signature(geometry))
            write_json(job/'work/spatial_matches.json',[dict(project='row-3')]);write_json(job/'work/spatial_geometry.json',{'row-3':view})
            save_relationship_decision(job,dict(project='row-3',mode='exclusive',anchor_signature=signature(geometry),note='核实仅属于当前项目'))
            self.assertEqual(read_json(job/'outputs/governance/范围归属确认.json')[0]['mode'],'exclusive')
            result=save_georeference(job,dict(project='row-3',source='s',crs='EPSG:3857',points=[[0,0,0,100],[100,0,100,100],[0,100,0,0],[100,100,100,0]]))
            self.assertLess(result['max_error_m'],1e-6)
            with self.assertRaises(ValueError):save_georeference(job,dict(project='row-3',source='s',crs='EPSG:4490',points=[]))
    def test_direction_semantics_and_conflicting_side(self):
        self.assertEqual(address_match('甲路以东、乙路以西','西至甲路，东临乙路')['score'],1)
        self.assertEqual(address_match('甲路以东','甲路以西')['conflicts'],['甲路'])
        self.assertEqual(decide(dict(contract_id='C',address='甲路以东'),[dict(contract_id='C',address='甲路以西')])[0],'CONFLICT')
        self.assertFalse(address_match('旧甲路以东','甲路以东',{'旧甲路':'甲路'})['conflicts'])
        status,candidates=decide(dict(name='新建道路工程',name_aliases=['旧项目道路工程']),[dict(name='旧项目道路工程')])
        self.assertEqual(candidates[0]['name_score'],1);self.assertNotEqual(status,'MATCH_EXACT')

    def test_rejected_spatial_evidence_clears_old_automatic_patch_cache(self):
        import openpyxl
        import build_patches
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            job=Path(folder);work=job/'work';work.mkdir();out=job/'outputs/governance'
            wb=openpyxl.Workbook();wb.active.append([None]);wb.active.append(HEADERS);row=[None]*69;row[1]='A';row[23]='测试道路工程';row[60]=100;row[61]='平方米';wb.active.append(row);wb.save(job/'test_result.xlsx');wb.close()
            hit=dict(attributes=dict(OBJECTID='1',PFWH='赣府〔2020〕1号',PZSJ='20200101'),supply_coverage=1)
            result=dict(project='row-3',area=100,approvals=[hit])
            write_json(work/'spatial_matches.json',[result]);write_json(work/'6.用地规划_台账.json',[[None]*19]);previous=Path.cwd()
            try:
                os.chdir(job)
                with patch.multiple(build_patches,OUT=work,DEST=out):build_patches.main()
                self.assertIn('AZ3',[p['cell'] for p in read_json(work/'search_patches.json')])
                result['approvals'][0]['manual_verdict']='不一致';write_json(work/'spatial_matches.json',[result])
                with patch.multiple(build_patches,OUT=work,DEST=out):build_patches.main()
                self.assertNotIn('AZ3',[p['cell'] for p in read_json(work/'search_patches.json')])
            finally:os.chdir(previous)

    def test_entity_and_table_rows_do_not_mix_fields(self):
        text='建设项目选址意见书\n项目名称：甲方道路工程；拟用地面积：10亩；项目名称：乙方道路工程；拟用地面积：20亩；'
        blocks=entity_blocks(text);self.assertEqual(len(blocks),2)
        self.assertNotIn('20亩',blocks[0]['text']);self.assertNotIn('10亩',blocks[1]['text'])
        table=entity_blocks('建设项目选址意见书\n项目名称\t拟用地面积\t面积单位\n甲方道路工程\t10\t亩\n乙方道路工程\t20\t亩')
        self.assertEqual(len(table),2);self.assertIn('拟用地面积：20亩',table[1]['text'])

    def test_layout_splits_columns(self):
        words=[]
        for x,name in [(0,'甲方道路工程'),(400,'乙方道路工程')]:
            words.extend([dict(text='项目名称：'+name,x=x,y=0),dict(text='拟用地面积：10亩；',x=x,y=40)])
        blocks=entity_blocks('交错正文',dict(words=words))
        self.assertEqual(len(blocks),2);self.assertNotIn('乙方',blocks[0]['text'])

    def test_ocr_correction_is_unique_scoped_and_keeps_literal_conflict(self):
        target=dict(contract_id='H360202001000012');fields={}
        corrections=ocr_identifier_candidates('合同编号：H36O202001000012；',[target],fields)
        self.assertEqual(fields['contract_id'],[target['contract_id']]);self.assertEqual(corrections[0]['original'],'H36O202001000012')
        fields={'contract_id':['OTHER']};self.assertEqual(ocr_identifier_candidates('H36O202001000012',[target],fields),[])
        self.assertEqual(ocr_identifier_candidates('H36O202001000012',[target,dict(contract_id='H36O2O2001000012')],{}),[])

    def test_segments_reach_different_projects(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);work=root/'work';work.mkdir();out=root/'outputs/governance'
            targets=[dict(project='A',name='甲方道路工程'),dict(project='B',name='乙方道路工程')]
            write_json(work/'ocr_manifest.json',[])
            write_json(work/'pdf_text.json',[dict(project='unassigned',source='document.pdf',page=1,text='建设项目选址意见书\n项目名称：甲方道路工程；拟用地面积：10亩；项目名称：乙方道路工程；拟用地面积：20亩；')])
            before=Path.cwd()
            try:
                os.chdir(root)
                with patch.multiple(extract_fields,OUT=work,DEST=out),patch('extract_fields.target_records',return_value=targets):extract_fields.main()
            finally:os.chdir(before)
            records=[json.loads(line) for line in (out/'识别字段候选.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([r['projects'] for r in records],[['A'],['B']])
            self.assertEqual([[c['value'] for c in r['field_candidates'] if c['column']==32] for r in records],[[10],[20]])

    def test_reprojection_and_invalid_crs_fail_explicitly(self):
        original=box(115.8,28.7,115.801,28.701)
        transformed=reproject(original,'EPSG:4490','EPSG:4548')
        self.assertGreater(transformed.area,1000)
        self.assertAlmostEqual(reproject(transformed,'EPSG:4548','EPSG:4490').bounds[0],original.bounds[0],places=7)
        with self.assertRaises(Exception):reproject(original,'LOCAL_CS["unknown"]','EPSG:4548')

    def test_cad_entities_and_closed_boundary(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            path=Path(folder)/'boundary.dxf'
            path.write_text('0\nSECTION\n2\nENTITIES\n0\nLWPOLYLINE\n8\nboundary\n90\n4\n70\n1\n10\n0\n20\n0\n10\n10\n20\n0\n10\n10\n20\n10\n10\n0\n20\n10\n0\nENDSEC\n0\nEOF\n',encoding='ascii')
            entities,polygons=cad_entities(path)
            self.assertGreater(len(entities),0);self.assertEqual(sum(p.area for a,p in polygons),100)

    def test_georeference_controls_error_and_red_boundary(self):
        points=[[0,0,1000,2000],[200,0,1400,2000],[0,200,1000,1600],[200,200,1400,1600]]
        fit=fit_control_points(points,200,200);self.assertLess(fit['max_error_m'],1e-6)
        with self.assertRaises(ValueError):fit_control_points([[0,0,0,0]]*4,200,200)
        bad=copy.deepcopy(points);bad[-1][2]+=50
        with self.assertRaises(ValueError):fit_control_points(bad,200,200,2)
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);image=Image.new('RGB',(200,200),'white');ImageDraw.Draw(image).rectangle((20,20,180,180),outline='red',width=3);image.save(root/'map.png')
            result=register_image(root/'map.png',points,'EPSG:3857',root/'registered')
            self.assertGreater(result['boundary_candidates'],0);self.assertGreater(result['boundaries'][0]['properties']['area_m2'],90000)
            self.assertTrue((root/'registered/配准原图.pgw').is_file())

    def test_multi_batch_union_and_global_shared_parcel(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);layers=dict(supply=[box(0,0,10,10)],approvals=[box(0,0,7,10),box(5,0,10,10)],parcels=[box(0,0,10,10)],attachments=[])
            hit=lambda i,layer:dict(attributes=dict(OBJECTID=str(i),PFWH='批文'+str(i)) if layer=='approvals' else dict(OBJECTID=str(i),BDCDYH='shared'),record_index=i)
            results=[dict(project=p,anchor_layer='supply',supply_indices=[0],approvals=[hit(0,'approvals'),hit(1,'approvals')],parcels=[hit(0,'parcels')]) for p in ('A','B')]
            report=resolve_spatial(results,layers,root)
            self.assertEqual(results[0]['resolution']['approval_union_coverage'],1)
            self.assertEqual(results[0]['resolution']['approval_overlap_m2'],20)
            self.assertFalse(results[0]['resolution']['automatic_allowed']);self.assertEqual(report['components'],[['A','B']])
            write_json(root/'outputs/governance/范围归属确认.json',[dict(project=p,anchor_signature=results[0]['anchor_signature'],mode='shared') for p in ('A','B')])
            report=resolve_spatial(results,layers,root)
            # Shared supply is resolved; shared parcel still requires its own scope confirmation.
            self.assertTrue(any(c['status']=='resolved_shared' for c in report['conflicts']))
            write_json(root/'outputs/governance/范围归属确认.json',[dict(project=p,anchor_signature=results[0]['anchor_signature'],mode='shared',geometry_signatures={'parcels:0:0':signature(mapping(layers['parcels'][0]))}) for p in ('A','B')])
            report=resolve_spatial(results,layers,root)
            self.assertEqual(report['components'],[]);self.assertTrue(all(r['resolution']['automatic_allowed'] for r in results))
            write_json(root/'outputs/governance/范围归属确认.json',[dict(project='A',anchor_signature=results[0]['anchor_signature'],mode='exclusive')])
            resolve_spatial(results,layers,root)
            self.assertTrue(results[1]['resolution']['excluded']);self.assertFalse(results[1]['resolution']['automatic_allowed'])

    def test_visual_feedback_is_version_bound_and_removes_rejected_hit(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);image=root/'page.png';Image.new('RGB',(10,10)).save(image)
            footprint=box(0,0,10,10);geometry=mapping(footprint)
            view=dict(supply=geometry,supply_indices=[0],hits=[dict(id='approvals:a:0',geometry=geometry)])
            write_json(root/'review.json',dict(rows=[dict(project='P')],sources=dict(s=dict(label='source.pdf#page=1',path=str(image)))))
            write_json(root/'work/spatial_matches.json',[dict(project='P')]);write_json(root/'work/spatial_geometry.json',dict(P=view))
            save_judgement(root,dict(project='P',source='s',supply_indices=[0],hits=['approvals:a:0'],verdict='不一致',note='本图与该批次范围不一致'))
            self.assertEqual(len(valid_judgements(root)),1)
            result=dict(project='P',supply_indices=[0],approvals=[dict(attributes=dict(OBJECTID='a'),record_index=0)],parcels=[])
            resolve_spatial([result],dict(supply=[footprint],approvals=[footprint],parcels=[],attachments=[]),root)
            self.assertEqual(result['approvals'][0]['manual_verdict'],'不一致');self.assertEqual(result['groups'],[])
            image.write_bytes(b'changed');self.assertEqual(valid_judgements(root),[])

if __name__=='__main__':unittest.main()
