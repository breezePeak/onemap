import json
from pathlib import Path
import tempfile
import unittest
import os
import subprocess
from unittest.mock import patch
import openpyxl
from pypdf import PdfWriter
from cross_stage_materials import contains_token,discover_files,search_terms,path_projects
from material_evidence import page_evidence,field_candidates,material_stage
from review_service import BASE,HEADERS,NODE,collect_review,resolve_review,write_json
from extract_pdfs import read_pdf_text
import expand_materials
import extract_fields


TARGETS=[dict(project='2020-001',name='华佗路道路工程',project_code=None,regulator_id='3601002020A00636',contract_id='H360202001000012',unit_id=None),
         dict(project='2020-002',name='博吾路道路工程',project_code=None,regulator_id='3601002020A00656',contract_id='H360202001000013',unit_id=None)]


class EvidenceTests(unittest.TestCase):
    def test_supply_identifier_associates_page(self):
        r=page_evidence('合同编号 H360202001000012',TARGETS)
        self.assertEqual(r['projects'],['2020-001'])

    def test_other_project_on_same_page_blocks_assignment(self):
        r=page_evidence('华佗路道路工程，博吾路道路工程 H360202001000012',TARGETS)
        self.assertEqual(r['projects'],[])

    def test_conflicting_contract_blocks_assignment(self):
        self.assertEqual(page_evidence('华佗路道路工程 H360202001000013',TARGETS)['projects'],[])

    def test_folder_is_not_evidence(self):
        self.assertEqual(page_evidence('用地面积20亩',TARGETS)['projects'],[])

    def test_supply_short_name_requires_project_name_label(self):
        targets=[dict(TARGETS[0],name_aliases=['华佗路'])]
        self.assertEqual(page_evidence('项目名称：华佗路；拟用地面积20亩',targets)['projects'],['2020-001'])
        self.assertEqual(page_evidence('建设地点：华佗路以东；拟用地面积20亩',targets)['projects'],[])

    def test_same_name_shared_code_does_not_disambiguate(self):
        targets=[dict(t,project_code='2019-360090-48-01-020404') for t in TARGETS]
        self.assertEqual(page_evidence('2019-360090-48-01-020404',targets)['projects'],[])

    def test_even_single_target_code_is_only_retrieval_evidence(self):
        targets=[dict(TARGETS[0],project_code='2019-360090-48-01-020404')]
        self.assertEqual(page_evidence('其他道路项目 2019-360090-48-01-020404',targets)['projects'],[])

    def test_ocr_code_separators_are_normalized_without_losing_evidence(self):
        text='华佗路道路工程 项目代码2019一360090一48一01一020404'
        targets=[dict(TARGETS[0],project_code='2019-360090-48-01-020404')]
        evidence=page_evidence(text,targets)
        self.assertEqual(evidence['projects'],['2020-001'])
        candidates=field_candidates(text,evidence['fields'])
        self.assertEqual(candidates[0]['value'],'2019-360090-48-01-020404')
        self.assertIn('一',candidates[0]['evidence'])

    def test_certificate_alphanumeric_suffix_is_preserved(self):
        text='建设项目用地预审与选址意见书 用字第36010020220011 GJXQZGQ 号'
        evidence=page_evidence(text,TARGETS)
        self.assertEqual(evidence['fields']['certificate_id'],['用字第36010020220011GJXQZGQ号'])
        self.assertIn('用字第36010020220011GJXQZGQ号',[c['value'] for c in field_candidates(text,evidence['fields'])])

    def test_overlong_certificate_is_not_truncated(self):
        self.assertEqual(page_evidence('用字第'+'1'*29+'号',TARGETS)['fields'].get('certificate_id',[]),[])

    def test_specific_contract_disambiguates_shared_code(self):
        targets=[dict(t,project_code='2019-360090-48-01-020404') for t in TARGETS]
        self.assertEqual(page_evidence('2019-360090-48-01-020404 H360202001000012',targets)['projects'],['2020-001'])

    def test_selection_area_maps_only_to_selection(self):
        text='建设项目选址意见书 华佗路道路工程 拟用地面积：22.17亩 发证日期：2020年2月3日'
        evidence=page_evidence(text,TARGETS)
        fields=field_candidates(text,evidence['fields'])
        self.assertIn((32,22.17),[(c['column'],c['value']) for c in fields])
        self.assertIn((36,'2020-02-03'),[(c['column'],c['value']) for c in fields])
        self.assertFalse(any(c['column'] in (40,48,61) for c in fields))

    def test_supply_area_does_not_become_selection_area(self):
        text='国有土地使用权出让合同 依据建设用地规划条件 宗地面积：100平方米'
        self.assertEqual(material_stage(text),'土地供应')
        self.assertEqual({c['column'] for c in field_candidates(text,{})},{61,62})

    def test_invalid_date_is_not_candidate(self):
        self.assertEqual(field_candidates('建设项目选址意见书 发证日期：2020年2月31日',{}),[])

    def test_text_labels_map_to_their_material_stage(self):
        fields=field_candidates('建设用地规划条件；用地性质：住宅用地；用地位置：赣江新区。',{})
        self.assertEqual([(c['column'],c['value']) for c in fields],[(41,'住宅用地'),(42,'赣江新区')])

    def test_text_field_does_not_swallow_next_label(self):
        self.assertEqual(field_candidates('建设用地规划条件；用地性质：住宅用地用地位置：赣江新区。',{}),
                         [dict(column=42,value='赣江新区',evidence='用地位置:赣江新区',type=None,material_stage='规划条件与红线')])

    def test_bare_area_or_date_without_title_is_not_mapped(self):
        self.assertEqual(field_candidates('宗地面积100平方米 2020年2月3日',{}),[])

    def test_area_units_survive_unicode_normalization(self):
        self.assertIn((46,'平方米'),[(c['column'],c['value']) for c in field_candidates('建设用地规划条件；用地面积100㎡',{})])
        self.assertIn((46,'万平方米'),[(c['column'],c['value']) for c in field_candidates('建设用地规划条件；用地面积1.5万平方米',{})])

    def test_zero_and_nonfinite_area_are_not_candidates(self):
        self.assertEqual(field_candidates('建设用地规划条件；用地面积0亩',{}),[])
        self.assertEqual(field_candidates('建设用地规划条件；用地面积'+'9'*400+'亩',{}),[])

    def test_archive_id_boundary(self):
        self.assertTrue(contains_token('2020-001 项目/文件.pdf','2020-001'))
        self.assertFalse(contains_token('2020-0019 项目/文件.pdf','2020-001'))

    def test_boundary_descriptions_are_retained_as_retrieval_hints(self):
        terms={t['project']:{'华佗路' if i==0 else '博吾路'} for i,t in enumerate(TARGETS)}
        self.assertEqual(path_projects('地块/华佗路以东、博吾路西侧/合同.pdf',TARGETS,terms),['2020-001','2020-002'])
        self.assertEqual(path_projects('华佗路道路工程/合同.pdf',TARGETS,terms),['2020-001'])


class DiscoveryTests(unittest.TestCase):
    def test_fuzzy_names_and_addresses_find_files_without_relaxing_identifiers(self):
        targets=[dict(TARGETS[0],address='赣江新区中医药大道')]
        terms={targets[0]['project']:{targets[0]['contract_id']}}
        for label in ('用地规划/华陀路道路工程/合同.pdf','用地规划.zip!华佗路道路改造工程/意见书.pdf',
                      '用地首登/赣江新区中医药大街/规划条件.pdf'):
            self.assertEqual(path_projects(label,targets,terms),['2020-001'])
        self.assertEqual(path_projects('其他项目/H360202001000013/合同.pdf',targets,terms),[])
        self.assertEqual(page_evidence('建设地点：华陀路以东；拟用地面积20亩',targets)['projects'],[])
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);work=root/'work';work.mkdir()
            material=root/'6.用地规划/华陀路道路工程/合同.pdf'
            material.parent.mkdir(parents=True);material.write_bytes(b'pdf')
            write_json(work/'files.json',[dict(path=str(material))])
            files,_=discover_files(root,work,root/'out',targets,[])
            self.assertEqual([f['path'] for f in files],[material])

    def test_native_and_ocr_page_are_attributed_without_using_folder_project(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);work=root/'work';out=root/'out';work.mkdir();out.mkdir()
            source=str(root/'6.用地规划/选址意见书.pdf')
            text='建设项目选址意见书；华佗路道路工程；拟用地面积22.17亩'
            ocr=work/'page.txt';ocr.write_text(text,encoding='utf-8')
            write_json(work/'pdf_text.json',[dict(project='2020-002',source=source,page=1,text=text)])
            write_json(work/'ocr_manifest.json',[dict(project='2020-002',source=source+'#page=1',path=str(work/'page.png'),text=str(ocr))])
            with patch.multiple('extract_fields',OUT=work,DEST=out,ROOT=root),patch('extract_fields.target_records',return_value=[dict(t) for t in TARGETS]):extract_fields.main()
            records=[json.loads(line) for line in (out/'识别字段候选.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(records),2)
            for record in records:
                self.assertEqual(record['projects'],['2020-001']);self.assertEqual(record['folder_project'],'2020-002')
                self.assertEqual(record['storage_stage'],'6.用地规划');self.assertEqual(record['material_stage'],'预审与选址')
            report=json.loads((out/'材料识别统计.json').read_text(encoding='utf-8'))
            self.assertEqual(report['unique_pages'],1);self.assertEqual(report['candidate_project_fields'],3)

    def test_folder_hint_cannot_override_conflicting_known_project_code(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);work=root/'work';out=root/'out';work.mkdir();out.mkdir()
            source=str(root/'6.用地规划/仲景三期用地预审.pdf')
            text='建设项目用地预审与选址意见书；中医药科创城仲景雅苑三期A地块项目；2102-360090-04-01-193665；用字第36010020210001GJXQZGQ号'
            write_json(work/'pdf_text.json',[dict(project='2020-001',source=source,page=1,text=text)])
            write_json(work/'ocr_manifest.json',[])
            target=dict(TARGETS[0],project_code='2019-360090-48-01-020404')
            with patch.multiple('extract_fields',OUT=work,DEST=out,ROOT=root),patch('extract_fields.target_records',return_value=[target]):extract_fields.main()
            record=json.loads((out/'识别字段候选.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(record['projects'],[]);self.assertEqual(record['pending_projects'],[])
            self.assertEqual(record['identity_conflicts'],['2020-001'])

    def test_render_keeps_distinct_files_and_isolates_bad_pdf(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            job=Path(folder);(job/'work').mkdir();documents=[]
            for key in ('a','b'):
                path=job/key/'same.pdf';path.parent.mkdir()
                writer=PdfWriter();writer.add_blank_page(width=200,height=100);writer.write(path);writer.close()
                documents.append(dict(project='test',path=str(path),source=str(path),cache_key=key))
            bad=job/'bad.pdf';bad.write_bytes(b'not a pdf');documents.append(dict(project='test',path=str(bad),source=str(bad)))
            write_json(job/'work/pdf_documents.json',documents);write_json(job/'work/ocr_manifest.json',[])
            env=os.environ.copy();env.pop('GOV_ASSETS',None)
            for repeat in range(2):
                if repeat:
                    documents[0]['retrieval_projects']=['first','second'];write_json(job/'work/pdf_documents.json',documents)
                subprocess.run([str(NODE),str(BASE/'scripts/render_pdfs.mjs')],cwd=job,env=env,check=True,capture_output=True,timeout=60)
                pages=json.loads((job/'work/ocr_manifest.json').read_text())
                self.assertEqual(len(pages),2);self.assertEqual(len({p['path'] for p in pages}),2)
                if repeat:self.assertEqual(pages[0]['retrieval_projects'],['first','second'])
                errors=json.loads((job/'work/pdf_render_errors.json').read_text())
                self.assertEqual(len(errors),1);self.assertEqual(errors[0]['source'],str(bad))
            old_text=Path(pages[0]['text']);old_text.write_text('stale OCR',encoding='utf-8')
            writer=PdfWriter();writer.add_blank_page(width=220,height=100);writer.add_blank_page(width=220,height=100);writer.write(Path(documents[0]['path']));writer.close()
            subprocess.run([str(NODE),str(BASE/'scripts/render_pdfs.mjs')],cwd=job,env=env,check=True,capture_output=True,timeout=60)
            self.assertEqual(len(json.loads((job/'work/ocr_manifest.json').read_text())),3)
            self.assertFalse(old_text.exists())

    def test_pdf_native_reader_has_document_timeout(self):
        with patch('extract_pdfs.subprocess.run',side_effect=subprocess.TimeoutExpired('pdf',45)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):read_pdf_text(Path('drawing.pdf'))
            self.assertEqual(run.call_args.kwargs['timeout'],45)

    def test_expansion_stops_when_no_new_material_and_honors_skip_ocr(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            job=Path(folder);(job/'work').mkdir();(job/'outputs/governance').mkdir(parents=True)
            write_json(job/'work/pdf_documents.json',[]);write_json(job/'work/ocr_manifest.json',[])
            previous=Path.cwd()
            try:
                os.chdir(job)
                with patch('expand_materials.extract',return_value=set()) as extract,patch('expand_materials.subprocess.run') as run,patch('sys.argv',['expand_materials.py','--skip-ocr']):
                    expand_materials.main()
                    self.assertEqual(extract.call_count,1);self.assertEqual(run.call_count,3)
                    self.assertIs(run.call_args.kwargs['stdout'],os.sys.stdout)
                self.assertTrue(json.loads(Path('outputs/governance/跨阶段检索统计.json').read_text(encoding='utf-8'))['converged'])
            finally:os.chdir(previous)

    def test_expansion_reports_round_limit(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            job=Path(folder);(job/'work').mkdir();(job/'outputs/governance').mkdir(parents=True)
            write_json(job/'work/pdf_documents.json',[]);write_json(job/'work/ocr_manifest.json',[])
            previous=Path.cwd()
            try:
                os.chdir(job)
                with patch('expand_materials.extract',return_value={'new.pdf'}),patch('expand_materials.subprocess.run'),patch('sys.argv',['expand_materials.py','--skip-ocr']):expand_materials.main()
                report=json.loads(Path('outputs/governance/跨阶段检索统计.json').read_text(encoding='utf-8'))
                self.assertFalse(report['converged']);self.assertEqual(len(report['rounds']),3)
            finally:os.chdir(previous)

    def test_expansion_continues_when_only_relationships_change(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            job=Path(folder);(job/'work').mkdir();out=job/'outputs/governance';out.mkdir(parents=True)
            write_json(job/'work/pdf_documents.json',[]);write_json(job/'work/ocr_manifest.json',[])
            calls=[]
            def run(command,**kwargs):
                if command[-1].endswith('extract_fields.py'):
                    calls.append(command)
                    (out/'识别字段候选.jsonl').write_text(json.dumps(dict(source='same.pdf',projects=['row-3'],fields={'project_code':['new-code']})),encoding='utf-8')
            previous=Path.cwd()
            try:
                os.chdir(job)
                with patch('expand_materials.extract',return_value=set()),patch('expand_materials.subprocess.run',side_effect=run),patch('sys.argv',['expand_materials.py','--skip-ocr']):expand_materials.main()
                report=json.loads((out/'跨阶段检索统计.json').read_text(encoding='utf-8'))
                self.assertEqual(len(calls),2);self.assertTrue(report['converged']);self.assertTrue(report['rounds'][0]['relationships_changed'])
            finally:os.chdir(previous)

    def test_supply_alias_and_new_code_expand_search_but_conflicts_do_not(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            work=Path(folder);out=work/'out';out.mkdir()
            write_json(work/'供地矢量1016.json',[dict(DZJGH=TARGETS[0]['regulator_id'],HTHBBH=TARGETS[0]['contract_id'],XMMC='华佗路')])
            records=[dict(projects=['2020-001'],fields={'project_code':['2019-360090-48-01-020404']}),
                     dict(projects=[],fields={'project_code':['2019-360090-48-01-999999']})]
            (out/'识别字段候选.jsonl').write_text('\n'.join(json.dumps(r) for r in records),encoding='utf-8')
            terms=search_terms(TARGETS,work,out)
            self.assertIn('华佗路',terms['2020-001'])
            self.assertIn('2019-360090-48-01-020404',terms['2020-001'])
            self.assertNotIn('2019-360090-48-01-999999',terms['2020-001'])

    def test_identical_supply_attachment_opens_sibling_material(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);work=root/'work';out=root/'out';work.mkdir();out.mkdir()
            supply=root/'4.土地供应阶段/2020-001/合同.pdf';supply.parent.mkdir(parents=True);supply.write_bytes(b'same-contract')
            other=root/'6.用地规划/2019-360090-48-01-020404/合同.pdf';other.parent.mkdir(parents=True);other.write_bytes(supply.read_bytes())
            sibling=other.with_name('选址意见书.pdf');sibling.write_bytes(b'selection')
            private=other.with_name('身份证.pdf');private.write_bytes(b'private')
            write_json(work/'files.json',[{'path':str(p)} for p in (supply,other,sibling,private)])
            files,terms=discover_files(root,work,out,TARGETS,[dict(path=str(supply),project='2020-001')])
            self.assertIn(sibling,[x['path'] for x in files]);self.assertNotIn(private,[x['path'] for x in files])
            # 同一合同进入另一项目代码目录后，不能借它默认选择任何一个目录。
            duplicate=root/'7.工程规划/2019-360090-48-01-020401/合同.pdf';duplicate.parent.mkdir(parents=True);duplicate.write_bytes(supply.read_bytes())
            write_json(work/'files.json',[{'path':str(p)} for p in (supply,other,sibling,duplicate)])
            files,terms=discover_files(root,work,out,TARGETS,[dict(path=str(supply),project='2020-001')])
            self.assertIn(sibling,[x['path'] for x in files])
            self.assertEqual(next(x['projects'] for x in files if x['path']==sibling),[])

    def test_recognized_field_reaches_review_with_page_and_stays_manual(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            job=Path(folder);(job/'work').mkdir();out=job/'outputs/governance';out.mkdir(parents=True)
            wb=openpyxl.Workbook();sheet=wb.active;sheet.title='Sheet1';sheet.append([None]);sheet.append(HEADERS)
            row=[None]*69;row[1]='2020-001';row[23]='华佗路道路工程';sheet.append(row)
            wb.save(job/'test_result.xlsx');wb.save(job/'project_info_all_v5_a - 成果20261021.xlsx');wb.close()
            evidence=dict(projects=['row-3'],source='规划目录/选址意见书.pdf#page=2',image=None,text_method='PDF原生文字',identity_reason='页内项目名称唯一',
                          storage_stage='6.用地规划',field_candidates=[dict(column=32,value=22.17,evidence='拟用地面积22.17亩',type=None,material_stage='预审与选址')])
            unassigned=dict(evidence,projects=[],field_candidates=[dict(column=32,value=99,evidence='其他项目',type=None,material_stage='预审与选址')])
            (out/'识别字段候选.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in (evidence,unassigned)),encoding='utf-8')
            with patch('review_service.SOURCE',job):review=resolve_review(collect_review(job))
            entry=review['cells']['AF3'];self.assertEqual(entry['status'],'candidate');self.assertIsNone(entry['value'])
            self.assertEqual([c['value'] for c in entry['candidates']],[22.17])
            source=review['sources'][entry['candidates'][0]['sources'][0]]
            self.assertEqual(source['location'],'第2页，AF列，PDF原生文字');self.assertEqual(source['detail']['存放阶段'],'6.用地规划')
            self.assertEqual(source['detail']['原文证据'],['拟用地面积22.17亩'])
            pending=dict(unassigned,pending_projects=['row-3'])
            (out/'识别字段候选.jsonl').write_text(json.dumps(pending,ensure_ascii=False),encoding='utf-8')
            with patch('review_service.SOURCE',job):review=resolve_review(collect_review(job))
            candidate=review['cells']['AF3']['candidates'][0]
            self.assertTrue(candidate['project_pending']);self.assertEqual(review['cells']['AF3']['status'],'candidate')


if __name__=='__main__':unittest.main()
