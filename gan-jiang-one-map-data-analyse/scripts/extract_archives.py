"""原始数据树内增量解压，SQLite 记录成员校验与实际路径。"""
from contextlib import ExitStack, nullcontext
import json
import os
from pathlib import Path
import shutil
import sqlite3
import zipfile
import hashlib
import zlib
import subprocess
import tempfile

BASE=Path(__file__).resolve().parent.parent
from source_config import SOURCE
OUTPUT=BASE/'解压材料'


def disk_path(path):
    path=Path(path)
    if os.name=='nt' and not str(path).startswith('\\\\?\\'):
        return Path('\\\\?\\'+str(path.absolute()))
    return path


def decoded_name(info):
    name=info.filename
    if not info.flag_bits & 0x800:
        try:name=name.encode('cp437').decode('gb18030')
        except UnicodeError:pass
    return name.replace('\\','/')


def safe_target(folder,name):
    target=(folder/name).resolve()
    if not name or not target.is_relative_to(folder.resolve()) or name.startswith('/') or any(ord(c)<32 or c in '<>:"|?*' for c in name) or any(part.rstrip(' .') != part for part in name.replace('\\','/').split('/') if part):
        raise ValueError('ZIP 成员路径越界或包含非法路径')
    return target


def file_version(path):
    stat = disk_path(path).stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def checksums(path):
    sha = hashlib.sha256(); crc = 0
    with disk_path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            sha.update(block); crc = zlib.crc32(block, crc)
    return sha.hexdigest(), crc & 0xffffffff


def original_member(archive, member):
    archive = Path(archive)
    for folder in (archive.with_name(archive.name+'.解压'), archive.with_suffix(''), archive.parent):
        path = safe_target(folder, member['name'])
        if disk_path(path).is_file() and disk_path(path).stat().st_size == member['size']:
            if member.get('crc') is not None and checksums(path)[1] == member['crc']:
                return path
    return None


def seven_zip():
    tool = shutil.which('7z') or shutil.which('7za')
    installed = Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'7-Zip/7z.exe'
    if not tool and installed.is_file(): tool = str(installed)
    if not tool: raise RuntimeError('缺少 7-Zip 工具，RAR/7z 无法校验及解压')
    return tool


class ArchiveRecords:
    def __init__(self, output=OUTPUT, root=None):
        self.output=Path(output); self.output.mkdir(parents=True,exist_ok=True)
        self.root=Path(root).resolve() if root else None
        self.db=sqlite3.connect(self.output/'解压记录.sqlite')
        self.registered=0; self.errors=[]; self.tool_checked=False
        self.db.execute('CREATE TABLE IF NOT EXISTS archives (path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,members TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS files (archive TEXT,raw TEXT,crc INTEGER,size INTEGER,dest TEXT,dsize INTEGER,dmtime INTEGER,status TEXT,error TEXT,PRIMARY KEY(archive,raw))')
        self.db.execute('CREATE TABLE IF NOT EXISTS archive_versions (path TEXT PRIMARY KEY,version TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS owned_files (dest TEXT PRIMARY KEY,archive TEXT,raw TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS verified (archive TEXT,raw TEXT,dest TEXT,version TEXT,sha256 TEXT,PRIMARY KEY(archive,raw,dest))')

    def checked_path(self, path):
        path=Path(path).resolve()
        if self.root and not path.is_relative_to(self.root): raise ValueError('路径超出配置的数据根目录')
        return path

    def members(self, archive):
        archive=self.checked_path(archive); stat=disk_path(archive).stat(); key=str(archive)
        # RAR/7z 的可用工具每次都检查，即使成员清单已有缓存。
        tool=seven_zip() if archive.suffix.lower() in ('.rar','.7z') else None
        if tool and not self.tool_checked:
            check=subprocess.run([tool,'i'],capture_output=True,stdin=subprocess.DEVNULL,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if check.returncode: raise RuntimeError('7-Zip 工具不可用')
            self.tool_checked=True
        saved_version=self.db.execute('SELECT version FROM archive_versions WHERE path=?',(key,)).fetchone()
        current_version=json.dumps(file_version(archive))
        row=self.db.execute('SELECT size,mtime,members FROM archives WHERE path=?',(key,)).fetchone()
        if row and row[:2]==(stat.st_size,stat.st_mtime_ns) and saved_version==(current_version,): return json.loads(row[2])
        if not tool:
            with zipfile.ZipFile(disk_path(archive)) as z:
                members=[dict(raw=i.filename,name=decoded_name(i),size=i.file_size,crc=i.CRC,flags=i.flag_bits,directory=i.is_dir()) for i in z.infolist()]
        else:
            result=subprocess.run([tool,'l','-slt','-ba','-sccUTF-8','-p-',str(archive)],capture_output=True,stdin=subprocess.DEVNULL,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode: raise RuntimeError('压缩包读取失败（损坏、加密或格式不支持）：'+result.stderr.decode('utf-8',errors='replace')[-1500:])
            members=[]
            for block in result.stdout.decode('utf-8-sig').replace('\r','').strip().split('\n\n'):
                values=dict(line.split(' = ',1) for line in block.splitlines() if ' = ' in line)
                if not values: continue
                name=values['Path'].replace('\\','/')
                if 'Size' not in values: raise ValueError('无法读取成员大小：'+name)
                members.append(dict(raw=values['Path'],name=name,size=int(values['Size']),crc=int(values['CRC'],16) if values.get('CRC') else None,flags=1 if values.get('Encrypted')=='+' else 0,directory=values.get('Folder')=='+' or values.get('Attributes','').startswith('D')))
        self.db.execute('INSERT OR REPLACE INTO archives VALUES (?,?,?,?)',(key,stat.st_size,stat.st_mtime_ns,json.dumps(members,ensure_ascii=False)))
        self.db.execute('INSERT OR REPLACE INTO archive_versions VALUES (?,?)',(key,current_version))
        self.db.commit(); return members

    def stream_member(self, archive, member, target, archive_stream=None):
        if Path(archive).suffix.lower()=='.zip':
            with (nullcontext(archive_stream) if archive_stream is not None else zipfile.ZipFile(disk_path(archive))) as z, z.open(member['raw']) as source, disk_path(target).open('wb') as out:
                shutil.copyfileobj(source,out)
        else:
            with disk_path(target).open('wb') as out:
                result=subprocess.run([seven_zip(),'x','-so','-y','-p-',str(archive),'--',member['raw']],stdout=out,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,timeout=600,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode: raise RuntimeError('成员解压/校验失败：'+result.stderr.decode('utf-8',errors='replace')[-1500:])
        if disk_path(target).stat().st_size!=member['size']: raise ValueError('成员大小校验失败')
        sha,crc=checksums(target)
        if member.get('crc') is not None and crc!=member['crc']: raise ValueError('CRC 校验失败')
        return sha

    def existing(self, archive, member):
        archive=self.checked_path(archive)
        if member.get('flags',0)&1: raise ValueError('加密成员，未提供密码')
        row=self.db.execute("SELECT dest FROM files WHERE archive=? AND raw=? AND status='done'",(str(archive),member['raw'])).fetchone()
        paths=[]
        if row: paths.append(Path(row[0]))
        paths.extend(safe_target(folder,member['name']) for folder in (archive.with_name(archive.name+'.解压'),archive.with_suffix(''),archive.parent))
        for path in dict.fromkeys(paths):
            try: path=self.checked_path(path)
            except ValueError: continue
            if not disk_path(path).is_file() or disk_path(path).stat().st_size!=member['size']: continue
            version=json.dumps([file_version(path), member['size'],member.get('crc'), file_version(archive) if member.get('crc') is None else None])
            saved=self.db.execute('SELECT version,sha256 FROM verified WHERE archive=? AND raw=? AND dest=?',(str(archive),member['raw'],str(path))).fetchone()
            if saved and saved[0]==version:
                if row and Path(row[0])==path: return path
                sha=saved[1]
            else:
                before=file_version(path); sha,crc=checksums(path)
                if member.get('crc') is not None:
                    if crc!=member['crc']: continue
                else:
                    with tempfile.TemporaryDirectory(prefix='.onemap-verify-',dir=archive.parent) as temporary:
                        if sha!=self.stream_member(archive,member,Path(temporary)/'verify'): continue
                if file_version(path)!=before: raise OSError('校验时文件发生变化')
            self.record(archive,member,path,sha,version)
            return path
        return None

    def record(self, archive, member, target, sha, version=None):
        stat=disk_path(target).stat()
        if version is None: version=json.dumps([file_version(target),member['size'],member.get('crc'),file_version(archive) if member.get('crc') is None else None])
        self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)',(str(Path(archive).resolve()),member['raw'],member.get('crc'),member['size'],str(target),stat.st_size,stat.st_mtime_ns,'done',None))
        self.db.execute('INSERT OR REPLACE INTO verified VALUES (?,?,?,?,?)',(str(Path(archive).resolve()),member['raw'],str(target),version,sha))
        self.registered+=1
        if self.registered%100==0: self.db.commit()

    def extract(self, archive, folder=None, progress=lambda *_:None):
        archive=self.checked_path(archive)
        folder=self.checked_path(folder or archive.with_name(archive.name+'.解压'))
        if self.root and folder!=archive.with_name(archive.name+'.解压'): raise ValueError('必须在压缩包旁专用目录解压')
        counts=dict(extracted=0,reused=0,failed=0); nested=[]; self.errors=[]
        members=self.members(archive); version=file_version(archive)
        total=sum(not m['directory'] for m in members)
        with ExitStack() as streams:
            archive_stream = None
            for member in members:
                target=None
                try:
                    if member['directory'] and member['name'] in ('/','./','.'):
                        self.db.execute("DELETE FROM files WHERE archive=? AND raw=? AND status='failed'",(str(archive),member['raw']))
                        continue
                    target=self.checked_path(safe_target(folder,member['name']))
                    if member['directory']: continue
                    progress('检查已解压',str(archive),member['name'],dict(counts),total)
                    existing=self.existing(archive,member)
                    if existing:
                        target=existing; counts['reused']+=1
                    else:
                        progress('解压缺失成员',str(archive),member['name'],dict(counts),total)
                        if shutil.disk_usage(archive.parent).free < member['size']+1024*1024: raise OSError('磁盘空间不足')
                        disk_path(target.parent).mkdir(parents=True,exist_ok=True)
                        # 仅替换曾由本索引写入的专用解压文件，避免覆盖用户材料。
                        owned=self.db.execute('SELECT 1 FROM owned_files WHERE archive=? AND raw=? AND dest=?',(str(archive),member['raw'],str(target))).fetchone()
                        if disk_path(target).exists() and not owned: raise ValueError('专用路径已有未验证材料，保留原文件，请人工核对')
                        self.db.execute('INSERT OR REPLACE INTO owned_files VALUES (?,?,?)',(str(target),str(archive),member['raw']))
                        self.db.commit()
                        fd,tempname=tempfile.mkstemp(prefix='.onemap-',dir=disk_path(target.parent)); os.close(fd); temp=Path(tempname)
                        try:
                            if archive.suffix.lower()=='.zip' and archive_stream is None:
                                archive_stream = streams.enter_context(zipfile.ZipFile(disk_path(archive)))
                            sha=self.stream_member(archive,member,temp,archive_stream)
                            if file_version(archive)!=version: raise OSError('解压时压缩包发生变化')
                            progress('校验',str(archive),member['name'],dict(counts),total)
                            temp.replace(disk_path(target)); self.record(archive,member,target,sha); counts['extracted']+=1
                        finally: temp.unlink(missing_ok=True)
                    if target.suffix.lower() in ('.zip','.rar','.7z'): nested.append(target)
                except (OSError,ValueError,RuntimeError,zipfile.BadZipFile,NotImplementedError,subprocess.SubprocessError) as error:
                    counts['failed']+=1; self.errors.append(dict(path=str(archive)+'!'+member['name'],error=str(error)))
                    self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)',(str(archive),member['raw'],member.get('crc'),member['size'],str(target) if target else '',0,0,'failed',str(error)))
                    self.db.commit()
        if file_version(archive)!=version: raise OSError('处理时压缩包发生变化')
        self.db.commit()
        return counts,nested


class RecordedZip:
    """分析脚本复用目录索引和已经解压的文件，缺失时才打开原 ZIP。"""
    def __init__(self,archive):
        self.archive=Path(archive);self.records=ArchiveRecords();self.stream=None
        try:self.items=self.records.members(archive)
        except Exception:
            self.records.db.close();raise
        self.by_raw={m['raw']:m for m in self.items}

    def __enter__(self):return self

    def __exit__(self,*args):
        if self.stream:self.stream.close()
        self.records.db.commit()
        self.records.db.close()

    def infolist(self):
        result=[]
        for m in self.items:
            info=zipfile.ZipInfo(m['raw']);info.file_size=m['size'];info.CRC=m['crc'];info.flag_bits=m.get('flags',0)
            result.append(info)
        return result

    def open(self,member):
        raw=member.filename if isinstance(member,zipfile.ZipInfo) else member
        path=self.records.existing(self.archive,self.by_raw[raw])
        if path:return disk_path(path).open('rb')
        if self.stream is None:self.stream=zipfile.ZipFile(disk_path(self.archive))
        return self.stream.open(raw)


def main():
    from source_config import STAGES
    records=ArchiveRecords(root=SOURCE);queue=[(p,p.with_name(p.name+'.解压')) for _,name in STAGES for p in (SOURCE/name).rglob('*') if p.is_file() and p.suffix.lower() in ('.zip','.rar','.7z')]
    report=[];seen=set()
    try:
        while queue:
            archive,folder=queue.pop(0)
            if archive.resolve() in seen:continue
            seen.add(archive.resolve())
            try:
                counts,nested=records.extract(archive,folder)
                report.append(dict(archive=str(archive),output=str(folder),**counts))
                queue.extend((p,p.with_name(p.name+'.解压')) for p in nested)
                print(f'{len(report)} 压缩包: {archive.name}: {counts}',flush=True)
            except (OSError,ValueError,RuntimeError,zipfile.BadZipFile,NotImplementedError,subprocess.SubprocessError) as error:
                report.append(dict(archive=str(archive),error=str(error)));print('压缩包失败:',archive,str(error),flush=True)
            temp=OUTPUT/'解压汇总.json.tmp';temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(OUTPUT/'解压汇总.json')
    finally:records.db.close()
    failed=sum(r.get('failed',0)+bool(r.get('error')) for r in report)
    print(json.dumps(dict(archives=len(report),extracted=sum(r.get('extracted',0) for r in report),reused=sum(r.get('reused',0) for r in report),failed=failed),ensure_ascii=False),flush=True)
    if failed:raise SystemExit(1)


if __name__=='__main__':main()
