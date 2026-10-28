"""第二阶段：固定模板、七阶段取证和持久化审核；不合并目录或宗地。"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid
import zipfile
import xml.etree.ElementTree as ET
from contextlib import closing

import openpyxl
from openpyxl.utils import get_column_letter
from material_evidence import field_candidates, page_evidence, material_stage, compact_text, PATTERNS, valid_project_code, canonical_code
from entity_evidence import entity_blocks
from match_engine import area_m2, normalize
from source_config import SOURCE, STAGES

BASE = Path(__file__).resolve().parent.parent
RULE = 'stage2-2026-10-26-v1'
GROUPS = [('项目信息',24,29),('用地预审与选址',30,38),('规划条件与用地红线出具',39,46),('用地报批',47,55),('土地供应',56,69)]
KINDS = {'.pdf','.docx','.doc','.xls','.xlsx','.csv','.txt','.jpg','.jpeg','.png','.bmp','.tif','.tiff','.gif','.webp','.dbf','.shp','.dxf','.dwg'}


def source_kind(path):
    from cross_stage_materials import media_header
    with Path(path).open('rb') as stream:header=stream.read(16)
    return media_header(header) or ('.xlsx' if header.startswith(b'PK') and Path(path).suffix.lower()=='.xls' else Path(path).suffix.lower())


def dumps(value):
    return json.dumps(value,ensure_ascii=False,default=lambda v:v.isoformat() if isinstance(v,(dt.date,dt.datetime)) else str(v))


def digest(path):
    path=Path(path)
    if path.suffix.lower()=='.shp':
        parts={ext:hashlib.sha256(path.with_suffix(ext).read_bytes()).hexdigest() if path.with_suffix(ext).is_file() else None for ext in ('.shp','.shx','.dbf','.prj','.cpg')}
        return hashlib.sha256(dumps(parts).encode()).hexdigest()
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def key(*parts):
    return hashlib.sha256(dumps(parts).encode()).hexdigest()[:32]


def directory_unit_id(stage,path):
    match=re.match(PATTERNS['unit_id'],Path(path).name.upper()) if stage=='S5' else None
    return match[0] if match else None


def template_fields(path):
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist())>2000 or sum(i.file_size for i in archive.infolist())>100*1024*1024:raise ValueError('工作簿解压后超过大小限制')
    with closing(openpyxl.load_workbook(path,read_only=False,data_only=False)) as book:
        if 'Sheet1' not in book:raise ValueError('模板必须包含 Sheet1')
        sheet=book['Sheet1']
        if any(sheet.cell(2,c).value in (None,'') for c in range(1,70)):
            raise ValueError('Sheet1 第2行必须完整定义 A～BQ 的69列')
        if any(sheet.cell(2,c).value is not None for c in range(70,sheet.max_column+1)):
            raise ValueError('模板包含69列以外的字段')
        return [dict(code=get_column_letter(c),column=c,name=sheet.cell(2,c).value,
                     group=next((g for g,a,b in GROUPS if a<=c<=b),'原始标识'),
                     required='必填' in str(sheet.cell(2,c).value),format=sheet.cell(3,c).number_format,
                     type='date' if '日期' in str(sheet.cell(2,c).value) else 'number' if c in (32,40,48,49,60,61) else 'string')
                for c in range(1,70)]


class Stage2:
    def __init__(self, folder=None, source=None):
        self.folder=Path(folder or BASE/'work/stage2');self.folder.mkdir(parents=True,exist_ok=True)
        self.source=Path(source or SOURCE).resolve()
        self.db=sqlite3.connect(self.folder/'business.sqlite',timeout=30)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY,name TEXT,stage TEXT,path TEXT,original TEXT NOT NULL,active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS materials (id TEXT PRIMARY KEY,path TEXT,fingerprint TEXT,stage TEXT,kind TEXT,pages TEXT,error TEXT,active INTEGER DEFAULT 1,rule TEXT);
        CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY,material TEXT,location TEXT,business_stage TEXT,text TEXT,method TEXT,active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS relations (project TEXT,record TEXT,basis TEXT,reliable INTEGER,PRIMARY KEY(project,record));
        CREATE TABLE IF NOT EXISTS directory_links (left_id TEXT,right_id TEXT,status TEXT,evidence TEXT,PRIMARY KEY(left_id,right_id));
        CREATE TABLE IF NOT EXISTS exclusions (project TEXT,field TEXT,candidate TEXT,PRIMARY KEY(project,field,candidate));
        CREATE TABLE IF NOT EXISTS originals (project TEXT PRIMARY KEY,path TEXT,row_number INTEGER,fingerprint TEXT);
        CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY,project TEXT,record TEXT,field TEXT,raw TEXT,value TEXT,method TEXT,reliability TEXT,active INTEGER DEFAULT 1,rule TEXT);
        CREATE TABLE IF NOT EXISTS decisions (project TEXT,field TEXT,action TEXT,candidate TEXT,version TEXT,PRIMARY KEY(project,field));
        CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY,project TEXT,field TEXT,action TEXT,candidate TEXT,version TEXT,note TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY,status TEXT,progress INTEGER,total INTEGER,errors TEXT,started TEXT,finished TEXT);
        CREATE INDEX IF NOT EXISTS candidate_project ON candidates(project,active);
        CREATE INDEX IF NOT EXISTS material_path ON materials(path,active);
        ''')
        bound=self.meta('source')
        if bound and bound!=str(self.source):raise ValueError('业务数据库属于其他数据源，请使用独立存储目录')
        self.set_meta('source',str(self.source));self.db.commit()

    def close(self):self.db.close()
    def meta(self,name):
        row=self.db.execute('SELECT value FROM meta WHERE name=?',(name,)).fetchone()
        return json.loads(row[0]) if row else None
    def set_meta(self,name,value):self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(name,dumps(value)))

    def configure(self,path):
        fields=template_fields(path);signature=digest(path)
        previous=self.meta('fields')
        if previous and [{k:v for k,v in f.items() if k!='type'} for f in previous]!=[{k:v for k,v in f.items() if k!='type'} for f in fields]:raise ValueError('字段定义或格式已改变，请使用独立业务库，不能重解释已有审核结果')
        target=self.folder/'template.xlsx'
        if not target.exists():target.write_bytes(Path(path).read_bytes())
        elif digest(target)!=signature:raise ValueError('模板版本变化，请使用独立业务库')
        self.set_meta('fields',fields);self.set_meta('template_hash',signature)
        self.db.commit()
        return fields

    def sync_catalog(self,catalog=None):
        path=Path(catalog or BASE/'work/folder_mount/project_catalog.sqlite')
        if not path.is_file():raise FileNotFoundError('请先完成第一阶段项目目录识别')
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute("SELECT * FROM projects WHERE node_type='project'")]
        with self.db:
            self.db.execute('UPDATE projects SET active=0 WHERE id NOT LIKE \'import-%\'')
            for p in rows:
                physical=Path(p['physical_path']).resolve()
                if not physical.is_relative_to(self.source) or not physical.is_dir():continue
                # 目录名称是检索线索，不作为已核实的 X 列项目名称。
                self.db.execute('INSERT INTO projects VALUES (?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET name=excluded.name,stage=excluded.stage,path=excluded.path,active=1',
                                (p['id'],p['project_name'],p['stage_id'],str(physical),'{}'))
            self.set_meta('catalog',dict(path=str(path),synced=dt.datetime.now().isoformat()))
        return self.db.execute('SELECT count(*) FROM projects WHERE active=1').fetchone()[0]

    def sync_links(self,links):
        with self.db:
            self.db.execute('DELETE FROM directory_links')
            for link in links:
                self.db.execute('INSERT OR REPLACE INTO directory_links VALUES (?,?,?,?)',
                    (link['left']['id'],link['right']['id'],link.get('status','pending'),dumps({k:v for k,v in link.items() if k not in ('evidence','method_evidence')})))

    def import_values(self,path):
        """仅显式导入真实业务表；模板第三行从不自动导入。"""
        if [(f['code'],f['name']) for f in template_fields(path)]!=[(f['code'],f['name']) for f in self.meta('fields') or []]:raise ValueError('导入表字段必须与模板完全一致')
        signature=digest(path);count=0
        with closing(openpyxl.load_workbook(path,read_only=True,data_only=False)) as book, self.db:
            for row,values in enumerate(book['Sheet1'].iter_rows(min_row=3,max_col=69,values_only=True),3):
                if not any(v is not None for v in values):continue
                ident='import-'+key(str(Path(path).resolve()),row)
                original={get_column_letter(c):(v.date().isoformat() if isinstance(v,dt.datetime) else v.isoformat() if isinstance(v,dt.date) else v) for c,v in enumerate(values,1) if v is not None}
                old=self.db.execute('SELECT original FROM projects WHERE id=?',(ident,)).fetchone()
                if old and json.loads(old[0])!=json.loads(dumps(original)):raise ValueError('已导入原始值变化，请使用新的业务表路径并核对差异')
                self.db.execute('INSERT OR IGNORE INTO projects VALUES (?,?,?,?,?,1)',(ident,str(values[23] or values[9] or values[2] or ident),'',str(Path(path).resolve()),dumps(original)))
                self.db.execute('INSERT OR REPLACE INTO originals VALUES (?,?,?,?)',(ident,str(Path(path).resolve()),row,signature))
                count+=1
            self.set_meta('import',dict(path=str(Path(path).resolve()),fingerprint=signature,rows=count))
        return count

    def targets(self):
        result=[];confirmed={};versions={}
        for chosen in self.db.execute('''SELECT d.project,d.field,c.value,m.path,m.fingerprint,b.basis FROM decisions d
          JOIN candidates c ON c.id=d.candidate JOIN records r ON r.id=c.record JOIN materials m ON m.id=r.material
          JOIN relations b ON b.project=d.project AND b.record=c.record
          WHERE d.action='adopt' AND c.active=1 AND r.active=1 AND m.active=1 AND m.error IS NULL'''):
            path=Path(chosen['path'])
            if not path.is_file():continue
            if chosen['path'] not in versions:versions[chosen['path']]=digest(path)
            spatial=json.loads(chosen['basis']).get('spatial')
            if spatial:
                anchor=Path(spatial['anchor'])
                if not anchor.is_file():continue
                if str(anchor) not in versions:versions[str(anchor)]=digest(anchor)
                if versions[str(anchor)]!=spatial.get('anchor_version'):continue
            if versions[chosen['path']]==chosen['fingerprint']:confirmed.setdefault(chosen['project'],{})[chosen['field']]=json.loads(chosen['value'])
        for row in self.db.execute('SELECT * FROM projects WHERE active=1'):
            original=dict(json.loads(row['original']),**confirmed.get(row['id'],{}))
            codes={canonical_code(c) for c in re.findall(PATTERNS['project_code'],row['path']) if valid_project_code(c)}
            folder_unit=directory_unit_id(row['stage'],row['path'])
            inferred=([] if original.get('Y') else ['project_code'])+(['unit_id'] if folder_unit and not original.get('O') else [])
            result.append(dict(project=row['id'],name=original.get('X') or row['name'],path=row['path'],
                project_code=original.get('Y') or (next(iter(codes)) if len(codes)==1 else None),inferred_keys=inferred,regulator_id=original.get('R'),contract_id=original.get('BO') or original.get('S'),
                unit_id=original.get('O') or folder_unit,condition_id=original.get('AQ'),approval_id=original.get('AZ'),certificate_id=original.get('AI'),address=original.get('BG') or original.get('AD') or original.get('AP')))
        return result

    def ingest(self,path,pages,method='原生文字',fingerprint=None):
        """每个实体一条业务记录；候选和审核绑定原件内容及规则版本。"""
        path=Path(path).resolve()
        if not path.is_file() or not path.is_relative_to(self.source):raise ValueError('来源必须是真实数据源文件')
        stage=next((s for s,f in STAGES if path.is_relative_to(self.source/f)),None)
        if not stage:raise ValueError('来源不在七阶段有效目录中')
        fingerprint=fingerprint or digest(path);mid=key(str(path),fingerprint)
        targets=self.targets();by_id={t['project']:t for t in targets}
        self.db.execute('UPDATE materials SET active=0 WHERE path=?',(str(path),))
        self.db.execute('UPDATE records SET active=0 WHERE material IN (SELECT id FROM materials WHERE path=?)',(str(path),))
        self.db.execute('''UPDATE candidates SET active=0 WHERE record IN
          (SELECT id FROM records WHERE material IN (SELECT id FROM materials WHERE path=?))
          AND NOT(method='Agent语义识别' AND rule=? AND record IN (SELECT id FROM records WHERE material=?))''',(str(path),RULE,mid))
        self.db.execute('INSERT OR REPLACE INTO materials VALUES (?,?,?,?,?,?,NULL,1,?)',(mid,str(path),fingerprint,stage,source_kind(path),dumps(pages),RULE))
        for page_index,page in enumerate(pages,1):
            text=page['text'] if isinstance(page,dict) else page
            location=page.get('location',f'page={page_index}') if isinstance(page,dict) else f'page={page_index}'
            for block in entity_blocks(text):
                body=block['text'];rid=key(mid,location,block['id'],RULE)
                if not body.strip():continue
                business=material_stage(body)
                self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,1)',(rid,mid,location+';'+block['id'],business,body,method))
                ev=page_evidence(body,targets);fields=field_candidates(body,ev['fields'])
                fields=complete_candidates(body,fields)
                manual={r['project']:dict(r) for r in self.db.execute('SELECT * FROM relations WHERE record=?',(rid,)) if r['reliable'] or json.loads(r['basis']).get('agent_reason')}
                for relation in self.db.execute('SELECT * FROM relations WHERE record=?',(rid,)).fetchall():
                    if relation['project'] not in manual:self.db.execute('DELETE FROM relations WHERE project=? AND record=?',(relation['project'],rid))
                hints={t['project'] for t in targets if path.is_relative_to(Path(t['path'])) and Path(t['path']).is_dir()}
                possible=set(ev['page_project_candidates'])|hints
                related={}
                for link in self.db.execute("SELECT * FROM directory_links WHERE status!='rejected'"):
                    if link['left_id'] in hints:related[link['right_id']]=dict(link)
                    if link['right_id'] in hints:related[link['left_id']]=dict(link)
                possible.update(set(related)&by_id.keys())
                possible.update(set(manual)&by_id.keys())
                # M4 只扩大候选范围，精确代码和同名也不合并宗地。
                if not possible:
                    from difflib import SequenceMatcher
                    from address_match import address_match
                    names=re.findall(r'(?:建设项目名称|项目名称)[:：]([^，,。；;:：\n]{2,120})',body)
                    locations=re.findall(r'(?:建设地点|坐落|建设位置|宗地位置|用地位置)[:：]([^，,。；;:：\n]{2,120})',body)
                    possible.update(t['project'] for t in targets if any(SequenceMatcher(None,normalize(t['name']),normalize(n)).ratio()>=.8 for n in names))
                    possible.update(t['project'] for t in targets if t.get('address') and any(address_match(t['address'],loc)['score']>=.8 for loc in locations))
                # 同名、代码和目录都只能建立关系候选；不批量确认所有引用。
                for pid in possible:
                    basis=dict(identity=ev['identity_reason'],conflicts=ev['identity_conflicts'],
                               exact=pid in ev['projects'],directory_hint=pid in hints,segmentation=block['method'],directory_relation=related.get(pid))
                    target=by_id[pid]
                    if 'unit_id' in target.get('inferred_keys',[]):basis['directory_unit_id']=dict(value=target['unit_id'],path=target['path'],rule='S5目录名开头完整28位不动产单元号')
                    # 合同或监管号可能跨宗地，自动补齐还须唯一不动产单元号。
                    parcel_match=bool(target.get('unit_id') and 'unit_id' not in target.get('inferred_keys',[]) and len(ev['fields'].get('unit_id',[]))==1 and normalize(target['unit_id'])==normalize(ev['fields']['unit_id'][0]))
                    reliable=pid in ev['projects'] and parcel_match and len(possible)==1 and method in ('原生文字','PDF原生文字','Word原生文字')
                    if pid not in manual:self.db.execute('INSERT OR REPLACE INTO relations VALUES (?,?,?,?)',(pid,rid,dumps(basis),0))
                    for item in fields:
                        col=item['column']
                        if not 24<=col<=69:continue
                        field=get_column_letter(col);value=item['value']
                        cid=key(pid,rid,field,value,RULE)
                        self.db.execute('INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?,?,?,1,?)',
                            (cid,pid,rid,field,dumps(item['evidence']),dumps(value),method,'精确宗地标识唯一且无冲突' if reliable else '需核对实体归属及字段原文',RULE))
        return mid

    def scan(self,limit=None):
        if not self.meta('fields'):raise ValueError('请先配置 test.xlsx 模板')
        tid=uuid.uuid4().hex;errors=[];done=0
        files=[]
        for _,folder in STAGES:
            root=self.source/folder
            if not root.is_dir():errors.append(dict(path=str(root),error='阶段目录缺失'));continue
            for p in root.rglob('*'):
                if not p.is_file() or p.name.startswith('~$') or not p.resolve().is_relative_to(self.source):continue
                if p.suffix.lower() in ('.zip','.rar','.7z') and not p.with_name(p.name+'.解压').is_dir():
                    errors.append(dict(path=str(p),error='压缩包未发现原地解压目录，请先运行第一阶段并核对索引'))
                elif p.suffix.lower() in KINDS:files.append(p)
                elif p.suffix.lower() in ('','.bin'):
                    try:
                        if source_kind(p) in KINDS:files.append(p)
                    except OSError as error:errors.append(dict(path=str(p),error=str(error)))
        files=sorted({p for p in files if p.resolve().is_relative_to(self.source)},key=lambda p:(not any(t in p.name for t in ('合同','决定书','批复')),next(s for s,f in STAGES if p.is_relative_to(self.source/f)),str(p)))
        if limit is not None:files=files[:limit]
        with self.db:self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,NULL)',(tid,'running',0,len(files),'[]',dt.datetime.now().isoformat()))
        try:
            for path in files:
                try:
                    fingerprint=digest(path)
                    old=self.db.execute('SELECT * FROM materials WHERE path=? AND fingerprint=? AND active=1 AND error IS NULL AND rule=?',(str(path.resolve()),fingerprint,RULE)).fetchone()
                    if old:
                        with self.db:self.ingest(path,json.loads(old['pages']),fingerprint=fingerprint,method=self.db.execute('SELECT method FROM records WHERE material=? LIMIT 1',(old['id'],)).fetchone()[0])
                    else:
                        pages,method=read_material(path,self.folder/key(str(path),fingerprint))
                        with self.db:self.ingest(path,pages,method,fingerprint)
                        errors.extend(dict(path=str(path),location=p.get('location'),error=p['error']) for p in pages if isinstance(p,dict) and p.get('error'))
                except Exception as error:
                    errors.append(dict(path=str(path),error=f'{type(error).__name__}: {error}'))
                    with self.db:
                        self.db.execute('UPDATE materials SET active=0 WHERE path=?',(str(path.resolve()),))
                        self.db.execute('INSERT OR REPLACE INTO materials VALUES (?,?,?,?,?,?,?,1,?)',
                            (key(str(path),str(error)),str(path.resolve()),None,next(s for s,f in STAGES if path.is_relative_to(self.source/f)),path.suffix,'[]',str(error),RULE))
                done+=1
                with self.db:self.db.execute('UPDATE tasks SET progress=?,errors=? WHERE id=?',(done,dumps(errors),tid))
            # 未扫描的局部任务不撤销其他材料；文件删除始终使来源失效。
            for row in self.db.execute('SELECT id,path FROM materials WHERE active=1').fetchall():
                if not Path(row['path']).is_file():self.db.execute('UPDATE materials SET active=0 WHERE id=?',(row['id'],))
            status='partial' if limit is not None or errors else 'ready'
            with self.db:self.db.execute('UPDATE tasks SET status=?,finished=? WHERE id=?',(status,dt.datetime.now().isoformat(),tid))
        except BaseException:
            with self.db:self.db.execute("UPDATE tasks SET status='interrupted',finished=? WHERE id=?",(dt.datetime.now().isoformat(),tid))
            raise
        return dict(id=tid,status=status,processed=done,total=len(files),errors=errors)

    def candidate_rows(self,pid,verify=False):
        rows=[dict(r) for r in self.db.execute('''SELECT c.*,r.location,r.business_stage,r.text,m.path,m.fingerprint,m.stage,b.basis
          FROM candidates c JOIN records r ON c.record=r.id JOIN materials m ON r.material=m.id
          JOIN relations b ON b.project=c.project AND b.record=c.record
          WHERE c.project=? AND c.active=1 AND r.active=1 AND m.active=1 AND m.error IS NULL''',(pid,))]
        versions={}
        for row in rows:
            path=Path(row['path']);valid=path.is_file()
            if valid and verify:
                if row['path'] not in versions:versions[row['path']]=digest(path)
                valid=versions[row['path']]==row['fingerprint']
            row['valid']=valid
            for name in ('value','raw','basis'):row[name]=json.loads(row[name])
            spatial=row['basis'].get('spatial')
            if spatial and verify:
                anchor=Path(spatial['anchor'])
                if not anchor.is_file() or not anchor.resolve().is_relative_to(self.source):row['valid']=False
                else:
                    if str(anchor) not in versions:versions[str(anchor)]=digest(anchor)
                    row['valid']=row['valid'] and versions[str(anchor)]==spatial.get('anchor_version')
        return rows

    def project(self,pid,verify=True):
        row=self.db.execute('SELECT * FROM projects WHERE id=? AND active=1',(pid,)).fetchone()
        if not row:raise ValueError('项目不存在或第一阶段边界已失效')
        result=dict(row);original=json.loads(row['original']);candidates=self.candidate_rows(pid,verify)
        result['directory_unit_id']=directory_unit_id(row['stage'],row['path'])
        origin=self.db.execute('SELECT * FROM originals WHERE project=?',(pid,)).fetchone()
        result['original_source']=dict(origin) if origin else None
        decisions={r['field']:dict(r) for r in self.db.execute('SELECT * FROM decisions WHERE project=?',(pid,))}
        excluded={r[0] for r in self.db.execute('SELECT candidate FROM exclusions WHERE project=?',(pid,))}
        result['fields']=[]
        for field in self.meta('fields') or []:
            if field['column']<24:continue
            code=field['code'];old=original.get(code);options=[dict(c,excluded=c['id'] in excluded) for c in candidates if c['field']==code]
            usable=[c for c in options if c['valid'] and not c['excluded']]
            decision=decisions.get(code);value=old;state='original' if old is not None else 'empty';chosen=None
            if options:state='conflict' if old is not None and any(c['value']!=old for c in options) or len({dumps(c['value']) for c in options})>1 else 'candidate'
            if old is None and usable and len({dumps(c['value']) for c in usable})==1 and all(c['reliability']=='精确宗地标识唯一且无冲突' for c in usable):
                value=usable[0]['value'];state='filled'
            if decision:
                chosen=next((c for c in usable if c['id']==decision['candidate']),None)
                if decision['action']=='adopt':
                    if chosen:value=chosen['value'];state='selected'
                    else:state='stale'
                else:state=decision['action']
            result['fields'].append(dict(**field,original=old,value=value,status=state,candidates=options,decision=decision))
        result['history']=[dict(r) for r in self.db.execute('SELECT * FROM history WHERE project=? ORDER BY id DESC',(pid,))]
        result['relations']=[dict(r) for r in self.db.execute('SELECT * FROM relations WHERE project=?',(pid,))]
        return result

    def review(self,payload):
        pid=payload.get('project');code=payload.get('field');action=payload.get('action');cid=payload.get('candidate')
        if action not in ('adopt','exclude','keep','review','undo'):raise ValueError('审核动作无效')
        project=self.project(pid);field=next((f for f in project['fields'] if f['code']==code),None)
        if not field:raise ValueError('字段不在模板目标范围')
        candidate=next((c for c in field['candidates'] if c['id']==cid and c['valid']),None)
        if action in ('adopt','exclude') and not candidate:raise ValueError('候选来源已改变或不存在，请重新扫描')
        if action=='adopt' and payload.get('confirm_identity') is not True:raise ValueError('请核对原件并确认当前字段属于本项目及本宗地')
        version=key(candidate['fingerprint'],candidate['record'],RULE) if candidate else None
        with self.db:
            if action=='undo':
                self.db.execute('DELETE FROM decisions WHERE project=? AND field=?',(pid,code))
                self.db.execute('DELETE FROM exclusions WHERE project=? AND field=?',(pid,code))
            elif action=='exclude':
                self.db.execute('INSERT OR IGNORE INTO exclusions VALUES (?,?,?)',(pid,code,cid))
                self.db.execute('DELETE FROM decisions WHERE project=? AND field=?',(pid,code))
            else:self.db.execute('INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?)',(pid,code,action,cid,version))
            self.db.execute('INSERT INTO history(project,field,action,candidate,version,note,created) VALUES (?,?,?,?,?,?,?)',
                (pid,code,action,cid,version,payload.get('note',''),dt.datetime.now().isoformat()))
        return self.project(pid)

    def records(self,query=''):
        return [dict(r) for r in self.db.execute('''SELECT r.id,r.location,r.business_stage,substr(r.text,1,1600) AS text,m.path,m.stage,
          (SELECT count(*) FROM relations b WHERE b.record=r.id) AS projects
          FROM records r JOIN materials m ON r.material=m.id
          WHERE r.active=1 AND m.active=1 AND m.error IS NULL AND instr(lower(r.text || m.path),lower(?))>0
          ORDER BY projects,r.id LIMIT 50''',(query,))]

    def propose(self,payload):
        """Agent 语义读取可提交候选；原文片段和真实记录必须通过程序校验。"""
        pid=payload.get('project');rid=payload.get('record');code=payload.get('field')
        self.project(pid)
        field=next((f for f in self.meta('fields') if f['code']==code and f['column']>=24),None)
        if not field:raise ValueError('候选字段不在模板目标范围')
        row=self.db.execute('SELECT r.*,m.path,m.fingerprint FROM records r JOIN materials m ON r.material=m.id WHERE r.id=? AND r.active=1 AND m.active=1 AND m.error IS NULL',(rid,)).fetchone()
        if not row or not Path(row['path']).is_file() or digest(row['path'])!=row['fingerprint']:raise ValueError('原件或记录版本已失效')
        raw=payload.get('raw');value=payload.get('value');reason=payload.get('reason')
        if not isinstance(raw,str) or len(compact_text(raw))<2 or compact_text(raw) not in compact_text(row['text']):raise ValueError('原文证据必须能够在当前业务记录中找到')
        if not isinstance(reason,str) or not reason.strip():raise ValueError('请说明字段含义及归属依据')
        if value is None or isinstance(value,(list,dict,bool)) or value=='':raise ValueError('候选值类型无效')
        expected={'预审与选址':(30,38),'规划条件与红线':(39,46),'用地报批':(47,55),'土地供应':(56,69)}
        if field['column']>=30:
            actual=payload.get('business_stage') or row['business_stage']
            bounds=expected.get(actual)
            if not bounds or not bounds[0]<=field['column']<=bounds[1]:raise ValueError('业务文书阶段与目标字段分组不一致')
        if '日期' in str(field['name']):
            value=dt.date.fromisoformat(str(value)).isoformat()
        if code in ('AF','AN','AV','AW','BH','BI'):
            import math
            if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('面积或投资必须是有效非负数值')
        elif '日期' not in str(field['name']) and not isinstance(value,str):raise ValueError('文号、编号和其他文本字段必须保存为字符串')
        cid=key(pid,rid,code,value,RULE,'agent')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO relations VALUES (?,?,?,0)',(pid,rid,dumps(dict(agent_reason=reason,raw=raw))))
            self.db.execute('INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?,?,?,1,?)',
                (cid,pid,rid,code,dumps(raw),dumps(value),'Agent语义识别','原文片段已校验；归属与取值需审核',RULE))
        return dict(candidate=cid,project=pid,field=code)

    def attach(self,payload):
        pid=payload.get('project');rid=payload.get('record');note=payload.get('note','').strip()
        self.project(pid)
        if payload.get('confirm_identity') is not True or not note:raise ValueError('请填写原件核对、空间叠加或人工判读依据并确认记录归属')
        row=self.db.execute('SELECT r.*,m.path,m.fingerprint FROM records r JOIN materials m ON r.material=m.id WHERE r.id=? AND r.active=1 AND m.active=1 AND m.error IS NULL',(rid,)).fetchone()
        if not row or not Path(row['path']).is_file() or digest(row['path'])!=row['fingerprint']:raise ValueError('业务记录来源版本已改变')
        spatial=payload.get('spatial')
        if payload.get('method')=='M3':
            if not isinstance(spatial,dict) or spatial.get('record')!=rid:raise ValueError('缺少当前图斑空间证据')
            anchor=Path(spatial.get('anchor','')).resolve()
            if not anchor.is_relative_to(self.source) or not anchor.is_file() or digest(anchor)!=spatial.get('anchor_version') or row['fingerprint']!=spatial.get('source_version'):raise ValueError('空间参考或原件已改变，请重新计算')
        ev=page_evidence(row['text'],self.targets())
        fields=complete_candidates(row['text'],field_candidates(row['text'],ev['fields']))
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO relations VALUES (?,?,?,1)',(pid,rid,dumps(dict(manual=note,method=payload.get('method','M5'),version=row['fingerprint'],spatial=spatial))))
            for item in fields:
                if not 24<=item['column']<=69:continue
                field=get_column_letter(item['column']);cid=key(pid,rid,field,item['value'],RULE)
                self.db.execute('INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?,?,?,?,1,?)',
                    (cid,pid,rid,field,dumps(item['evidence']),dumps(item['value']),row['method'],'人工确认记录归属；字段仍需采用',RULE))
            self.db.execute('INSERT INTO history(project,field,action,candidate,version,note,created) VALUES (?,?,?,?,?,?,?)',
                (pid,'','attach',rid,row['fingerprint'],note,dt.datetime.now().isoformat()))
        return self.project(pid)

    def spatial(self,pid):
        """M3 复用投影/真实交集；只返回局部重叠证据，不自动确认权属。"""
        from shapely.geometry import shape,mapping
        from gis_geometry import reproject,projected_crs
        project=self.project(pid);features=[];errors=[]
        for material in self.db.execute('SELECT * FROM materials WHERE active=1 AND error IS NULL'):
            if not Path(material['path']).is_file() or digest(material['path'])!=material['fingerprint']:continue
            for page in json.loads(material['pages']):
                if not isinstance(page,dict) or not page.get('geometry'):continue
                if not page.get('crs'):
                    errors.append(dict(path=material['path'],error='坐标系缺失，需人工指定并重算'));continue
                features.append(dict(path=material['path'],fingerprint=material['fingerprint'],material=material['id'],**page))
        anchors=[f for f in features if Path(f['path']).is_relative_to(Path(project['path'])) and Path(project['path']).is_dir()]
        result=[]
        for anchor in anchors:
            try:projected_crs(anchor['crs']);a=shape(anchor['geometry'])
            except Exception as error:
                errors.append(dict(path=anchor['path'],error=str(error)));continue
            if a.is_empty or a.area<=0:continue
            for target in features:
                if target['material']==anchor['material']:continue
                try:
                    b=reproject(shape(target['geometry']),target['crs'],anchor['crs'])
                    intersection=a.intersection(b).area
                    if intersection<=0 or b.area<=0:continue
                    record=self.db.execute('SELECT id FROM records WHERE material=? AND location=? AND active=1',(target['material'],target['location']+';block-1')).fetchone()
                    if record:result.append(dict(record=record[0],source=target['path'],anchor=anchor['path'],anchor_location=anchor['location'],location=target['location'],intersection_m2=intersection,anchor_coverage=intersection/a.area,target_coverage=intersection/b.area,reference_crs=anchor['crs'],source_version=target['fingerprint'],anchor_version=anchor['fingerprint'],geometry=mapping(b),anchor_geometry=mapping(a),intersection=mapping(a.intersection(b)),method='M3真实空间相交；局部相交不等于权属一致'))
                except Exception as error:errors.append(dict(path=target['path'],error=str(error)))
        return dict(evidence=sorted(result,key=lambda r:r['intersection_m2'],reverse=True)[:100],errors=errors)

    def overview(self,query='',page=1):
        count=self.db.execute('SELECT count(*) FROM projects WHERE active=1 AND instr(lower(name || path),lower(?))>0',(query,)).fetchone()[0]
        page=max(1,min(page,max(1,(count+29)//30)))
        rows=[]
        for row in self.db.execute('SELECT * FROM projects WHERE active=1 AND instr(lower(name || path),lower(?))>0 ORDER BY stage,name LIMIT 30 OFFSET ?',(query,(page-1)*30)):
            item=dict(row);original=json.loads(item.pop('original'))
            item['archive']=original.get('B');item['original_fields']=sum(original.get(get_column_letter(c)) is not None for c in range(24,70))
            fields=self.project(row['id'],verify=False)['fields']
            item['completed_fields']=sum(f['value'] is not None for f in fields)
            item['required_fields']=sum(f['required'] for f in fields)
            item['completed_required']=sum(f['required'] and f['value'] is not None for f in fields)
            item['related_stages']=[r[0] for r in self.db.execute('''SELECT DISTINCT p.stage FROM directory_links d JOIN projects p
              ON p.id=CASE WHEN d.left_id=? THEN d.right_id ELSE d.left_id END
              WHERE (d.left_id=? OR d.right_id=?) AND d.status!='rejected' AND p.active=1 ORDER BY p.stage''',(row['id'],row['id'],row['id']))]
            item['candidates']=self.db.execute('SELECT count(*) FROM candidates WHERE project=? AND active=1',(row['id'],)).fetchone()[0]
            item['reviewed']=self.db.execute('SELECT count(*) FROM decisions WHERE project=?',(row['id'],)).fetchone()[0]
            rows.append(item)
        task=self.db.execute('SELECT * FROM tasks ORDER BY started DESC LIMIT 1').fetchone()
        return dict(projects=rows,count=count,page=page,pages=max(1,(count+29)//30),fields=self.meta('fields'),task=dict(task) if task else None,rule=RULE)

    def preview(self):
        """只改原包 Sheet1 数据行；原始表头、合并和其他包内容原样保留。"""
        template=self.folder/'template.xlsx'
        if digest(template)!=self.meta('template_hash'):raise ValueError('模板原件版本不一致')
        ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main';tag=lambda n:'{'+ns+'}'+n
        ET.register_namespace('',ns)
        audit=[]
        with zipfile.ZipFile(template) as src:
            styles=ET.fromstring(src.read('xl/styles.xml'));xfs=styles.find(tag('cellXfs'));date_style=str(len(xfs))
            ET.SubElement(xfs,tag('xf'),numFmtId='14',fontId='0',fillId='0',borderId='0',xfId='0',applyNumberFormat='1');xfs.set('count',str(len(xfs)))
            workbook=ET.fromstring(src.read('xl/workbook.xml'))
            sheet=next(s for s in workbook.find(tag('sheets')) if s.attrib['name']=='Sheet1')
            relid=sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            rels=ET.fromstring(src.read('xl/_rels/workbook.xml.rels'))
            target=next(r.attrib['Target'] for r in rels if r.attrib['Id']==relid)
            member=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            root=ET.fromstring(src.read(member));data=root.find(tag('sheetData'))
            for row in list(data):
                if int(row.attrib['r'])>=3:data.remove(row)
            projects=self.db.execute('SELECT id,original FROM projects WHERE active=1 ORDER BY stage,name').fetchall()
            for index,project in enumerate(projects,3):
                detail=self.project(project['id']);original=json.loads(project['original'])
                values=dict(original)
                for field in detail['fields']:
                    if field['value'] is not None:values[field['code']]=field['value']
                    if field['status'] in ('selected','filled'):
                        chosen=next(c for c in field['candidates'] if (c['id']==field['decision']['candidate'] if field['status']=='selected' else c['valid'] and not c['excluded']))
                        audit.append(dict(project=project['id'],field=field['code'],value=field['value'],candidate=chosen))
                row=ET.SubElement(data,tag('row'),r=str(index))
                for code,value in values.items():
                    cell=ET.SubElement(row,tag('c'),r=f'{code}{index}')
                    if code in ('AJ','AR','AY','BA','BP') and isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
                        from openpyxl.utils.datetime import to_excel
                        cell.set('s',date_style);ET.SubElement(cell,tag('v')).text=str(to_excel(dt.date.fromisoformat(value)))
                    elif code in original and value==original[code] and isinstance(value,str) and value.startswith('='):
                        ET.SubElement(cell,tag('f')).text=value[1:]
                    elif isinstance(value,(int,float)) and not isinstance(value,bool):ET.SubElement(cell,tag('v')).text=str(value)
                    else:
                        cell.set('t','inlineStr');ET.SubElement(ET.SubElement(cell,tag('is')),tag('t')).text=str(value)
            dimension=root.find(tag('dimension'))
            if dimension is not None:dimension.set('ref',f'A1:BQ{max(2,len(projects)+2)}')
            output=self.folder/'preview.xlsx';temp=output.with_suffix('.tmp')
            with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as dest:
                for info in src.infolist():
                    content=ET.tostring(root,encoding='utf-8',xml_declaration=True) if info.filename==member else ET.tostring(styles,encoding='utf-8',xml_declaration=True) if info.filename=='xl/styles.xml' else src.read(info.filename)
                    dest.writestr(info,content)
            temp.replace(output)
        (self.folder/'preview-audit.json').write_text(dumps(audit),encoding='utf-8')
        return output


def complete_candidates(text,items):
    """沿用既有分类/字段提取器；补足模板原值和标准值的区别。"""
    stage=material_stage(text);compact=compact_text(text)
    def add(col,value,raw):items.append(dict(column=col,value=value,evidence=raw,material_stage=stage))
    for m in re.finditer(r'(?:建设项目名称|项目名称)[:：]([^，,。；;:：\n]{2,120}?)(?=项目代码|建设单位|建设地点|拟用地面积|宗地面积|[，,。；;\n]|$)',text):add(24,m[1].strip(),m[0])
    for m in re.finditer(r'项目类型[:：]([^，,。；;:：\n]{1,100})(?=[，,。；;\n]|$)',text):add(27,m[1].strip(),m[0])
    labels={
        '未分类':[(26,'建设单位|建设单位名称|单位名称')],
        '预审与选址':[(38,'是否完成征转')],
        '规划条件与红线':[],
        '用地报批':[(50,'是否完成征转')],
        '土地供应':[(64,'是否完成征转')],
    }
    for col,label in labels.get(stage,[]):
        for m in re.finditer(r'(?:'+label+r')[:：]([^，,。；;:：\n]{1,120})(?=[，,。；;\n]|$)',text):add(col,m[1].strip(),m[0])
    # 指定数值列只接受带明确标签的数值，不把批次总面积分摊给项目。
    if stage=='用地报批':
        for m in re.finditer(r'征收(?:土地)?面积[:：](\d+(?:\.\d+)?)(平方米|公顷|亩)',compact):
            add(49,float(m[1]),m[0]);add(55,m[2],m[0])
        for m in re.finditer(r'报批日期[:：]?(20\d{2})年(\d{1,2})月(\d{1,2})日',compact):
            try:add(51,dt.date(int(m[1]),int(m[2]),int(m[3])).isoformat(),m[0])
            except ValueError:continue
    if stage=='土地供应':
        for item in list(items):
            if item['column']==57:add(58,item['value'],item['evidence'])
    for value_col,unit_col,raw_col,standard_col in ((32,37,31,None),(40,46,None,None),(48,54,None,None),(61,62,60,61)):
        values=[i for i in items if i['column']==value_col];units=[i for i in items if i['column']==unit_col]
        if len(values)==1 and len(units)==1:
            amount=values[0];unit=units[0];standard=area_m2(amount['value'],unit['value'])
            # BH 明确以平方米保存；原始文字及原单位留在独立来源证据。
            if raw_col==60 and standard is not None:add(raw_col,standard,amount['evidence'])
            if standard_col and standard is not None:
                amount['value']=standard;unit['value']='平方米'
    return items


def read_material(path,cache):
    cache.mkdir(parents=True,exist_ok=True);suffix=source_kind(path)
    if suffix in ('.xlsx','.csv'):
        if zipfile.is_zipfile(path):
            with path.open('rb') as stream, closing(openpyxl.load_workbook(stream,read_only=True,data_only=True)) as book:
                return [dict(location=f'{s.title}!row={i}',text=table_text(labels,row))
                    for s in book for labels in [[str(v or '') for v in next(s.iter_rows(values_only=True),())]]
                    for i,row in enumerate(s.iter_rows(min_row=2,values_only=True),2) if any(v is not None for v in row)],'Excel逐行'
        import csv
        text=path.read_bytes()
        try:text=text.decode('utf-8-sig')
        except UnicodeDecodeError:text=text.decode('gb18030')
        rows=list(csv.reader(text.splitlines()));labels=rows[0] if rows else []
        return [dict(location=f'row={i}',text=table_text(labels,row)) for i,row in enumerate(rows[1:],2)],'CSV逐行'
    if suffix=='.txt':
        raw=path.read_bytes()
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:text=raw.decode('gb18030')
        return [dict(location=f'lines=1-{len(text.splitlines())}',text=text)],'文本'
    if suffix=='.docx':
        with zipfile.ZipFile(path) as archive:
            root=ET.fromstring(archive.read('word/document.xml'));ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            paragraphs=root.findall('.//w:p',ns)
            return [dict(location=f'word/document.xml;paragraphs=1-{len(paragraphs)}',text='\n'.join(''.join(p.itertext()) for p in paragraphs))],'Word原生文字'
    if suffix=='.dbf':
        from inspect_sources import dbf
        _,rows=dbf(path)
        return [dict(location=f'OBJECTID={r["OBJECTID"]}' if 'OBJECTID' in r else f'active-record={i}',text='；'.join(f'{k}：{v}' for k,v in r.items())) for i,r in enumerate(rows,1)],'DBF对象'
    if suffix in ('.dwg','.dxf','.shp'):
        from gis_geometry import cad_entities
        entities,_=cad_entities(path)
        crs=path.with_suffix('.prj').read_text(errors='replace') if path.with_suffix('.prj').is_file() else None
        return [dict(location=f'object={e["layer"]}:{e["id"]}',text=table_text(list(e['attributes']),list(e['attributes'].values())),geometry=e['geometry'],crs=crs) for e in entities],'矢量对象属性'
    if suffix in ('.doc','.xls'):
        from extract_pdfs import office_pdf
        path=office_pdf(path,cache);suffix='.pdf'
    if suffix=='.pdf':
        from extract_pdfs import read_pdf_text
        pages=read_pdf_text(path)
        if all(p.strip() for p in pages):return pages,'PDF原生文字'
        images=[]
        node=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
        for i,page in enumerate(pages):
            if page.strip():continue
            image=cache/f'page-{i+1}.png'
            rendered=subprocess.run([str(node),str(BASE/'scripts/render_pdfs.mjs'),'--page',str(path),str(i+1),str(image.resolve())],capture_output=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if rendered.returncode:raise ValueError('PDF渲染失败：'+rendered.stderr.decode(errors='replace')[-500:])
            images.append(dict(path=str(image.resolve()),text=str(image.with_suffix('.txt').resolve()),source=f'{path}#page={i+1}',index=i))
    else:
        from image_materials import prepare_image
        image=cache/'image.png';prepare_image(path,image)
        pages=[''];images=[dict(path=str(image.resolve()),text=str(image.with_suffix('.txt').resolve()),source=str(path),index=0)]
    manifest=cache/'manifest.json';manifest.write_text(dumps(images),encoding='utf-8')
    result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(BASE/'scripts/ocr_images.ps1'),'-Manifest',str(manifest.resolve())],capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('Windows OCR 失败：'+result.stderr.decode(errors='replace')[-500:])
    for item in images:
        text=Path(item['text'])
        if not text.is_file() or not text.read_text(encoding='utf-8').strip():
            error_path=Path(str(text)+'.error')
            reason=error_path.read_text(errors='replace') if error_path.is_file() else '页面为空或没有读取到有效文字'
            pages[item['index']]=dict(text='',location=f'page={item["index"]+1}',error=reason)
        else:pages[item['index']]=text.read_text(encoding='utf-8')
    if not any((p.get('text') if isinstance(p,dict) else p).strip() for p in pages):raise ValueError('整份材料没有有效文字，查看缓存 ocr_errors.json 并人工复核')
    return pages,'原生文字/Windows OCR'


def table_text(labels,row):
    # 七阶段现有台账中 XMMC 是业务项目，通用“项目名称”列可能是登记事项名称。
    aliases={'XMMC':'项目名称','XMDM':'项目代码','XDDW':'建设单位','JSDW':'建设单位','YDWZ':'建设地点'}
    return '；'.join(f'{"原始事项名称" if label=="项目名称" and "XMMC" in labels else aliases.get(label,label)}：{value}'
                    for label,value in zip(labels,row) if value is not None)+'；'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['init','scan','summary','preview','import','propose','review','attach','records','project'])
    parser.add_argument('--template',type=Path);parser.add_argument('--source',type=Path);parser.add_argument('--store',type=Path);parser.add_argument('--catalog',type=Path);parser.add_argument('--limit',type=int);parser.add_argument('--workbook',type=Path)
    parser.add_argument('--payload',type=Path);parser.add_argument('--project');parser.add_argument('--query',default='')
    args=parser.parse_args()
    with closing(Stage2(args.store,args.source)) as service:
        if args.action=='init':
            service.configure(args.template or SOURCE.parent/'test.xlsx');result=dict(projects=service.sync_catalog(args.catalog))
        elif args.action=='scan':result=service.scan(args.limit)
        elif args.action in ('propose','review','attach'):result=getattr(service,args.action)(json.loads(args.payload.read_text(encoding='utf-8')))
        elif args.action=='records':result=service.records(args.query)
        elif args.action=='project':result=service.project(args.project)
        elif args.action=='import':result=dict(rows=service.import_values(args.workbook))
        elif args.action=='preview':result=dict(path=str(service.preview()))
        else:result=service.overview()
        print(dumps(result))


if __name__=='__main__':main()
