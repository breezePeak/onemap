"""全量解压到项目目录，SQLite 逐文件记录成功/失败与源版本。"""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import zipfile

BASE=Path(__file__).resolve().parent.parent
SOURCE=Path(r'F:\项目数据\赣江一张图')
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
    if not target.is_relative_to(folder.resolve()) or ':' in name or name.startswith('/'):
        raise ValueError('ZIP 成员路径越界或包含非法路径')
    return target


def original_member(archive,member):
    archive=Path(archive)
    name=member['name']
    if ':' in name or name.startswith('/') or '..' in Path(name).parts:return None
    for folder in (archive.parent,archive.with_suffix('')):
        path=folder/name
        if disk_path(path).is_file() and disk_path(path).stat().st_size==member['size']:
            return path
    return None


class ArchiveRecords:
    def __init__(self,output=OUTPUT):
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.output/'解压记录.sqlite')
        self.registered=0
        self.db.execute('CREATE TABLE IF NOT EXISTS archives (path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,members TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS files (archive TEXT,raw TEXT,crc INTEGER,size INTEGER,dest TEXT,dsize INTEGER,dmtime INTEGER,status TEXT,error TEXT,PRIMARY KEY(archive,raw))')

    def members(self,archive):
        archive=Path(archive);stat=disk_path(archive).stat();key=str(archive.resolve())
        row=self.db.execute('SELECT size,mtime,members FROM archives WHERE path=?',(key,)).fetchone()
        if row and row[:2]==(stat.st_size,stat.st_mtime_ns):return json.loads(row[2])
        with zipfile.ZipFile(disk_path(archive)) as z:
            members=[dict(raw=i.filename,name=decoded_name(i),size=i.file_size,crc=i.CRC,flags=i.flag_bits,directory=i.is_dir()) for i in z.infolist()]
        self.db.execute('INSERT OR REPLACE INTO archives VALUES (?,?,?,?)',(key,stat.st_size,stat.st_mtime_ns,json.dumps(members,ensure_ascii=False)))
        self.db.commit();return members

    def existing(self,archive,member):
        row=self.db.execute('SELECT crc,size,dest,dsize,dmtime,status FROM files WHERE archive=? AND raw=?',(str(Path(archive).absolute()),member['raw'])).fetchone()
        if row and row[0:2]==(member['crc'],member['size']) and row[5]=='done':
            path=Path(row[2])
            if disk_path(path).is_file():
                stat=disk_path(path).stat()
                if (stat.st_size,stat.st_mtime_ns)==row[3:5]:return path
        original=original_member(archive,member)
        if original:
            stat=disk_path(original).stat()
            # shortcut: 复用原目录中同路径同大小文件，源文件版本依赖大小和修改时间；内容争议时再校验 CRC。
            self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)',(str(Path(archive).absolute()),member['raw'],member['crc'],member['size'],str(original),stat.st_size,stat.st_mtime_ns,'done',None))
            self.registered+=1
            if self.registered%100==0:self.db.commit()
            return original
        return None

    def extract(self,archive,folder):
        archive=Path(archive);counts=dict(extracted=0,reused=0,failed=0);nested=[];stream=None
        try:
            for member in self.members(archive):
                if member['directory']:continue
                target=None
                try:
                    target=safe_target(folder,member['name'])
                    existing=self.existing(archive,member)
                    if existing:
                        counts['reused']+=1
                        target=existing
                    else:
                        if stream is None:stream=zipfile.ZipFile(disk_path(archive))
                        disk_path(target.parent).mkdir(parents=True,exist_ok=True)
                        temp=disk_path(target.with_name(target.name+'.extracting'))
                        try:
                            with stream.open(member['raw']) as source,temp.open('wb') as out:shutil.copyfileobj(source,out)
                            temp.replace(disk_path(target))
                        finally:temp.unlink(missing_ok=True)
                        stat=disk_path(target).stat()
                        self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)',(str(archive.resolve()),member['raw'],member['crc'],member['size'],str(target),stat.st_size,stat.st_mtime_ns,'done',None))
                        self.db.commit();counts['extracted']+=1
                    if target.suffix.lower()=='.zip':nested.append(target)
                except (OSError,ValueError,RuntimeError,zipfile.BadZipFile,NotImplementedError) as error:
                    counts['failed']+=1
                    self.db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?)',(str(archive.resolve()),member['raw'],member['crc'],member['size'],str(target) if target else '',0,0,'failed',str(error)))
                    self.db.commit()
        finally:
            if stream:stream.close()
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
    records=ArchiveRecords();queue=[(p,OUTPUT/p.relative_to(SOURCE).parent/(p.name+'.解压')) for p in SOURCE.rglob('*') if p.is_file() and p.suffix.lower()=='.zip']
    report=[];seen=set()
    try:
        while queue:
            archive,folder=queue.pop(0)
            if archive.resolve() in seen:continue
            seen.add(archive.resolve())
            try:
                counts,nested=records.extract(archive,folder)
                report.append(dict(archive=str(archive),output=str(folder),**counts))
                queue.extend((p,OUTPUT/p.relative_to(SOURCE).parent/(p.name+'.解压') if p.is_relative_to(SOURCE) else p.with_name(p.name+'.解压')) for p in nested)
                print(f'{len(report)} ZIP: {archive.name}: {counts}',flush=True)
            except (OSError,ValueError,RuntimeError,zipfile.BadZipFile,NotImplementedError) as error:
                report.append(dict(archive=str(archive),error=str(error)));print('ZIP failed:',archive,str(error),flush=True)
            temp=OUTPUT/'解压汇总.json.tmp';temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(OUTPUT/'解压汇总.json')
    finally:records.db.close()
    failed=sum(r.get('failed',0)+bool(r.get('error')) for r in report)
    print(json.dumps(dict(archives=len(report),extracted=sum(r.get('extracted',0) for r in report),reused=sum(r.get('reused',0) for r in report),failed=failed),ensure_ascii=False),flush=True)
    if failed:raise SystemExit(1)


if __name__=='__main__':main()
