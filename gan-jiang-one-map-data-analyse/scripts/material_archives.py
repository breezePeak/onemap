"""统一只读 ZIP/RAR；RAR 使用系统 bsdtar，成员数据不按外部路径落盘。"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile
from extract_archives import RecordedZip


class RarArchive:
    def __init__(self,path):
        self.path=Path(path);self.tar=shutil.which('tar')
        if not self.tar:raise RuntimeError('没有可读取 RAR 的系统 tar，原件保留待处理')

    def __enter__(self):return self
    def __exit__(self,*args):pass

    def infolist(self):
        result=subprocess.run([self.tar,'-tf',str(self.path)],capture_output=True,timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise RuntimeError('RAR 读取失败（可能是伪扩展名、损坏或加密）：'+result.stderr.decode('utf-8',errors='replace')[-1000:])
        try:names=result.stdout.decode('utf-8')
        except UnicodeDecodeError:names=result.stdout.decode('gb18030')
        return [zipfile.ZipInfo(name) for name in names.splitlines() if name and not name.endswith('/')]

    @contextmanager
    def open(self,member):
        name=member.filename if isinstance(member,zipfile.ZipInfo) else member
        with tempfile.TemporaryFile() as spool,tempfile.TemporaryFile() as errors:
            process=subprocess.Popen([self.tar,'-xOf',str(self.path),'--',name],stdout=subprocess.PIPE,stderr=errors,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            def read():
                size=0
                while block:=process.stdout.read(1024*1024):
                    size+=len(block)
                    if size>100*1024*1024:raise ValueError('RAR 单个成员超过100MB，待单独处理')
                    spool.write(block)
            with ThreadPoolExecutor(max_workers=1) as pool:
                try:
                    pool.submit(read).result(timeout=90)
                    if process.wait(timeout=5):
                        errors.seek(0);raise RuntimeError(errors.read().decode('utf-8',errors='replace')[-1000:])
                except BaseException:
                    process.kill();process.wait();raise
                finally:process.stdout.close()
            spool.seek(0);yield spool


def open_archive(path):
    return RarArchive(path) if Path(path).suffix.lower()=='.rar' else RecordedZip(path)
