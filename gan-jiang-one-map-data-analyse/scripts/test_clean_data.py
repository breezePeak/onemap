"""Run the cleaner only against disposable project copies, never real collected data."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

BASE = Path(__file__).resolve().parent.parent


class CleanDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=BASE / 'work')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project = self.base / 'project with spaces'
        self.project.mkdir()
        shutil.copy2(BASE / 'clean-data.ps1', self.project)
        self.put('app/server.py', '# marker')
        self.put('scripts/source_config.py', '# marker')
        self.put('stop-tool.ps1', "'stopped' | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'stopped.txt'); exit 0")
        self.put('work/folder_mount/project_catalog.sqlite')
        self.put('work/jobs/123/upload.xlsx')
        self.put('work/pdf_pages/a.png')
        self.put('work/python_packages/keep.txt')
        self.put('work/cad_runtime/keep.exe')
        self.put('outputs/export.xlsx')
        self.put('解压材料/解压记录.sqlite')
        self.put('cache_reuse.json')
        self.put('reviewed_fields.json', '[{"old":"review"}]')
        self.put('test_result.xlsx', 'original input')
        self.outside = self.base / 'source'
        self.outside.mkdir()
        (self.outside / 'original.pdf').write_text('source material')

    def put(self, relative, text='generated'):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def run_clean(self, *args):
        return subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                               str(self.project / 'clean-data.ps1'), *args], capture_output=True)

    def junction(self, link, target):
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                                 f'New-Item -ItemType Junction -Path {quote(link)} -Target {quote(target)}'], capture_output=True)
        if result.returncode:
            self.skipTest('Windows policy does not permit creating directory junctions')

    def test_cleanup_preserves_source_dependencies_and_is_repeatable(self):
        for _ in range(2):
            result = self.run_clean()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({p.name for p in (self.project / 'work').iterdir()}, {'python_packages', 'cad_runtime'})
        for folder in ('outputs', '解压材料'):
            self.assertEqual(list((self.project / folder).iterdir()), [])
        self.assertFalse((self.project / 'cache_reuse.json').exists())
        self.assertEqual((self.project / 'reviewed_fields.json').read_text().strip(), '[{"old":"review"}]')
        self.assertEqual((self.project / 'test_result.xlsx').read_text(), 'original input')
        self.assertEqual((self.outside / 'original.pdf').read_text(), 'source material')
        self.assertTrue((self.project / 'work/python_packages/keep.txt').is_file())
        self.assertTrue((self.project / 'work/cad_runtime/keep.exe').is_file())

    def test_nested_junction_does_not_delete_source(self):
        self.junction(self.project / 'work/source-link', self.outside)
        result = self.run_clean()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.project / 'work/source-link').exists())
        self.assertEqual((self.outside / 'original.pdf').read_text(), 'source material')

    def test_long_office_profile_path_is_deleted(self):
        relative = ('work/jobs/7053ac1161a7407f/work/office_pdf/13eecfb0122f7a89a76a/'
                    'profile/user/extensions/bundled/registry/'
                    'com.sun.star.comp.deployment.configuration.PackageRegistryBackend/'
                    'lu20404arfefs.tmp/中文临时目录/OptionsDialog.xcu')
        target = self.project / relative
        disk_target = Path('\\\\?\\' + str(target))
        self.addCleanup(shutil.rmtree, '\\\\?\\' + str(self.base))
        disk_target.parent.mkdir(parents=True)
        disk_target.write_text('office profile', encoding='utf-8')
        self.assertGreater(len(str(target)), 260)
        self.assertTrue(disk_target.is_file())
        result = self.run_clean()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.project / 'work/jobs').exists())
        self.assertTrue((self.outside / 'original.pdf').is_file())

    def test_preview_does_not_stop_or_delete(self):
        result = self.run_clean('-Preview')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.project / 'work/folder_mount/project_catalog.sqlite').is_file())
        self.assertFalse((self.project / 'stopped.txt').exists())
        self.assertIn('old', (self.project / 'reviewed_fields.json').read_text())

    def test_linked_data_root_is_rejected_before_deletion(self):
        shutil.rmtree(self.project / 'outputs')
        self.junction(self.project / 'outputs', self.outside)
        result = self.run_clean()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.project / 'work/folder_mount/project_catalog.sqlite').is_file())
        self.assertFalse((self.project / 'stopped.txt').exists())
        self.assertTrue((self.outside / 'original.pdf').is_file())

    def test_stop_failure_prevents_deletion(self):
        self.put('stop-tool.ps1', 'exit 1')
        self.assertNotEqual(self.run_clean().returncode, 0)
        self.assertTrue((self.project / 'work/folder_mount/project_catalog.sqlite').is_file())


if __name__ == '__main__':
    unittest.main()
