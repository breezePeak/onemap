"""只读真实材料抽样验证；不将样本结果冒充全量验收。"""
from contextlib import closing
import json
from pathlib import Path
import traceback

from stage2 import BASE, SOURCE, STAGES, Stage2, read_material, digest, material_stage


def main():
    store=BASE/'work/stage2-real-validation'
    report=dict(source=str(SOURCE),scope='七阶段真实材料抽样，不代表全量或人工业务验收；数量为验证库累计有效记录',samples=[],errors=[],page_warnings=[])
    terms={'S1':['选址意见书'],'S2':['条件'],'S3':['建设用地的批复'],'S4':['选址意见书'],'S5':['出让合同','划拨决定书'],'S6':['条件'],'S7':['ZRZYSITE_CONSTRUCT__001012_003b']}
    with closing(Stage2(store,SOURCE)) as service:
        service.configure(BASE/'templates/test.xlsx');report['projects']=service.sync_catalog()
        for sid,folder in STAGES:
            paths=[p for p in (SOURCE/folder).rglob('*') if p.is_file() and p.suffix.lower() in ('.pdf','.docx','.csv','.xlsx') and any(t in p.name for t in terms[sid])]
            paths.sort(key=lambda p:(p.suffix.lower()!='.docx',p.stat().st_size,str(p)))
            if not paths:
                report['errors'].append(dict(stage=sid,error='未找到指定文书类型的真实样本'));continue
            for path in paths[:2]:
                try:
                    pages,method=read_material(path,store/'cache'/digest(path))
                    report['page_warnings'].extend(dict(path=str(path),location=p.get('location'),error=p['error']) for p in pages if isinstance(p,dict) and p.get('error'))
                    # 台账保留全部行；逐行归属，不以同一代码合并。
                    with service.db:mid=service.ingest(path,pages,method)
                    counts=[dict(r) for r in service.db.execute('SELECT business_stage,count(*) AS records FROM records WHERE material=? AND active=1 GROUP BY business_stage',(mid,))]
                    fields=[dict(r) for r in service.db.execute('SELECT field,count(*) AS candidates FROM candidates WHERE active=1 AND record IN (SELECT id FROM records WHERE material=? AND active=1) GROUP BY field',(mid,))]
                    sample=dict(storage_stage=sid,path=str(path),fingerprint=digest(path),method=method,pages=len(pages),business_records=counts,fields=fields,
                        excerpt=(pages[0]['text'] if isinstance(pages[0],dict) else pages[0])[:900] if pages else '')
                    report['samples'].append(sample)
                    print(json.dumps({k:v for k,v in sample.items() if k!='excerpt'},ensure_ascii=False),flush=True)
                except Exception as error:
                    report['errors'].append(dict(stage=sid,path=str(path),error=str(error)))
                    print(sid,str(error),flush=True)
        report['candidate_count']=service.db.execute('SELECT count(*) FROM candidates WHERE active=1').fetchone()[0]
        report['record_count']=service.db.execute('SELECT count(*) FROM records WHERE active=1').fetchone()[0]
        report['preview']=str(service.preview())
        report['note']='真实材料自动识别出的值仍为候选，未代替用户确认；未覆盖的验收场景必须保持待验证。'
    destination=BASE/'docs/stage2-real-validation.json';destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(destination),flush=True)


if __name__=='__main__':main()
