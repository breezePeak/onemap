import tempfile
import shutil
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from extract_archives import ArchiveRecords,RecordedZip,safe_target,disk_path,BASE


class ArchiveTests(unittest.TestCase):
    def test_existing_original_file_is_reused_without_decompression(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);archive=root/'source.zip';original=root/'already/a.txt'
            original.parent.mkdir();original.write_bytes(b'existing')
            with zipfile.ZipFile(archive,'w') as z:z.writestr('already/a.txt',b'existing')
            records=ArchiveRecords(root/'out')
            try:
                records.members(archive)
                with patch('extract_archives.zipfile.ZipFile',side_effect=AssertionError('ZIP reopened')):
                    counts,_=records.extract(archive,root/'out/files')
                self.assertEqual(counts,dict(extracted=0,reused=1,failed=0))
                self.assertEqual(records.existing(archive,records.members(archive)[0]),original)
                self.assertFalse((root/'out/files/already/a.txt').exists())
            finally:records.db.close()

    def test_long_paths_nested_zip_and_unsafe_member(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);archive=root/'source.zip';records=ArchiveRecords(root/'out')
            try:
                name='/'.join(['long-directory-name']*14)+'/page.txt'
                with zipfile.ZipFile(archive,'w') as z:
                    z.writestr(name,'long path')
                    z.writestr('../escape.txt','blocked')
                    z.writestr('nested.zip',b'placeholder')
                counts,nested=records.extract(archive,root/'files')
                self.assertEqual(counts,dict(extracted=2,reused=0,failed=1))
                self.assertEqual(nested,[root/'files/nested.zip'])
                self.assertFalse((root/'escape.txt').exists())
            finally:
                records.db.close()
                target=root/'files'
                assert target.resolve().is_relative_to((BASE/'work').resolve())
                shutil.rmtree(disk_path(target))

    def test_resume_changed_member_and_missing_output(self):
        with tempfile.TemporaryDirectory(dir=BASE/'work') as folder:
            root=Path(folder);archive=root/'source.zip';out=root/'out';records=ArchiveRecords(out)
            try:
                with zipfile.ZipFile(archive,'w') as z:z.writestr('中文/a.txt','one')
                self.assertEqual(records.extract(archive,out/'files')[0]['extracted'],1)
                with patch('extract_archives.zipfile.ZipFile',side_effect=AssertionError('ZIP reopened')):
                    self.assertEqual(records.extract(archive,out/'files')[0]['reused'],1)
                    with patch('extract_archives.ArchiveRecords',return_value=records):
                        with RecordedZip(archive) as cached:
                            self.assertEqual(cached.infolist()[0].filename,'中文/a.txt')
                            with cached.open('中文/a.txt') as stream:self.assertEqual(stream.read(),b'one')
                    records=ArchiveRecords(out)
                with zipfile.ZipFile(archive,'w') as z:z.writestr('中文/a.txt','new text')
                self.assertEqual(records.extract(archive,out/'files')[0]['extracted'],1)
                self.assertEqual((out/'files/中文/a.txt').read_text(),'new text')
                (out/'files/中文/a.txt').unlink()
                self.assertEqual(records.extract(archive,out/'files')[0]['extracted'],1)
                with self.assertRaises(ValueError):safe_target(out,'../escape.txt')
            finally:records.db.close()


if __name__=='__main__':unittest.main()
