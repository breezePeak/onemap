import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from code_recognition import Recognition
from project_catalog import ProjectCatalog
from project_codes import extract_codes
import test_project_codes as fixtures

CODE, OTHER = fixtures.CODE, fixtures.OTHER


class RecognitionTests(unittest.TestCase):
    setUp = fixtures.ProjectCodeTests.setUp
    put = fixtures.ProjectCodeTests.put
    scan = fixtures.ProjectCodeTests.scan
    view = fixtures.ProjectCodeTests.view

    def test_labels_adjacent_lines_and_isolated_codes(self):
        for label in ('项目代码', '项目编号', '建设项目代码', 'project_code'):
            self.assertEqual(extract_codes(label+':\n'+CODE, labelled=True)[0]['grade'], 'A')
        for label in ('合同编号','电子监管号','不动产单元号','证书编号','业务编号'):
            self.assertEqual(extract_codes(label+':\n'+CODE), [])
        self.put(1, '甲/a.txt', CODE)
        self.put(7, '乙/a.txt', '项目代码：'+CODE)
        result, _ = self.scan()
        self.assertEqual(result['links'], [])
        row = next(r for r in result['project_code_index'] if r['stage']=='S1')
        self.assertEqual((row['grade'],row['ownership_status']), ('C','unresolved_ownership'))

    def test_context_requires_exact_local_ownership(self):
        self.put(1,'甲/a.txt','项目名称：甲\n备案建设项目 '+CODE)
        self.put(7,'乙/a.txt','项目代码：'+CODE)
        result, _ = self.scan()
        self.assertEqual(len(result['links']),1)
        row = next(r for r in result['project_code_index'] if r['stage']=='S1')
        self.assertEqual(row['grade'],'B')
        self.put(1,'甲/a.txt','项目名称：其他项目\n项目代码：'+CODE)
        changed, _ = self.scan()
        self.assertEqual(changed['links'],[])

    def test_multi_page_compilation_does_not_assign_other_project(self):
        self.put(1,'甲/汇编.pdf',b'PDF fixture')
        self.put(7,'宗地一/a.txt','项目代码：'+CODE)
        self.put(7,'宗地二/a.txt','项目代码：'+OTHER)
        with patch('extract_pdfs.read_pdf_text',return_value=['项目名称：甲\n项目代码：'+CODE,'项目名称：乙\n项目代码：'+OTHER]):
            result, _ = self.scan()
        self.assertEqual(len(result['links']),1)
        self.assertEqual(result['links'][0]['right']['name'],'宗地一')
        foreign = next(r for r in result['project_code_index'] if r['stage']=='S1' and r['normalized_code']==OTHER)
        self.assertEqual((foreign['source_location'],foreign['ownership_status']),('page=2','unresolved_ownership'))

    def test_excel_records_keep_foreign_rows_out_of_directory(self):
        from openpyxl import Workbook
        book=Workbook(); sheet=book.active; sheet.append(['项目名称','项目编号']); sheet.append(['甲',CODE]); sheet.append(['乙',OTHER])
        path=self.put(1,'甲/台账.xlsx',b''); book.save(path); book.close()
        self.put(7,'宗地/a.txt','项目代码：'+CODE)
        result,_=self.scan()
        self.assertEqual(len(result['links']),1)
        foreign=next(r for r in result['project_code_index'] if r['normalized_code']==OTHER)
        self.assertEqual((foreign['location'],foreign['ownership_status']),('Sheet!B3','unresolved_ownership'))

    def test_two_code_columns_in_one_record_remain_unresolved(self):
        from openpyxl import Workbook
        book=Workbook();sheet=book.active;sheet.append(['项目名称','项目代码','项目编号']);sheet.append(['甲',CODE,OTHER])
        path=self.put(1,'甲/台账.xlsx',b'');book.save(path);book.close()
        self.put(7,'宗地/a.txt','项目代码：'+CODE)
        result,_=self.scan()
        self.assertEqual(result['links'],[])
        self.assertTrue(all(r['ownership_status']=='unresolved_ownership' and r['source_record']=='Sheet!row=2' for r in result['project_code_index'] if r['stage']=='S1'))

    def test_csv_and_word_header_rows_keep_local_projects(self):
        self.put(1,'甲/台账.csv',f'项目名称,项目代码\n甲,{CODE}\n乙,{OTHER}\n')
        rows=''.join('<w:tr>'+''.join('<w:tc><w:p><w:r><w:t>'+v+'</w:t></w:r></w:p></w:tc>' for v in row)+'</w:tr>' for row in (('项目名称','项目代码'),('丙',CODE),('丁',OTHER)))
        xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>'+rows+'</w:tbl></w:body></w:document>'
        self.put(7,'丙/台账.docx',fixtures.zip_bytes({'word/document.xml':xml}))
        result,_=self.scan()
        self.assertEqual(len(result['links']),1)
        current=[r for r in result['project_code_index'] if r['ownership_status']=='current_project']
        self.assertEqual({r['source_location'] for r in current},{'row=2,column=项目代码','表1行2列2'})

    def test_ocr_opt_in_limits_retry_and_remaining_pages(self):
        self.put(1,'甲/扫描.pdf',b'PDF fixture'); self.put(7,'乙/a.txt','项目代码：'+CODE)
        options=json.dumps(dict(ocr=True,max_pages=1,retries=1))
        page=dict(text='项目代码：'+CODE,layout={'words':[]},engine={'engine':'fixture','version':'1'})
        attempts=[]
        def recognize(path,number,version,allow_new=True):
            if not allow_new: return None
            attempts.append(number)
            if len(attempts)==1: raise OSError('retry')
            return page
        with patch.dict(os.environ,{'ONEMAP_M1_OPTIONS':options}), patch('extract_pdfs.read_pdf_text',return_value=['','','']), patch.object(Recognition,'ocr_page',side_effect=recognize):
            result,_=self.scan()
        self.assertEqual(attempts,[1,1])
        self.assertEqual(len(result['links']),1)
        self.assertTrue(any(e.get('remaining')==2 and e['state']=='unprocessed' for e in result['errors']))
        self.assertEqual(next(r for r in result['project_code_index'] if r['source_type']=='ocr')['ocr_engine']['version'],'1')
        with patch('extract_pdfs.read_pdf_text',side_effect=AssertionError('native cache')),patch.object(Recognition,'ocr_page',side_effect=AssertionError('disabled')):
            disabled,_=self.scan()
        self.assertEqual(disabled['links'],[])
        self.assertTrue(any(e.get('reason')=='ocr_disabled' for e in disabled['errors']))

    def test_filename_avoids_ocr_and_broken_field_triggers_it(self):
        path=self.put(1,f'甲/{CODE}.pdf',b'PDF fixture')
        with patch.dict(os.environ,{'ONEMAP_M1_OPTIONS':json.dumps(dict(ocr=True))}),patch('extract_pdfs.read_pdf_text',return_value=['']),patch.object(Recognition,'ocr_page',side_effect=AssertionError('已有可靠低成本代码')):
            self.scan()
        path.write_bytes(b'changed PDF fixture')
        value=dict(text='项目代码：'+CODE,layout={},engine={'version':'1'})
        with patch.dict(os.environ,{'ONEMAP_M1_OPTIONS':json.dumps(dict(ocr=True))}),patch('extract_pdfs.read_pdf_text',return_value=['项目代码：O14458']),patch.object(Recognition,'ocr_page',return_value=value) as ocr:
            self.scan()
        self.assertEqual(ocr.call_count,1)

    def test_ocr_cache_bound_to_version_page_and_engine(self):
        cat=ProjectCatalog(self.storage,self.root); self.addCleanup(cat.close)
        rec=Recognition(cat); rec.engine={'version':'one'}; path=self.put(1,'甲/a.pdf',b'pdf')
        def run(command,**kwargs):
            if '-Manifest' in command:
                manifest=json.loads(Path(command[-1]).read_text(encoding='utf-8'))[0]
                Path(manifest['text']).write_text('项目代码：'+CODE,encoding='utf-8')
                Path(manifest['text']+'.layout.json').write_text('{"words":[]}',encoding='utf-8')
            return subprocess.CompletedProcess(command,0,stdout='')
        with patch.object(rec,'run',side_effect=run) as runner:
            rec.ocr_page(path,1,{'size':1}); first=runner.call_count
            rec.ocr_page(path,1,{'size':1}); self.assertEqual(runner.call_count,first)
            rec.ocr_page(path,2,{'size':1}); self.assertGreater(runner.call_count,first)
            rec.engine={'version':'two'}; rec.ocr_page(path,1,{'size':1})
            rec.ocr_page(path,1,{'size':2})
        self.assertEqual(cat.db.execute('SELECT count(*) FROM code_recognition_cache').fetchone()[0],4)

    def test_model_schema_original_evidence_budget_and_cache(self):
        options=dict(semantic_command=['adapter'],semantic_version='fixture-v1',cost_per_call=1,max_cost=1)
        cat=ProjectCatalog(self.storage,self.root); self.addCleanup(cat.close)
        hit=extract_codes(CODE)[0]
        output=dict(normalized_code=CODE,source_location='page=2',ownership='uncertain',confidence='uncertain',evidence_text=CODE,needs_review=True)
        with patch.dict(os.environ,{'ONEMAP_M1_OPTIONS':json.dumps(options)}): rec=Recognition(cat)
        with patch.object(rec,'run',return_value=subprocess.CompletedProcess([],0,stdout=json.dumps(output))) as runner:
            self.assertEqual(rec.semantic(hit,'page=2',{},'a')['conclusion']['ownership'],'uncertain')
            self.assertEqual(rec.semantic(hit,'page=2',{},'a')['status'],'review')
            self.assertEqual(runner.call_count,1)
            self.assertEqual(rec.semantic(hit,'page=3',{},'a')['reason'],'semantic_budget_limit')
        for alteration in (dict(normalized_code=OTHER),dict(source_location='page=9'),dict(evidence_text='模型编造 '+OTHER),dict(needs_review=False),dict(extra=True)):
            rec.calls=0;rec.cost=0
            with patch.object(rec,'run',return_value=subprocess.CompletedProcess([],0,stdout=json.dumps(dict(output,**alteration)))):
                self.assertEqual(rec.semantic(hit,'page=2',{},str(alteration))['status'],'failed')

    def test_page_budget_resumes_past_cached_pages(self):
        cat=ProjectCatalog(self.storage,self.root);self.addCleanup(cat.close)
        path=self.put(1,'甲/a.pdf',b'pdf')
        def run(command,**kwargs):
            if '-Manifest' in command:
                item=json.loads(Path(command[-1]).read_text(encoding='utf-8'))[0]
                Path(item['text']).write_text('项目代码：'+CODE,encoding='utf-8')
                Path(item['text']+'.layout.json').write_text('{}',encoding='utf-8')
            return subprocess.CompletedProcess(command,0,stdout='')
        with patch.dict(os.environ,{'ONEMAP_M1_OPTIONS':json.dumps(dict(ocr=True,max_pages=1,max_files=1))}):
            for expected in (1,2,3):
                rec=Recognition(cat);rec.engine={'version':'fixture'}
                with patch.object(rec,'run',side_effect=run):
                    results,states=rec.ocr(path,{},[(f'page={i}','', 'body_field') for i in (1,2,3)])
                self.assertEqual(len(results),expected)
                self.assertEqual(rec.files,1)
            self.assertFalse(any(s['state']=='unprocessed' for s in states))

    def test_resource_states_and_timeout_do_not_become_no_code(self):
        cat=ProjectCatalog(self.storage,self.root);self.addCleanup(cat.close)
        path=self.put(1,'甲/a.pdf',b'PDF fixture')
        with patch.dict(os.environ,{'ONEMAP_M1_OPTIONS':json.dumps(dict(ocr=True,max_bytes=1))}): rec=Recognition(cat)
        self.assertEqual(rec.ocr(path,{},[])[1][0]['state'],'skipped')
        with patch.dict(os.environ,{'ONEMAP_M1_OPTIONS':json.dumps(dict(ocr=True,retries=0))}): rec=Recognition(cat)
        with patch.object(rec,'ocr_page',side_effect=TimeoutError('timeout')):
            self.assertEqual(rec.ocr(path,{},[('page=1','','body_field')])[1][0]['state'],'failed')
        rec.elapsed=301
        self.assertEqual(rec.ocr(path,{},[('page=1','','body_field')])[1][0]['state'],'unprocessed')

    def test_time_budget_counts_subprocess_time(self):
        cat=ProjectCatalog(self.storage,self.root);self.addCleanup(cat.close)
        rec=Recognition(cat);rec.options['max_seconds']=3
        with patch('code_recognition.time.monotonic',side_effect=[100,102,200,203]),patch('code_recognition.subprocess.run',return_value=subprocess.CompletedProcess([],0,stdout='')) as runner:
            rec.run(['fixture']);rec.run(['fixture'])
        self.assertEqual([call.kwargs['timeout'] for call in runner.call_args_list],[3,1])
        self.assertEqual(rec.elapsed,5)
        with self.assertRaises(TimeoutError):rec.run(['fixture'])

    def test_code_evidence_api_lists_unlinked_review_records(self):
        import server
        self.put(1,'甲/a.txt',CODE)
        result,_=self.scan();self.view(result)
        handler=server.Handler.__new__(server.Handler);handler.path='/api/folder-mount/code-evidence?status=unresolved_ownership'
        responses=[];handler.json=lambda value,status=200:responses.append((value,status))
        with patch.object(server,'MOUNT_DIR',self.storage),patch.object(server,'SOURCE',self.root): handler.do_GET()
        value,status=responses[-1]
        self.assertEqual((status,value['count']),(200,1))
        self.assertEqual(value['evidence'][0]['grade'],'C')

    def test_new_unresolved_evidence_invalidates_confirmation_keeps_m2(self):
        for stage in (1,7): self.put(stage,'甲/a.txt','项目代码：'+CODE)
        result,cache=self.scan(['M1','M2']); link=result['links'][0]
        decision={link['id']:dict(status='confirmed',signature=link['signature'],method_signatures=link['method_signatures'],review_context=link['review_context'])}
        self.put(1,'甲/foreign.txt',OTHER)
        changed,_=self.scan(['M1','M2'],cache)
        _,loaded=self.view(changed,decision)
        self.assertEqual(loaded['links'][0]['status'],'review')
        self.assertEqual(loaded['links'][0]['method_evidence']['M2'],link['method_evidence']['M2'])

    def test_other_source_cannot_reuse_repository(self):
        cat=ProjectCatalog(self.storage,self.root);cat.close()
        other=self.base/'other';other.mkdir()
        with self.assertRaisesRegex(ValueError,'其他数据源'): ProjectCatalog(self.storage,other)

    def test_ocr_ambiguous_characters_never_corrected(self):
        self.assertEqual(extract_codes('项目代码：'+CODE.replace('014458','O14458')),[])
        self.put(1,'甲/a.pdf',b'fixture')
        with patch.dict(os.environ,{'ONEMAP_M1_OPTIONS':json.dumps(dict(ocr=True))}),patch('extract_pdfs.read_pdf_text',return_value=['']),patch.object(Recognition,'ocr_page',return_value=dict(text='项目代码：'+CODE.replace('014458','O14458'),layout={},engine={'version':'1'})):
            result,_=self.scan()
        self.assertEqual(result['project_code_index'],[])
        self.assertTrue(any(e.get('state')=='unrecognized' and 'O14458' in e.get('evidence_text','') for e in result['errors']))

    @unittest.skipUnless(os.environ.get('ONEMAP_TEST_OCR')=='1', '设置 ONEMAP_TEST_OCR=1 才调用本机 Windows OCR 和 PDF 渲染器')
    def test_real_windows_ocr_and_single_page_renderer(self):
        from PIL import Image, ImageDraw, ImageFont
        from pypdf import PdfWriter
        from code_recognition import SCRIPTS
        image=Image.new('RGB',(2000,240),'white')
        ImageDraw.Draw(image).text((25,60),'PROJECT_CODE: '+CODE,font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',60),fill='black')
        path=self.put(1,'甲/scan.png',b'');image.save(path);image.close()
        cat=ProjectCatalog(self.storage,self.root);self.addCleanup(cat.close)
        rec=Recognition(cat)
        value=rec.ocr_page(path,1,{'fixture':1})
        self.assertIn(CODE,[hit['normalized_code'] for hit in extract_codes(value['text'])])
        self.assertTrue(value['layout']['words'])
        with patch.object(rec,'run',side_effect=AssertionError('已验证版本必须命中缓存')):
            self.assertTrue(rec.ocr_page(path,1,{'fixture':1})['cache_hit'])
        pdf=self.put(1,'甲/blank.pdf',b'');writer=PdfWriter();writer.add_blank_page(100,100)
        with pdf.open('wb') as stream:writer.write(stream)
        rendered=self.storage/'render.png'
        rec.run(['node',str(SCRIPTS/'render_pdfs.mjs'),'--page',str(pdf),'1',str(rendered)])
        with Image.open(rendered) as result:self.assertGreater(result.width,0)


if __name__=='__main__': unittest.main()
