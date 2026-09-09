"""Desktop entry against isolated projects and fake process observations only."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "Windows PowerShell required")
class DesktopEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="operator-desktop-entry-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.scripts = self.root / 'plugins/feishu-codex-operator/scripts'
        self.scripts.mkdir(parents=True)
        self.entry = self.scripts / 'operator_desktop_entry.ps1'
        shutil.copyfile(ROOT / 'scripts/operator_desktop_entry.ps1', self.entry)
        self.bundle = self.root / '.codex/startup-bundle'; self.bundle.mkdir(parents=True)
        self.app = self.root / 'fake-app'; (self.app/'app').mkdir(parents=True)
        (self.app/'app/ChatGPT.exe').write_bytes(b'fixture only')
        self.startup = self.bundle / 'start-codex-with-lmstudio.ps1'
        self.set_startup("Add-Content -LiteralPath " + self.q(self.root/'sync-count') + " -Value 'sync'\nexit 0\n")

    @staticmethod
    def q(path): return "'" + str(path).replace("'", "''") + "'"

    def set_startup(self, code):
        self.startup.write_text(code, encoding='utf-8')
        (self.bundle/'startup-sync-plan.json').write_text(json.dumps({'entry_files':{
            self.startup.name:hashlib.sha256(self.startup.read_bytes()).hexdigest()}}), encoding='utf-8')

    def run_entry(self, *, running=False, check=False):
        process = "[pscustomobject]@{ExecutablePath="+self.q(self.app/'app/ChatGPT.exe')+"}" if running else '@()'
        code = (
            "function Get-AppxPackage { param($Name) [pscustomobject]@{InstallLocation="+self.q(self.app)+"} }\n"
            "function Get-CimInstance { param($ClassName,$Filter) "+process+" }\n"
            "function Start-Process { param($FilePath,$WorkingDirectory,$WindowStyle) "
            "if ($FilePath -ne "+self.q(self.app/'app/ChatGPT.exe')+" -or $WindowStyle -ne 'Normal') { throw 'Unexpected process' }; "
            "Add-Content -LiteralPath "+self.q(self.root/'open-count')+" -Value 'open' }\n"
            "& "+self.q(self.entry)+" -StartupBundle "+self.q(self.bundle)+(' -CheckOnly' if check else '')+"\nexit $LASTEXITCODE\n")
        driver = self.root/'driver.ps1'; driver.write_text(code, encoding='utf-8')
        return subprocess.run([shutil.which('pwsh'),'-NoLogo','-NoProfile','-File',str(driver)],
                              capture_output=True,text=True,encoding='utf-8',timeout=15)

    def test_closed_desktop_runs_reviewed_sync_once(self):
        result = self.run_entry()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'sync-count').read_text(encoding='utf-8-sig').splitlines(),['sync'])
        self.assertFalse((self.root/'open-count').exists(), "The reviewed sync workflow owns the cold launch")

    def test_running_desktop_only_opens_existing_app(self):
        self.startup.write_text("throw 'Must not run'", encoding='utf-8')
        result = self.run_entry(running=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse((self.root/'sync-count').exists())
        self.assertEqual((self.root/'open-count').read_text(encoding='utf-8-sig').splitlines(),['open'])

    def test_check_only_does_not_create_logs_launch_or_sync(self):
        before = sorted(str(p.relative_to(self.bundle)) for p in self.bundle.rglob('*'))
        for running, action in ((False,'synchronize_then_open'),(True,'open_existing')):
            result = self.run_entry(running=running,check=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(json.loads(result.stdout)['action'],action)
        self.assertEqual(sorted(str(p.relative_to(self.bundle)) for p in self.bundle.rglob('*')),before)
        self.assertFalse((self.root/'sync-count').exists()); self.assertFalse((self.root/'open-count').exists())

    def test_installed_entry_without_plugin_source_opens_native(self):
        installed = self.bundle/'operator_desktop_entry.ps1'
        shutil.copyfile(self.entry,installed)
        self.entry = installed
        (self.bundle/'desktop-entry.json').write_text(json.dumps({'schema_version':1,'mode':'native',
            'entry_script_sha256':hashlib.sha256(installed.read_bytes()).hexdigest()}),encoding='utf-8')
        # The source copy can disappear without affecting the installed launcher.
        (self.scripts/'operator_desktop_entry.ps1').unlink()
        result = self.run_entry(check=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout)['action'],'open_native')
        result = self.run_entry()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertFalse((self.root/'sync-count').exists())
        self.assertEqual((self.root/'open-count').read_text(encoding='utf-8-sig').splitlines(),['open'])

    def test_installed_entry_digest_change_stops(self):
        installed = self.bundle/'operator_desktop_entry.ps1'; shutil.copyfile(self.entry,installed); self.entry=installed
        (self.bundle/'desktop-entry.json').write_text(json.dumps({'schema_version':1,'mode':'native',
            'entry_script_sha256':'a'*64}),encoding='utf-8')
        result = self.run_entry()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'open-count').exists()); self.assertFalse((self.root/'sync-count').exists())

    def test_changed_workflow_stops_and_records_fresh_failure(self):
        (self.bundle/'unified-startup-last-log.txt').write_text('obsolete log',encoding='utf-8')
        self.startup.write_text("throw 'Must not run'", encoding='utf-8')
        result = self.run_entry()
        self.assertNotEqual(result.returncode,0)
        log = Path((self.bundle/'unified-startup-last-log.txt').read_text(encoding='utf-8-sig').strip())
        self.assertTrue(log.resolve().is_relative_to(self.bundle.resolve()))
        self.assertIn('workflow changed',log.read_text(encoding='utf-8-sig'))
        self.assertFalse((self.root/'sync-count').exists())

    def test_failed_workflow_is_not_retried(self):
        self.set_startup("Add-Content -LiteralPath " + self.q(self.root/'sync-count') + " -Value 'failed'\nWrite-Output 'fixture failure'\nexit 7\n")
        result = self.run_entry()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.root/'sync-count').read_text(encoding='utf-8-sig').splitlines(),['failed'])
        log = Path((self.bundle/'unified-startup-last-log.txt').read_text(encoding='utf-8-sig').strip())
        self.assertIn('fixture failure',log.read_text(encoding='utf-8-sig'))

    def test_concurrent_launches_coalesce_without_replacing_active_log(self):
        self.set_startup("Add-Content -LiteralPath " + self.q(self.root/'sync-count') +
                         " -Value 'sync'\nStart-Sleep -Seconds 2\nexit 0\n")
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.run_entry)
            deadline = time.monotonic() + 5
            while not (self.root/'sync-count').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue((self.root/'sync-count').exists())
            pointer = (self.bundle/'unified-startup-last-log.txt').read_bytes()
            second = self.run_entry()
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertIn('already in progress',second.stdout)
            self.assertEqual((self.bundle/'unified-startup-last-log.txt').read_bytes(),pointer)
            result = first.result(timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'sync-count').read_text(encoding='utf-8-sig').splitlines(),['sync'])


if __name__ == '__main__': unittest.main()
