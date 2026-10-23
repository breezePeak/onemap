import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from shapely.geometry import box,shape,Polygon,MultiPolygon
from spatial_matches import preview
from review_service import BASE,write_json,spatial_preview
import spatial_matches


class SpatialPreviewTests(unittest.TestCase):
    def test_direct_unit_and_approval_lookup_without_supply(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);path=root/'layer.shp';path.with_suffix('.prj').write_text('EPSG:3857')
            approvals=[dict(OBJECTID='a',PFWH='赣府〔2020〕1号',PCMC='华佗路建设批次')]
            parcels=[dict(OBJECTID='p',BDCDYH='unit-1')]
            targets=[dict(project='row-3',unit_id='unit-1'),dict(project='row-4',approval_id='赣府[2020]1号'),dict(project='row-5',name='华佗路建设批次')]
            data=[(path,[],[]),(path,approvals,[box(0,0,20,20)]),(path,parcels,[box(0,0,10,10)])]
            with patch.multiple(spatial_matches,OUT=root),patch('spatial_matches.load',side_effect=data),patch('spatial_matches.target_records',return_value=targets),patch('sys.argv',['spatial_matches.py']):spatial_matches.main()
            results=__import__('json').loads((root/'spatial_matches.json').read_text(encoding='utf-8'))
            self.assertEqual(results[0]['anchor_layer'],'parcels')
            self.assertEqual(results[0]['approvals'][0]['intersection_m2'],100)
            self.assertEqual(results[1]['anchor_layer'],'approvals')
            self.assertEqual(results[1]['parcels'][0]['target_coverage'],1)
            self.assertTrue(results[2]['supply_pending'])
            views=__import__('json').loads((root/'spatial_geometry.json').read_text(encoding='utf-8'))
            self.assertEqual(views['row-3']['anchor_name'],'首登宗地范围')
            self.assertEqual(views['row-4']['anchor_name'],'报批批次范围')

    def test_direct_document_hit_survives_disjoint_geometry(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);path=root/'layer.shp';path.with_suffix('.prj').write_text('EPSG:3857')
            supply=[dict(OBJECTID='s',HTHBBH='contract')];approvals=[dict(OBJECTID='a',PFWH='approval')]
            data=[(path,supply,[box(0,0,10,10)]),(path,approvals,[box(100,100,110,110)]),(path,[],[])]
            with patch.multiple(spatial_matches,OUT=root),patch('spatial_matches.load',side_effect=data),patch('spatial_matches.target_records',return_value=[dict(project='row-3',contract_id='contract',approval_id='approval')]),patch('sys.argv',['spatial_matches.py']):spatial_matches.main()
            result=__import__('json').loads((root/'spatial_geometry.json').read_text(encoding='utf-8'))['row-3']
            self.assertEqual(result['hits'][0]['intersection_m2'],0)
            self.assertIn('不相交',result['hits'][0]['method'])
            data=[(path,supply,[box(0,0,10,10)]),(path,approvals,[box(100,100,110,110)]),(path,[],[])]
            with patch.multiple(spatial_matches,OUT=root),patch('spatial_matches.load',side_effect=data),patch('spatial_matches.target_records',return_value=[dict(project='row-3',contract_id='contract',inferred_keys=['contract_id'])]),patch('sys.argv',['spatial_matches.py']):spatial_matches.main()
            result=__import__('json').loads((root/'spatial_matches.json').read_text(encoding='utf-8'))[0]
            self.assertTrue(result['supply_pending'])

    def test_missing_supply_identifiers_generate_separate_fuzzy_views(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);path=root/'layer.shp';path.with_suffix('.prj').write_text('EPSG:3857')
            supply=[dict(OBJECTID='1',XMMC='华佗路道路工程',TDZL='赣江新区',XMZMJ='100',DZJGH='',HTHBBH=''),dict(OBJECTID='2',XMMC='华佗路道路改造工程',TDZL='赣江新区',XMZMJ='100',DZJGH='',HTHBBH='')]
            target=dict(project='row-3',name='华佗路道路工程',address='赣江新区',area_m2=100)
            data=[(path,supply,[box(0,0,10,10),box(20,0,30,10)]),(path,[],[]),(path,[],[])]
            with patch.multiple(spatial_matches,OUT=root),patch('spatial_matches.load',side_effect=data),patch('spatial_matches.target_records',return_value=[target]),patch('sys.argv',['spatial_matches.py']):spatial_matches.main()
            result=__import__('json').loads((root/'spatial_matches.json').read_text(encoding='utf-8'))[0]
            self.assertTrue(result['supply_pending']);self.assertEqual(len(result['candidates']),2)
            views=__import__('json').loads((root/'spatial_geometry.json').read_text(encoding='utf-8'))['row-3']['supply_candidates']
            self.assertEqual([v['view']['supply_indices'] for v in views],[[0],[1]])

    def test_true_intersection_and_both_layers(self):
        supply=box(0,0,10,10);target=box(5,0,15,10)
        attributes={'OBJECTID':'7','PFWH':'批文A'}
        hit=dict(attributes=attributes,intersection_m2=50,supply_coverage=.5,target_coverage=.5)
        result=dict(project='P',supply_objects=['1'],approvals=[hit],parcels=[dict(hit,attributes={'OBJECTID':'9','BDCDYH':'宗地B'})])
        data=preview(result,[{'OBJECTID':'1'}],[supply],[attributes],[target],[{'OBJECTID':'9','BDCDYH':'宗地B'}],[target])
        self.assertEqual(len(data['hits']),2)
        self.assertEqual(shape(data['hits'][0]['intersection']).area,50)
        self.assertEqual(data['hits'][1]['id'],'parcels:9:0')
        self.assertEqual(data['hits'][0]['supply_coverage'],.5)
        with self.assertRaisesRegex(ValueError,'图层已变化'):
            preview(result,[{'OBJECTID':'1'}],[supply],[attributes],[box(0,0,10,10)],[{'OBJECTID':'9','BDCDYH':'宗地B'}],[target])

    def test_holes_and_multiple_supply_parts_are_preserved(self):
        polygon=Polygon([(0,0),(10,0),(10,10),(0,10)],holes=[[(2,2),(8,2),(8,8),(2,8)]])
        result=dict(project='P',supply_objects=['1'],approvals=[],parcels=[])
        data=preview(result,[{'OBJECTID':'1'}],[MultiPolygon([polygon,box(20,20,21,21)])],[],[],[],[])
        self.assertEqual(shape(data['supply']).area,65)
        self.assertEqual(data['supply']['type'],'MultiPolygon')

    def test_duplicate_object_ids_do_not_select_wrong_geometry(self):
        attrs={'OBJECTID':'0','PFWH':'same-document'}
        hit=dict(attributes=attrs,intersection_m2=50,supply_coverage=.5,target_coverage=.5)
        result=dict(project='P',supply_objects=['1'],approvals=[hit],parcels=[])
        data=preview(result,[{'OBJECTID':'1'}],[box(0,0,10,10)],[attrs,attrs],[box(5,0,15,10),box(100,100,110,110)],[],[])
        self.assertEqual(data['hits'][0]['id'],'approvals:0:0')
        self.assertEqual(shape(data['hits'][0]['intersection']).area,50)

    def test_old_task_builds_preview_once_without_touching_choices(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            job=Path(folder)
            write_json(job/'work/spatial_matches.json',[dict(project='P')])
            write_json(job/'review.json',dict(decisions={'AZ3':'chosen'}))
            before=(job/'review.json').read_bytes()
            def build(*args):write_json(job/'work/spatial_geometry.json',{'P':{'hits':[]}})
            with patch('review_service.run_step',side_effect=build) as run:
                self.assertEqual(spatial_preview(job,'P'),{'hits':[]})
                spatial_preview(job,'P');run.assert_called_once_with(job,'spatial_matches.py','--preview-only')
                with self.assertRaises(ValueError):spatial_preview(job,'other')
            self.assertEqual((job/'review.json').read_bytes(),before)


if __name__=='__main__':unittest.main()
