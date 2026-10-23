"""对照原目录、解压记录和识别缓存，区分解压完成与文字提取完成。"""
import json
from pathlib import Path
from extract_archives import ArchiveRecords,BASE,SOURCE,OUTPUT,original_member


def key(source):return str(source).replace('\\','/').split('#page=',1)[0].casefold()


def main():
    records=ArchiveRecords();work=BASE/'work'
    roots=[work,work/'cross_stage_validation/work',*work.glob('jobs/*/work')]
    ocr=set();native=set()
    for root in roots:
        manifest=root/'ocr_manifest.json'
        if manifest.is_file():
            for row in json.loads(manifest.read_text(encoding='utf-8')):
                text=Path(row['text'])
                if text.is_file() and text.stat().st_size:ocr.add(key(row['source']))
        cache=root/'pdf_text_cache.json';docs=root/'pdf_documents.json'
        if cache.is_file() and docs.is_file():
            values=json.loads(cache.read_text(encoding='utf-8'))
            for row in json.loads(docs.read_text(encoding='utf-8')):
                value=values.get(row['path'],{})
                if not value.get('error') and any(str(p).strip() for p in value.get('pages',[])):native.add(key(row['source']))
    report=[];details=[]
    try:
        for archive in SOURCE.rglob('*'):
            if not archive.is_file() or archive.suffix.lower()!='.zip':continue
            summary=dict(archive=str(archive),files=0,original_present=0,local_extracted=0,missing=0,with_ocr_text=0,with_pdf_text=0)
            try:
                for member in records.members(archive):
                    if member['directory']:continue
                    summary['files']+=1
                    original=original_member(archive,member)
                    existing=records.existing(archive,member)
                    status='original_present' if original else 'local_extracted' if existing else 'missing'
                    summary[status]+=1
                    sources={key(str(archive)+'!'+member['name'])}
                    if original:sources.add(key(original))
                    with_ocr=bool(sources & ocr);with_native=bool(sources & native)
                    summary['with_ocr_text']+=with_ocr;summary['with_pdf_text']+=with_native
                    details.append(dict(archive=str(archive),member=member['name'],status=status,path=str(original or existing or ''),with_ocr_text=with_ocr,with_pdf_text=with_native))
            except Exception as error:summary['error']=str(error)
            report.append(summary)
            if len(report)%20==0:print('已核对 ZIP',len(report),flush=True)
    finally:
        records.db.commit();records.db.close()
    totals={k:sum(r[k] for r in report) for k in ('files','original_present','local_extracted','missing','with_ocr_text','with_pdf_text')}
    totals['archives']=len(report)
    (OUTPUT/'材料完成情况.json').write_text(json.dumps(dict(note='原目录对应仅核对路径与大小，未逐个校验CRC；有文字仅表示至少一页有缓存，不代表整份识别完成或字段正确。',totals=totals,archives=report,members=details),ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# ZIP 与识别缓存核对','', '原目录对应核对路径与大小，未逐个校验 CRC。有文字表示至少一页存在文字缓存，不代表整份识别完成或字段正确。','', '|压缩包|文件数|原目录已存在|本项目已解压|缺失|有OCR文字的材料|有PDF文字的材料|','|---|---:|---:|---:|---:|---:|---:|']
    for r in report:lines.append('|'+str(Path(r['archive']).relative_to(SOURCE))+'|'+'|'.join(str(r[k]) for k in ('files','original_present','local_extracted','missing','with_ocr_text','with_pdf_text'))+'|')
    lines+=['','汇总：'+json.dumps(totals,ensure_ascii=False)]
    (OUTPUT/'材料完成情况.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(totals,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
