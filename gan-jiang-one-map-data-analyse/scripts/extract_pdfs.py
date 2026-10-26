"""读取供应优先关联的各阶段 PDF/图片，保留存放阶段与检索依据。"""
import hashlib
import json
import os
import subprocess
import sys
import shutil
import zipfile
from pathlib import Path
from PIL import Image
from image_materials import prepare_image,valid_image,IMAGE_ERRORS
from inspect_sources import ROOT,OUT
from link_stages import target_records
from cross_stage_materials import MEDIA,SENSITIVE,discover_files,path_projects,read_json,media_header
from extract_archives import RecordedZip
from material_archives import open_archive


def office_pdf(path,assets):
    executable=shutil.which('soffice')
    if not executable:raise RuntimeError('未找到 LibreOffice，无法读取 Word/旧版 Excel，原件保留待处理')
    stat=path.stat();folder=assets/'office_pdf'/hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:20]
    folder.mkdir(parents=True,exist_ok=True);pdf=folder/(path.stem+'.pdf');version=folder/'source.json'
    if pdf.is_file() and read_json(version,{})==dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns):return pdf
    pdf.unlink(missing_ok=True)
    profile=(folder/'profile').resolve().as_uri()
    result=subprocess.run([executable,'-env:UserInstallation='+profile,'--headless','--convert-to','pdf','--outdir',str(folder.resolve()),str(path.resolve())],
                          capture_output=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or not pdf.is_file():raise RuntimeError('Office 转 PDF 失败：'+result.stderr.decode('utf-8',errors='replace')[-1000:])
    version.write_text(json.dumps(dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns)),encoding='utf-8')
    return pdf


def read_pdf_text(path):
    # 个别图纸的文字解析会长时间卡住；隔离并限时，失败保留日志，后续仍可渲染/OCR。
    code="import json,sys;from pypdf import PdfReader;print(json.dumps([p.extract_text() or '' for p in PdfReader(sys.argv[1]).pages],ensure_ascii=False))"
    env=os.environ.copy();env['PYTHONIOENCODING']='utf-8'
    result=subprocess.run([sys.executable,'-c',code,str(path)],capture_output=True,encoding='utf-8',env=env,check=True,timeout=45,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return json.loads(result.stdout)


def main():
    assets=Path(os.environ.get('GOV_ASSETS',str(OUT)))
    targets=target_records()
    previous=read_json(OUT/'pdf_documents.json',[])
    selected,terms=discover_files(ROOT,OUT,Path('outputs/governance'),targets,previous)
    # 保留供应图片队列，去掉旧 PDF 渲染项，由渲染器根据最新文档重建。
    manifest=[x for x in read_json(OUT/'ocr_manifest.json',[]) if '/pdf_pages/' not in Path(x['path']).as_posix() and valid_image(x['path'])]
    existing_images={x['source']:x for x in manifest}
    failures=read_json(OUT/'source_read_errors.json',[])
    inventory=read_json(OUT/'files.json',[])
    archives=[x for x in inventory if Path(x['path']).suffix.lower() in ('.zip','.rar')]
    archives.sort(key=lambda item:(0 if '4.土地供应阶段' in Path(item['path']).parts else 1,item['path']))
    for archive_item in archives:
        archive=Path(archive_item['path']);archive_source=archive_item.get('source',str(archive))
        storage_stage=archive_item.get('storage_stage') or archive.relative_to(ROOT).parts[0]
        print('检查跨阶段 ZIP',archive.name,flush=True)
        try:
            with open_archive(archive) as z:
                for info in z.infolist():
                    name=info.filename
                    if not info.flag_bits & 0x800:
                        try:name=name.encode('cp437').decode('gb18030')
                        except UnicodeError:pass
                    if SENSITIVE.search(name):continue
                    kind=Path(name).suffix.lower()
                    if kind not in MEDIA:
                        if kind not in ('','.bin'):continue
                        with z.open(info) as stream:kind=media_header(stream.read(16))
                    if not kind:continue
                    source=archive_source+'!'+name
                    projects=path_projects(source,targets,terms)
                    existing_image=Path(existing_images[source]['path']) if source in existing_images else None
                    if info.file_size>40*1024*1024:
                        failures.append(dict(source=source,error='单个材料超过40MB，需单独处理'));continue
                    # 保留既有划拨 PDF 缓存名称和人工核验图片路径。
                    key=name if archive.name=='划拨.zip' else source
                    suffix=kind
                    folder=assets/('pdfs' if suffix=='.pdf' else 'cross_stage_images')/(projects[0] if projects else 'unassigned')
                    folder.mkdir(parents=True,exist_ok=True)
                    dest=folder/(hashlib.sha256(key.encode()).hexdigest()[:12]+suffix)
                    if not dest.exists() or dest.stat().st_mtime_ns < archive.stat().st_mtime_ns:
                        temp=dest.with_suffix(dest.suffix+'.tmp')
                        with z.open(info) as stream,temp.open('wb') as output:
                            import shutil
                            shutil.copyfileobj(stream,output)
                        temp.replace(dest)
                    selected.append(dict(path=dest,kind=kind,projects=projects,source=source,storage_stage=storage_stage,
                                         retrieval_reason='压缩包路径命中' if projects else '全文扫描：压缩包路径无项目线索'))
        except (OSError,ValueError,zipfile.BadZipFile,NotImplementedError,RuntimeError,subprocess.SubprocessError,TimeoutError) as e:
            failures.append(dict(source=str(archive),error=str(e)))
    selected.sort(key=lambda item:(not bool(item['projects']),0 if item['storage_stage']=='4.土地供应阶段' else 1,item['source']))
    documents=[];seen=set();assets_manifest=[]
    print('待处理跨阶段材料',len(selected),flush=True)
    for item_index,item in enumerate(selected,1):
        if item_index%100==0:print('已检查材料',item_index,'/',len(selected),flush=True)
        path=item['path'];source=item['source']
        if source in seen:continue
        seen.add(source)
        projects=item['projects'];project=projects[0] if projects else 'unassigned'
        metadata=dict(project=project,retrieval_projects=projects,source=source,storage_stage=item['storage_stage'],retrieval_reason=item.get('retrieval_reason'))
        kind=item.get('kind',path.suffix.lower())
        if kind in ('.dwg','.dxf'):
            assets_manifest.append(dict(path=str(path.resolve()),**metadata));continue
        if kind in ('.doc','.docx','.xls'):
            try:path=office_pdf(path,assets)
            except (OSError,RuntimeError,subprocess.TimeoutExpired) as error:
                failures.append(dict(source=source,error=str(error)));continue
            kind='.pdf'
        if kind=='.pdf':
            cache_key=path.stem if path.parent.parent.name=='pdfs' else hashlib.sha256(source.encode()).hexdigest()[:16]
            documents.append(dict(path=str(path.resolve()),cache_key=cache_key,**metadata))
        else:
            try:
                if path.stat().st_size==0:raise ValueError('图片为空文件（0字节），无法识别')
                if source in existing_images and valid_image(existing_images[source]['path']) and Path(existing_images[source]['path']).stat().st_mtime_ns >= path.stat().st_mtime_ns:
                    manifest.append(dict(existing_images[source],**metadata));continue
                folder=assets/'cross_stage_images'/project;folder.mkdir(parents=True,exist_ok=True)
                image=folder/(hashlib.sha256(source.encode()).hexdigest()[:16]+'.png')
                if not valid_image(image) or image.stat().st_mtime_ns < path.stat().st_mtime_ns:prepare_image(path,image)
                manifest.append(dict(path=str(image.resolve()),text=str(image.with_suffix('.txt').resolve()),**metadata))
            except IMAGE_ERRORS as e:
                manifest=[m for m in manifest if m['source']!=source]
                failures.append(dict(source=source,error=type(e).__name__+': '+str(e)))
    print('已定位 PDF',len(documents),'图片来源',len(manifest),'开始读取正文',flush=True)
    texts=[];text_cache=read_json(OUT/'pdf_text_cache.json',{})
    for index,document in enumerate(documents,1):
        try:
            path=Path(document['path']);stat=path.stat();cache_key=str(path)
            cached=text_cache.get(cache_key)
            if not cached or cached['size']!=stat.st_size or cached['mtime_ns']!=stat.st_mtime_ns or cached.get('ctime_ns')!=stat.st_ctime_ns:
                try:cached=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,pages=read_pdf_text(path))
                except subprocess.TimeoutExpired:cached=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,pages=[],error='PDF 原生文字提取超过45秒，保留材料供渲染和OCR；本任务不重复解析同一版本')
                except subprocess.CalledProcessError as e:cached=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,pages=[],error='PDF 原生文字解析失败：'+(e.stderr or str(e))[-2000:])
                cached['ctime_ns']=stat.st_ctime_ns
                text_cache[cache_key]=cached
            if cached.get('error'):failures.append(dict(source=document['source'],error=cached['error']))
            for page_no,text in enumerate(cached['pages'],1):texts.append(dict(project=document['project'],retrieval_projects=document['retrieval_projects'],source=document['source'],page=page_no,text=text,retrieval_reason=document.get('retrieval_reason')))
        except Exception as e:failures.append(dict(source=document['source'],error=str(e)))
        if index%25==0:
            (OUT/'pdf_text_cache.json').write_text(json.dumps(text_cache,ensure_ascii=False),encoding='utf-8')
            print('已读取 PDF',index,'/',len(documents),flush=True)
    manifest=list({x['source']:x for x in manifest}.values())
    for name,data in [('ocr_manifest.json',manifest),('pdf_text.json',texts),('pdf_errors.json',failures),('pdf_documents.json',documents),('pdf_text_cache.json',text_cache),('material_assets.json',assets_manifest)]:
        (OUT/name).write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    print('跨阶段 PDF',len(documents),'原生文字页',len(texts),'图片',len(manifest),'读取错误',len(failures),flush=True)
    return {d['source'] for d in documents}|{m['source'] for m in manifest}


if __name__=='__main__':main()
