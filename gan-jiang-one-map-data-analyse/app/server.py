"""仅监听本机的 Excel 治理工作台。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import unquote,urlsplit,quote,parse_qs
import uuid

BASE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE/'scripts'))
from review_service import JOBS,SOURCE,read_json,write_json,validate_upload,prepare_job,run_step,collect_review,resolve_review,save_choice,export_review,spatial_preview,save_judgement,save_alias,save_coordinate_system,save_georeference,save_relationship_decision
from storage_info import storage_info
from folder_mount import scan_folders, validate_methods, PreparationError, RULE_VERSION, identifier, project_rows
from source_config import STAGES, STAGE_TITLES
from project_catalog import ProjectCatalog

def get_catalog_projects():
    cat=ProjectCatalog(storage_dir=MOUNT_DIR,root=SOURCE)
    try: return list(cat.projects.values())
    finally: cat.close()

from organize_independent_documents import organize_independent_documents

MOUNT_DIR=BASE/'work/folder_mount'
MOUNT_LOCK=threading.RLock()
MOUNT_POOL=ThreadPoolExecutor(max_workers=1)
MOUNT_STATE=read_json(MOUNT_DIR/'task.json',dict(status='idle',phase='尚未扫描'))
if MOUNT_STATE['status']=='running':
    MOUNT_STATE.update(status='interrupted',phase='上次任务因服务重启中断，请重新扫描；已验证成员可以复用')


MOUNT_LAST_SAVE=0

def mount_update(**changes):
    global MOUNT_LAST_SAVE
    with MOUNT_LOCK:
        MOUNT_STATE.update(changes)
        if 'status' in changes or time.monotonic()-MOUNT_LAST_SAVE>=0.5:
            write_json(MOUNT_DIR/'task.json',MOUNT_STATE)
            MOUNT_LAST_SAVE=time.monotonic()


def mount_scan(methods=None):
    try:
        organize_independent_documents(root=SOURCE, verbose=False)
        def progress(value): mount_update(**value)
        result,cache=scan_folders(SOURCE,BASE/'解压材料/解压记录.sqlite',read_json(MOUNT_DIR/'hashes.json',{}),progress,methods,storage_dir=MOUNT_DIR)
        with MOUNT_LOCK:
            write_json(MOUNT_DIR/'hashes.json',cache)
            write_json(MOUNT_DIR/'result.json',dict(result,scanned_at=datetime.now().isoformat(timespec='seconds')))
            for method in result['methods']:
                write_json(MOUNT_DIR/f'result-{method}.json',dict(result,scanned_at=datetime.now().isoformat(timespec='seconds')))
            phase='完成（含未读取材料，请核对异常）' if result.get('errors') else '完成'
            mount_update(status='ready',phase=phase,errors=result.get('errors',[]),stages=result.get('stages',[]))
    except PreparationError as error:
        write_json(MOUNT_DIR/'hashes.json',error.cache)
        write_json(MOUNT_DIR/'inventory.json',{key:error.result[key] for key in ('folders','stages','errors')})
        mount_update(status='failed',phase=str(error),errors=error.result['errors'],stages=error.result['stages'])
    except Exception as error:
        mount_update(status='failed',phase='任务失败',errors=[dict(path=str(SOURCE),error=str(error))])


def mount_result():
    result=read_json(MOUNT_DIR/'result.json',dict(folders=[],links=[],errors=[]))
    for folder in result['folders']+[f for link in result['links'] for f in (link['left'],link['right'])]:
        folder.setdefault('id',identifier('legacy:'+folder['folder']))
        folder.setdefault('name',Path(folder['folder']).name)
    if result.get('rule_version')!=RULE_VERSION and (MOUNT_DIR/'inventory.json').is_file():
        inventory=read_json(MOUNT_DIR/'inventory.json')
        result.update(folders=inventory['folders'],links=[],stages=inventory['stages'],inventory_only=True)
    result['folders']=[folder for folder in result['folders'] if Path(folder['folder'].split('!',1)[0]).resolve().is_relative_to(SOURCE.resolve())]
    allowed={folder['id'] for folder in result['folders']}
    result['links']=[link for link in result['links'] if link['left']['id'] in allowed and link['right']['id'] in allowed]
    decisions=read_json(MOUNT_DIR/'decisions.json',{})
    for link in result['links']:
        link.setdefault('evidence_count',len(link.get('evidence',[])))
        decision=decisions.get(link['id'],{})
        same=decision.get('signature')==link['signature'] and result.get('rule_version')==RULE_VERSION
        if decision.get('review_context'):
            same = same and decision['review_context']==link.get('review_context')
        elif link.get('ambiguous') or link.get('conflict') or any(f.get('reason')=='人工手动指定项目大目录边界' for f in (link['left'],link['right'])):
            same = False
        link['method_statuses']={method: (decision.get('status','pending') if decision.get('method_signatures',{}).get(method, decision.get('signature') if method=='M2' else None)==signature else 'pending') for method,signature in link.get('method_signatures',{'M2':link['signature']}).items()}
        link['status']=decision.get('status',link.get('status','pending')) if same else ('review' if decision.get('status') in ('confirmed','rejected','review') else link.get('status','pending'))
        link['confirmation']=decision
    return result




def mount_search(value):
    return json.dumps(value,ensure_ascii=False).replace('\\\\','/').casefold()


def resolve_mount_path(raw_path):
    raw_path=str(raw_path or '').strip()
    if not raw_path:raise ValueError('缺少路径参数。')
    parts=[p for p in raw_path.split('!') if p]
    if not parts:raise ValueError('路径无效。')
    current=Path(parts[0])
    if len(parts)==1 and raw_path.endswith('!'):
        extracted=current.with_name(current.name+'.解压')
        if extracted.exists():current=extracted
    else:
        for member in parts[1:]:
            extracted=current.with_name(current.name+'.解压')
            candidate=extracted/member
            if candidate.exists():current=candidate
            elif (current.with_suffix('')/member).exists():current=current.with_suffix('')/member
            elif extracted.exists():current=extracted;break
            else:break
    resolved=current.resolve()
    if not any(resolved.is_relative_to(root.resolve()) for root in (SOURCE,BASE)):
        raise ValueError('路径不在项目或数据源目录内。')
    if not resolved.exists():raise FileNotFoundError('路径在磁盘上不存在。')
    return resolved


def launch_explorer(target,action='folder'):
    if os.name=='nt':
        if action=='open' or target.is_dir():os.startfile(str(target))
        else:subprocess.Popen(['explorer.exe',f'/select,{target}'])


def open_local_path(raw_path,action='folder'):
    if action not in ('folder','select','open'):raise ValueError('操作类型无效。')
    target=resolve_mount_path(raw_path)
    launch_explorer(target,action)
    return dict(opened=str(target),is_dir=target.is_dir(),action=action)


def mount_view(params=None):
    params=params or {}; result=mount_result(); state=dict(MOUNT_STATE)
    if state['status']=='idle' and result.get('scanned_at'): state.update(status='ready',phase='已载入上次扫描结果')
    historical=bool(result.get('scanned_at')) and (state['status']!='ready' or result.get('rule_version')!=RULE_VERSION)
    query=params.get('q',[''])[0].casefold(); status=params.get('status',['all'])[0]; pair=params.get('pair',['all'])[0]; method=params.get('method',['all'])[0]
    links=result['links']; linked={f['id'] for link in links for f in (link['left'],link['right']) if 'id' in f}
    unlinked=[f for f in result['folders'] if f.get('id') not in linked]
    filtered=[e for e in (unlinked if status=='unlinked' else links)
              if (status in ('all','unlinked') or e['status']==status)
              and (pair=='all' or (e['stage'] in pair.split('-') if status=='unlinked' else e['left']['stage']+'-'+e['right']['stage']==pair))
              and query in mount_search(e)]
    if method!='all': filtered=[e for e in filtered if method in e.get('methods',[e.get('method','M2')])]
    requested_page=max(1,int(params.get('page',['1'])[0])); pages=max(1,(len(filtered)+49)//50); page=min(requested_page,pages)
    visible=filtered[(page-1)*50:page*50]
    compact=lambda link:{k:v for k,v in link.items() if k not in ('evidence','method_evidence')}
    chains=[]
    outgoing={}
    for link in links:
        if link['status']=='confirmed': outgoing.setdefault(link['left'].get('id'),[]).append(link)
    def chain(path):
        if len(chains)>=200: return
        children=outgoing.get(path[-1]['right'].get('id'),[])
        if len(path)>1: chains.append([path[0]['left']]+[e['right'] for e in path])
        for child in children: chain(path+[child])
    for group in outgoing.values():
        for link in group: chain([link])
    response=dict(status=state['status'],phase=state['phase'],processed=state.get('processed',0),total=state.get('total',0),current_stage=state.get('current_stage',''),current_archive=state.get('current_archive',''),current_member=state.get('current_member',''),
                source_root=str(SOURCE),stage_config=[dict(stage_id=s,name=n,title=STAGE_TITLES.get(s,n)) for s,n in STAGES],projects=get_catalog_projects(),stages=result.get('stages',[]) if result.get('inventory_only') else state.get('stages',result.get('stages',[])),
                links=[compact(e) for e in visible] if status!='unlinked' else [],unlinked=visible if status=='unlinked' else [],
                errors=state.get('errors',result.get('errors',[])),historical=historical,scanned_at=result.get('scanned_at'),
                summary=dict(folders=len(result['folders']),links=len(links),confirmed=sum(e['status']=='confirmed' for e in links),pending=sum(e['status']=='pending' for e in links),review=sum(e['status']=='review' for e in links),unlinked=len(unlinked)),
                page=page,pages=pages,count=len(filtered),chains=chains,chains_truncated=len(chains)>=200)
    response.update(methods=result.get('methods',['M2']),m1_statistics=result.get('m1_statistics'),skip_statistics=result.get('skip_statistics'))
    if params.get('view',[''])[0]=='projects':
        rows=project_rows(result)
        chosen=[row for row in rows if (status=='all' or row['status']==status)
                and (pair=='all' or (bool(row['stages'].get(pair)) if '-' not in pair else any(link['left']['stage']+'-'+link['right']['stage']==pair for link in row['links'])))
                and (method=='all' or any(method in link.get('methods',[link.get('method','M2')]) for link in row['links'])) and query in mount_search(row)]
        pages=max(1,(len(chosen)+49)//50); page=min(requested_page,pages)
        response.update(rows=chosen[(page-1)*50:page*50],page=page,pages=pages,count=len(chosen),inventory_only=result.get('inventory_only',False))
        response['summary'].update(projects=len(rows),project_confirmed=sum(r['status']=='confirmed' for r in rows),project_pending=sum(r['status']=='pending' for r in rows),project_review=sum(r['status']=='review' for r in rows),project_unlinked=sum(r['status']=='unlinked' for r in rows),project_unscanned=sum(r['status']=='unscanned' for r in rows))
    return response


POOL=ThreadPoolExecutor(max_workers=1)
STATS_POOL=ThreadPoolExecutor(max_workers=8)
LOCKS={}
PROTECTED_WORK_ENTRIES={'python_packages','jobs','folder_mount'}
DATAFILE_NOTES={
    'materials':'解压后的材料文件','pdfs':'收集的 PDF 原件副本','pdf_pages':'PDF 渲染出的页面图片',
    'render_manifests':'PDF 渲染清单','cross_stage_images':'跨阶段核验渲染图','cross_stage_validation':'跨阶段核验数据',
    'pdf_text.json':'PDF 文本提取结果','pdf_text_cache.json':'PDF 文本提取缓存','pdf_documents.json':'PDF 文档清单',
    'pdf_errors.json':'PDF 解析错误记录','ocr_manifest.json':'OCR 任务清单','ocr_priority.json':'OCR 优先级清单',
    'hash_cache.json':'附件 SHA-256 指纹缓存','files.json':'数据源盘点结果','spatial_matches.json':'空间挂接结果',
    'patches.json':'台账回填补丁','pipeline_run.log':'管线运行日志','link_console.log':'挂接运行日志',
    'export_console.log':'导出运行日志','python_packages':'程序运行依赖（不可删除）','jobs':'处理任务数据（在“处理任务”页签中逐个删除）',
    'governance':'治理输出明细（Excel、挂接明细、溯源等）','ui_checks':'界面自检数据',
    '供地矢量1016.json':'供地矢量数据','批文报批矢量1016.json':'批文报批矢量数据','地表宗地.json':'地表宗地矢量数据',
    '出让_zip.json':'出让档案材料','划拨_zip.json':'划拨档案材料','6.用地规划_台账.json':'用地规划台账提取结果',
    '7.工程规划_台账.json':'工程规划台账提取结果',
}

def tree_size(path):
    size=0;count=0
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):sub,sub_count=tree_size(entry.path);size+=sub;count+=sub_count
                elif entry.is_file(follow_symlinks=False):size+=entry.stat(follow_symlinks=False).st_size;count+=1
            except OSError:pass
    return size,count

def entry_info(path):
    try:modified=datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec='seconds')
    except OSError:modified=None
    if path.is_dir():
        size,count=tree_size(path);return dict(is_dir=True,size_bytes=size,file_count=count,modified=modified)
    try:stat=path.stat()
    except OSError:stat=None
    return dict(is_dir=False,size_bytes=stat.st_size if stat else 0,file_count=1 if stat else 0,modified=modified)

def datafile_entries():
    entries=[];futures=[]
    for root in (BASE/'work',BASE/'outputs'):
        if not root.is_dir():continue
        with os.scandir(root) as items:
            for item in items:
                path=Path(item.path);relative=path.relative_to(BASE).as_posix()
                protected=(root.name=='work' and path.name in PROTECTED_WORK_ENTRIES)
                futures.append((dict(key=relative,name=path.name,root=str(root.resolve()),path=str(path.resolve()),note=DATAFILE_NOTES.get(path.name,'程序处理过程数据' if path.suffix in ('.json','.log','.jsonl') else ('程序处理过程数据' if path.is_dir() else '处理过程文件')),protected=protected),STATS_POOL.submit(entry_info,path)))
    total=0
    for base,future in futures:
        try:info=future.result(timeout=60)
        except Exception:info=dict(is_dir=path_is_dir(Path(base['path'])),size_bytes=0,file_count=0,modified=None)
        base.update(info);entries.append(base);total+=base['size_bytes']
    entries.sort(key=lambda x:x['size_bytes'],reverse=True)
    return entries,total

def path_is_dir(path):
    try:return path.is_dir()
    except OSError:return False

def remove_tree(path):
    def on_error(func,target,exc):
        try:
            os.chmod(target,stat.S_IWRITE);func(target)
        except PermissionError:raise PermissionError(f'「{target}」正被其他程序占用（可能仍在处理或被杀毒软件扫描），请稍后重试。')
        except Exception:raise
    shutil.rmtree(path,onerror=on_error)

def job_path(ident):
    if not re.fullmatch(r'[a-f0-9]{16}',ident):raise ValueError('任务编号无效。')
    path=JOBS/ident
    if not (path/'meta.json').is_file():raise FileNotFoundError('任务不存在。')
    return path

def update(job,**changes):
    state=read_json(job/'meta.json',{});state.update(changes);write_json(job/'meta.json',state)


def execute(job):
    steps=[('inspect_sources.py','盘点数据源'),('extract_materials.py','优先读取供应扫描件'),('extract_pdfs.py','各阶段材料全文检索（不限路径命中）'),('render_pdfs.mjs','渲染 PDF 页面'),('ocr_images.ps1','本机 OCR（已有文字自动复用）'),('vector_outputs.py','读取附件SHP/CAD及转换坐标'),('spatial_matches.py','计算空间挂接'),('extract_fields.py','提取字段及多项目候选'),('expand_materials.py','利用新标识和附件指纹继续跨阶段检索'),('link_stages.py','编号、附件与模糊挂接'),('build_patches.py','建立回填及冲突清单'),('governance_outputs.py','整理阶段材料、选址信息及补建项目')]
    try:
        prepare_job(job)
        for i,(script,label) in enumerate(steps):
            update(job,status='running',phase=label,progress=round(i/len(steps)*90))
            if script=='ocr_images.ps1':run_step(job,script,'-Manifest',str(job/'work/ocr_manifest.json'))
            else:run_step(job,script)
        update(job,phase='合并字段来源与候选',progress=95)
        review=collect_review(job)
        update(job,status='ready',phase='检索完成，有来源异常待核对' if review.get('source_error_count') else '检索完成，可以核对并导出',progress=100,cross_stage=True)
    except Exception as e:update(job,status='failed',phase='处理失败',error=str(e))

def refresh_matching(job):
    try:
        for script in ('extract_fields.py','vector_outputs.py','spatial_matches.py','build_patches.py'):
            update(job,status='running',phase='重算匹配：'+script,progress=50)
            run_step(job,script)
        collect_review(job);run_step(job,'governance_outputs.py')
        update(job,status='ready',phase='匹配已根据人工证据更新',progress=100)
    except Exception as error:update(job,status='failed',phase='重算失败',error=str(error))

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):print(fmt%args,flush=True)
    def json(self,payload,status=200):
        body=json.dumps(payload,ensure_ascii=False,default=str).encode();self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def body(self,limit=25*1024*1024):
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=limit:raise ValueError('文件为空或超过 25 MB 限制。')
        return self.rfile.read(size)
    def file(self,path,download=False,large_download=False):
        if not path.is_file():raise FileNotFoundError('文件不存在。')
        if path.stat().st_size>40*1024*1024 and not large_download:raise ValueError('来源文件较大，请按显示的路径在本机打开。')
        self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(path.stat().st_size));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
        if download:
            name=path.name
            if path.suffix.lower()=='.csv':
                with path.open('rb') as f:
                    if f.read(2)==b'PK':name=path.stem+'.xlsx'
            self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(name))
        self.end_headers()
        with path.open('rb') as f:shutil.copyfileobj(f,self.wfile)
    def do_GET(self):
        try:
            parts=urlsplit(self.path).path.strip('/').split('/')
            if parts==['api','folder-mount']:
                with MOUNT_LOCK:return self.json(mount_view(parse_qs(urlsplit(self.path).query)))
            if parts==['api','folder-mount','projects']:
                with MOUNT_LOCK:
                    projs=get_catalog_projects()
                    return self.json(dict(projects=projs,count=len(projs)))
            if parts==['api','folder-mount','file']:
                params=parse_qs(urlsplit(self.path).query)
                target=resolve_mount_path(params.get('path',[''])[0])
                if not target.is_file():raise FileNotFoundError('目标不是可预览的文件。')
                download=params.get('download',['0'])[0]=='1'
                if not download and target.suffix.lower() in ('.txt','.csv','.json','.md','.xml','.prj','.cpg','.log') and target.stat().st_size<=5*1024*1024:
                    raw=target.read_bytes()
                    try:text=raw.decode('utf-8-sig')
                    except UnicodeDecodeError:text=raw.decode('gb18030',errors='replace')
                    body=text.encode('utf-8')
                    self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8')
                    self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
                    return
                return self.file(target,download=download,large_download=True)
            if len(parts)==4 and parts[:3]==['api','folder-mount','links']:
                with MOUNT_LOCK:
                    link=next((e for e in mount_result()['links'] if e['id']==parts[3]),None)
                    if not link: raise FileNotFoundError('目录关系不存在。')
                    params=parse_qs(urlsplit(self.path).query); page=max(1,int(params.get('page',['1'])[0]))
                    query=params.get('q',[''])[0].casefold()
                    method=params.get('method',['all'])[0]
                    evidence=[e for e in link.get('evidence',[]) if query in mount_search(e) and (method=='all' or e.get('method','M2')==method)]
                    pages=max(1,(len(evidence)+19)//20); page=min(page,pages)
                    return self.json(dict(id=link['id'],signature=link['signature'],confirmation=link.get('confirmation'),evidence=evidence[(page-1)*20:page*20],page=page,pages=pages,count=len(evidence)))
            if parts==['api','storage']:
                params=parse_qs(urlsplit(self.path).query)
                return self.json(storage_info(BASE,SOURCE,params.get('kind',['archive'])[0],params.get('q',[''])[0],int(params.get('page',['1'])[0])))
            if parts==['api','jobs']:
                jobs=[dict(id=p.parent.name,**read_json(p,{})) for p in JOBS.glob('*/meta.json')]
                return self.json(sorted(jobs,key=lambda x:x.get('created_at',''),reverse=True))
            if parts==['api','jobs','stats']:
                metas=[(p.parent.name,read_json(p,{})) for p in JOBS.glob('*/meta.json')]
                metas.sort(key=lambda x:x[1].get('created_at',''),reverse=True)
                futures=[(ident,meta,STATS_POOL.submit(entry_info,JOBS/ident)) for ident,meta in metas]
                jobs=[]
                for ident,meta,future in futures:
                    try:info=future.result(timeout=60)
                    except Exception:info=dict(is_dir=True,size_bytes=0,file_count=0,modified=None)
                    jobs.append(dict(id=ident,directory=str((JOBS/ident).resolve()),**meta,**info))
                return self.json(jobs)
            if parts==['api','datafiles']:
                entries,total=datafile_entries()
                return self.json({'entries':entries,'total_bytes':total})
            if len(parts)>=3 and parts[:2]==['api','jobs']:
                job=job_path(parts[2])
                if len(parts)==3:
                    state=read_json(job/'meta.json');log=job/'console.log'
                    state['artifacts']=(job/'outputs/governance/治理成果.zip').is_file()
                    if log.exists():
                        with log.open('rb') as f:f.seek(max(0,log.stat().st_size-50000));state['logs']=f.read().decode('utf-8',errors='replace')[-30000:]
                    return self.json(state)
                if parts[3]=='review':
                    review=read_json(job/'review.json')
                    if review is None:raise ValueError('检索尚未完成。')
                    review['spatial_projects']=[str(r['project']) for r in read_json(job/'work/spatial_matches.json',[])]
                    return self.json(resolve_review(review))
                if parts[3]=='spatial' and len(parts)==5:
                    if read_json(job/'meta.json')['status']!='ready':raise ValueError('检索完成后可查看空间图。')
                    with LOCKS.setdefault(parts[2],threading.RLock()):
                        return self.json(spatial_preview(job,unquote(parts[4])))
                if parts[3]=='download':return self.file(job/'outputs/governance/test_result_已填充.xlsx',True)
                if parts[3]=='audit':return self.file(job/'outputs/governance/用户选择与来源.json')
                if parts[3]=='log':return self.file(job/'console.log',True)
                if parts[3]=='queue':return self.file(job/'outputs/governance/待复核队列.jsonl',True)
                if parts[3]=='unassociated':return self.file(job/'outputs/governance/未关联材料.jsonl',True)
                if parts[3]=='recognition':return self.file(job/'outputs/governance/材料识别统计.json',True)
                artifacts={'bundle':'治理成果.zip','selection':'选址意见书信息提取.xlsx','new-projects':'项目表_含补建项目.xlsx','vectors':'附件矢量清单.xlsx','missing-shp':'缺少SHP项目.xlsx','mapping':'监管号与单元号映射.xlsx','materials':'阶段材料清单.xlsx','results':'成果生成统计.json','source-errors':'来源异常清单.xlsx','resolution':'整体挂接消歧.xlsx','cad':'CAD实体清单.xlsx','cad-status':'CAD读取状态.xlsx','coordinate-pending':'矢量坐标待确认.xlsx'}
                if parts[3] in artifacts:return self.file(job/'outputs/governance'/artifacts[parts[3]],True,True)
                if parts[3]=='source' and len(parts)==5:
                    source=read_json(job/'review.json')['sources'].get(parts[4])
                    if not source or not source.get('path'):raise FileNotFoundError('来源仅有属性证据，请查看来源详情。')
                    path=Path(source['path']).resolve()
                    if not any(path.is_relative_to(p.resolve()) for p in (BASE,SOURCE)):raise ValueError('来源不在项目或数据源目录内。')
                    return self.file(path,path.suffix.lower() not in ('.pdf','.jpg','.jpeg','.png','.bmp'))
            allowed={'':'index.html','index.html':'index.html','style.css':'style.css','app.js':'app.js','spatial.js':'spatial.js','matching.js':'matching.js','mount.js':'mount.js'}
            path=urlsplit(self.path).path.lstrip('/')
            if path not in allowed:raise FileNotFoundError('页面不存在。')
            return self.file(BASE/'app/static'/allowed[path])
        except (ValueError,KeyError) as e:self.json({'error':str(e)},400)
        except FileNotFoundError as e:self.json({'error':str(e)},404)
        except Exception as e:self.json({'error':str(e)},500)
    def do_DELETE(self):
        try:
            origin=self.headers.get('Origin')
            if origin and origin!=f'http://{self.headers.get("Host")}':return self.json({'error':'不接受其他网页发起的操作。'},403)
            parts=urlsplit(self.path).path.strip('/').split('/')
            if len(parts)==3 and parts[:2]==['api','jobs']:
                job=job_path(parts[2])
                if read_json(job/'meta.json',{}).get('status') in ('queued','running'):raise ValueError('任务正在处理，暂不能删除。')
                with LOCKS.setdefault(parts[2],threading.RLock()):
                    remove_tree(job)
                LOCKS.pop(parts[2],None)
                return self.json({'deleted':parts[2]})
            if len(parts)>=3 and parts[:2]==['api','datafiles']:
                key=unquote('/'.join(parts[2:]));match=re.fullmatch(r'(work|outputs)/([^/]+)',key)
                if not match:raise ValueError('数据条目无效。')
                root,name=match.groups();path=(BASE/root/name).resolve();parent=path.parent.resolve()
                if not (parent==(BASE/root).resolve() and parent.is_relative_to(BASE.resolve())):raise ValueError('数据条目不在允许的目录内。')
                if root=='work' and name in PROTECTED_WORK_ENTRIES:raise ValueError('该项为程序依赖或任务数据，不可在此删除。')
                if not path.exists():raise FileNotFoundError('数据条目不存在，请刷新列表。')
                if path.is_dir():remove_tree(path)
                else:path.unlink()
                return self.json({'deleted':key})
            raise FileNotFoundError('接口不存在。')
        except (ValueError,KeyError) as e:self.json({'error':str(e)},400)
        except FileNotFoundError as e:self.json({'error':str(e)},404)
        except PermissionError as e:self.json({'error':str(e)},409)
        except Exception as e:self.json({'error':f'删除失败：{e}'},500)
    def do_POST(self):
        try:
            origin=self.headers.get('Origin')
            if origin and origin!=f'http://{self.headers.get("Host")}':return self.json({'error':'不接受其他网页发起的操作。'},403)
            parts=urlsplit(self.path).path.strip('/').split('/')
            if parts==['api','folder-mount','open-path']:
                payload=json.loads(self.body(65536))
                return self.json(open_local_path(payload.get('path',''),payload.get('action','folder')))
            if parts==['api','folder-mount','adjust-project']:
                payload=json.loads(self.body(65536))
                with MOUNT_LOCK:
                    catalog=ProjectCatalog(storage_dir=MOUNT_DIR,root=SOURCE)
                    try: updated=catalog.update_project_boundary(payload.get('project_id'),payload.get('new_root_rel'),payload.get('new_name'))
                    finally: catalog.close()
                    mount_update(status='interrupted',phase='目录边界已调整，请重新扫描并复核关联')
                    return self.json(dict(updated=updated))
            if parts==['api','folder-mount','scan']:
                payload=json.loads(self.body(65536)); validate_methods(payload.get('methods'))
                with MOUNT_LOCK:
                    if MOUNT_STATE['status']=='running':raise ValueError('正在扫描，请等待完成。')
                    mount_update(status='running',phase='扫描目录',errors=[],stages=[],current_archive='',current_member='',processed=0,total=0)
                    MOUNT_POOL.submit(mount_scan,payload['methods'])
                    return self.json(MOUNT_STATE,202)
            if parts==['api','folder-mount','decision']:
                payload=json.loads(self.body(65536))
                with MOUNT_LOCK:
                    if MOUNT_STATE['status']=='running':raise ValueError('扫描完成后再确认关系。')
                    if mount_view()['historical']: raise ValueError('当前为历史结果，成功扫描后才能确认关系。')
                    link=next((x for x in mount_result()['links'] if x['id']==payload.get('id')),None)
                    if not link or link['signature']!=payload.get('signature'):raise ValueError('关联证据已变化，请刷新后重新核对。')
                    status=payload.get('status')
                    if status not in ('pending','confirmed','rejected','review'):raise ValueError('关联状态无效。')
                    decisions=read_json(MOUNT_DIR/'decisions.json',{})
                    previous=decisions.get(link['id'],{})
                    history=previous.get('history',[])
                    if previous: history=history+[{k:v for k,v in previous.items() if k!='history'}]
                    decisions[link['id']]=dict(status=status,signature=link['signature'],review_context=link.get('review_context'),method_signatures=link.get('method_signatures',{'M2':link['signature']}),rule_version=RULE_VERSION,updated_at=datetime.now().isoformat(timespec='seconds'),actor='本机用户',history=history)
                    write_json(MOUNT_DIR/'decisions.json',decisions)
                    return self.json(mount_view())
            if parts in (['api','jobs'],['api','current']):
                payload=self.body() if parts==['api','jobs'] else (BASE/'test_result.xlsx').read_bytes()
                name=unquote(self.headers.get('X-Filename','test_result.xlsx'))
                if not name.lower().endswith('.xlsx'):raise ValueError('仅支持 .xlsx 文件。')
                ident=uuid.uuid4().hex[:16];job=JOBS/ident;job.mkdir(parents=True)
                (job/'test_result.xlsx').write_bytes(payload)
                try:validate_upload(job/'test_result.xlsx')
                except Exception:
                    (job/'test_result.xlsx').unlink();job.rmdir();raise
                write_json(job/'meta.json',dict(filename=name,status='queued',phase='等待检索',progress=0,created_at=datetime.now().isoformat(timespec='seconds')))
                LOCKS[ident]=threading.RLock()
                POOL.submit(execute,job)
                return self.json({'id':ident},202)
            if len(parts)==4 and parts[:2]==['api','jobs']:
                ident=parts[2];job=job_path(ident);lock=LOCKS.setdefault(ident,threading.RLock())
                with lock:
                    if parts[3]=='rerun':
                        if read_json(job/'meta.json')['status'] not in ('ready','failed'):raise ValueError('任务正在处理，请等待当前处理结束。')
                        update(job,status='queued',phase='重新检索当前任务',progress=0,error=None)
                        POOL.submit(execute,job)
                        return self.json({'id':ident},202)
                    if read_json(job/'meta.json')['status']!='ready':raise ValueError('检索未完成，暂不能选择或导出。')
                    actions={'judgement':save_judgement,'alias':save_alias,'coordinate-system':save_coordinate_system,'relationship':save_relationship_decision}
                    if parts[3] in actions:
                        result=actions[parts[3]](job,json.loads(self.body(65536)))
                        update(job,status='queued',phase='按人工证据重新匹配',progress=0);POOL.submit(refresh_matching,job)
                        return self.json(dict(result,id=ident),202)
                    if parts[3]=='georeference':return self.json(save_georeference(job,json.loads(self.body(65536))))
                    if parts[3]=='choice':
                        payload=json.loads(self.body(65536));review=save_choice(job,payload['cell'],payload.get('candidate_id'),payload.get('confirm_project') is True)
                        return self.json(review)
                    if parts[3]=='export':
                        export_review(job)
                        return self.json({'download':f'/api/jobs/{ident}/download','audit':f'/api/jobs/{ident}/audit'})
            raise FileNotFoundError('接口不存在。')
        except (ValueError,KeyError,json.JSONDecodeError) as e:self.json({'error':str(e)},400)
        except FileNotFoundError as e:self.json({'error':str(e)},404)
        except Exception as e:self.json({'error':str(e)},500)

if __name__=='__main__':
    JOBS.mkdir(parents=True,exist_ok=True)
    port=int(sys.argv[1]) if len(sys.argv)>1 else 8766
    print(f'Local: http://127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Handler).serve_forever()
