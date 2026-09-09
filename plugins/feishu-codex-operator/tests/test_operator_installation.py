"""Ownership and removal only in disposable projects; no real shortcuts or tasks."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which('pwsh')


@unittest.skipUnless(os.name == 'nt' and PWSH, 'Windows PowerShell required')
class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='operator-installation-')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.target = self.project/'AGENTS.md'

    @staticmethod
    def q(value): return "'"+str(value).replace("'","''")+"'"

    def ps(self, code):
        script = self.project/'test-driver.ps1'
        script.write_text("$ErrorActionPreference='Stop'\nImport-Module "+self.q(ROOT/'scripts/operator_installation.psm1')+
            " -Force -DisableNameChecking\n$p="+self.q(self.project)+"\n$f=Join-Path $p 'AGENTS.md'\n"+code,
            encoding='utf-8')
        return subprocess.run([PWSH,'-NoProfile','-File',str(script)],capture_output=True,text=True,
                              encoding='utf-8',timeout=30)

    def write(self, text):
        return self.ps("Set-OperatorManagedFile -ProjectRoot $p -Path $f -Bytes ([Text.Encoding]::UTF8.GetBytes("+self.q(text)+"))")

    def test_missing_journal_after_rule_only_install_blocks_recovery_and_adoption(self):
        self.assertEqual(self.write('managed').returncode, 0)
        (self.project/'.codex/operator-installation/ownership.json').unlink()
        for code in ('Get-OperatorRestorePlan $p', 'Restore-OperatorManagedFiles $p',
                     'Start-OperatorInstallation $p'):
            result = self.ps(code)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Ownership journal missing', result.stderr)
        self.assertNotEqual(self.write('new').returncode, 0)
        self.assertEqual(self.target.read_text(), 'managed')

    def test_missing_journal_blocks_full_uninstall_before_mutation(self):
        env = os.environ.copy()
        env['LOCALAPPDATA'] = str(self.project/'local-appdata')
        env['CODEX_HOME'] = str(self.project/'user-config')
        installed = subprocess.run([PWSH, '-NoProfile', '-File',
            str(ROOT/'scripts/install-feishu-codex-operator.ps1'), '-ProjectRoot', str(self.project),
            '-SkipDesktopEntry'], env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(installed.returncode, 0, installed.stdout+installed.stderr)
        (self.project/'.codex/operator-installation/ownership.json').unlink()
        before = {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        for flags in ([], ['-Apply']):
            result = subprocess.run([PWSH, '-NoProfile', '-File',
                str(ROOT/'scripts/uninstall-feishu-codex-operator.ps1'), '-ProjectRoot', str(self.project),
                '-RouterPort', str(port), *flags], env=env, capture_output=True, text=True,
                encoding='utf-8', timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Ownership journal missing', result.stderr)
            self.assertEqual(before, {p.relative_to(self.project): p.read_bytes()
                                     for p in self.project.rglob('*') if p.is_file()})

    def test_reinstall_requires_completed_uninstall_and_unchanged_originals(self):
        self.target.write_bytes(b'original')
        self.assertEqual(self.write('managed').returncode, 0)
        self.assertEqual(self.ps('Restore-OperatorManagedFiles $p | Out-Null').returncode, 0)
        journal = self.project/'.codex/operator-installation/ownership.json'
        before = journal.read_bytes()
        self.assertNotEqual(self.ps('Start-OperatorInstallation $p').returncode, 0)
        receipt = self.project/'.codex/operator-installation/uninstall-receipt.json'
        receipt.write_text(json.dumps({'schema_version': 1, 'project': str(self.project), 'uninstalled': True}))
        self.target.write_bytes(b'new user edit')
        self.assertNotEqual(self.ps('Start-OperatorInstallation $p').returncode, 0)
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(self.target.read_bytes(), b'new user edit')

    def test_init_import_preserves_explicit_startup_bundle(self):
        source = (ROOT/'scripts/feishu-codex-operator.ps1').read_text(encoding='utf-8')
        # Execute the actual initialization import, with its path resolved to source.
        statement = next(line.strip() for line in source.splitlines()
                         if "'operator_desktop_setup.ps1'" in line and '-Library' in line)
        statement = statement.replace("(Join-Path $PSScriptRoot 'operator_desktop_setup.ps1')",
                                      self.q(ROOT/'scripts/operator_desktop_setup.ps1'))
        result = self.ps("function Resolve-Project { $p }\n$StartupBundle=Join-Path $p '.codex/selected-workflow'\n"
                         "$expected=$StartupBundle\n"+statement+
                         "\nif($StartupBundle -cne $expected){throw 'Selected startup bundle was lost'}")
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_upgrade_restores_first_original_exactly(self):
        original = b'\xef\xbb\xbfUser rules\r\n\r\n'
        self.target.write_bytes(original)
        for version in ('v1','v2','v3'):
            result=self.write(version); self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        result=self.ps('Restore-OperatorManagedFiles $p | ConvertTo-Json -Depth 6')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.target.read_bytes(),original)
        result=self.ps('Restore-OperatorManagedFiles $p | Out-Null')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_created_file_removed_and_preview_has_no_writes(self):
        self.assertEqual(self.write('managed').returncode,0)
        journal=self.project/'.codex/operator-installation/ownership.json'; before=journal.read_bytes()
        result=self.ps('Get-OperatorRestorePlan $p | ConvertTo-Json -Depth 6')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(journal.read_bytes(),before)
        self.assertEqual(self.target.read_text(),'managed')
        self.assertEqual(self.ps('Restore-OperatorManagedFiles $p | Out-Null').returncode,0)
        self.assertFalse(self.target.exists())

    def test_user_edit_blocks_entire_restore(self):
        self.target.write_bytes(b'original'); self.assertEqual(self.write('managed').returncode,0)
        second=self.project/'.codex/hooks.json'
        result=self.ps("Set-OperatorManagedFile $p (Join-Path $p '.codex/hooks.json') ([Text.Encoding]::UTF8.GetBytes('{}'))")
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.target.write_bytes(b'user later edit')
        result=self.ps('Restore-OperatorManagedFiles $p | Out-Null')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(self.target.read_bytes(),b'user later edit'); self.assertTrue(second.exists())
        self.assertNotEqual(self.write('new upgrade').returncode,0)

    def test_corrupt_original_and_unowned_journal_path_are_rejected(self):
        self.target.write_bytes(b'original'); self.assertEqual(self.write('managed').returncode,0)
        original=next((self.project/'.codex/operator-installation/originals').glob('*.bin'))
        original.write_bytes(b'tampered')
        self.assertNotEqual(self.ps('Restore-OperatorManagedFiles $p | Out-Null').returncode,0)
        journal=self.project/'.codex/operator-installation/ownership.json'
        data=json.loads(journal.read_text()); data['entries'][str(self.project/'unrelated.txt')]=data['entries'].pop(str(self.target))
        journal.write_text(json.dumps(data),encoding='utf-8')
        self.assertNotEqual(self.ps('Get-OperatorRestorePlan $p | Out-Null').returncode,0)
        self.assertEqual(self.target.read_bytes(),b'managed')

    def test_pending_write_after_crash_can_restore_without_replaying_install(self):
        self.target.write_bytes(b'original'); self.assertEqual(self.write('first').returncode,0)
        journal=self.project/'.codex/operator-installation/ownership.json'; data=json.loads(journal.read_text())
        row=data['entries'][str(self.target)]; row.update(previous=row['after'],after='a'*64,status='pending')
        journal.write_text(json.dumps(data),encoding='utf-8')
        result=self.ps('Restore-OperatorManagedFiles $p | Out-Null')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.target.read_bytes(),b'original')

    def test_fresh_install_and_safe_uninstall_preserve_unrelated_state(self):
        self.target.write_bytes(b'User rules\r\n')
        hooks=self.project/'.codex/hooks.json'; hooks.parent.mkdir()
        existing=json.dumps({'hooks':{'SessionStart':[
            {'hooks':[{'type':'command','command':'echo user fixture'}]},
            {'hooks':[{'type':'command','command':r'powershell.exe -File "C:\other-project\start-feishu-codex-operator.ps1"'}]}
        ]}}).encode()
        hooks.write_bytes(existing)
        env=os.environ.copy(); env['LOCALAPPDATA']=str(self.project/'local-appdata'); env['CODEX_HOME']=str(self.project/'user-config')
        (self.project/'user-config').mkdir(); (self.project/'user-config/config.toml').write_text('model="native-fixture"\n',encoding='utf-8')
        commands=[['merge-agents-rules.ps1','-ProjectRoot',str(self.project)],
                  ['install-feishu-codex-operator.ps1','-ProjectRoot',str(self.project),'-SkipDesktopEntry']]
        for command in commands:
            result=subprocess.run([PWSH,'-NoProfile','-File',str(ROOT/'scripts'/command[0]),*command[1:]],
                                  env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        runtime=self.project/'.codex/feishu-codex-operator-runtime'
        self.assertEqual(len(json.loads(hooks.read_text())['hooks']['SessionStart']),3,
                         'Unrelated similarly named hooks must remain during install')
        (runtime/'sessions.json').write_bytes(b'private fixture retained')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
        command=[PWSH,'-NoProfile','-File',str(ROOT/'scripts/uninstall-feishu-codex-operator.ps1'),
                 '-ProjectRoot',str(self.project),'-RouterPort',str(port)]
        preview=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(preview.returncode,0,preview.stdout+preview.stderr)
        self.assertTrue(json.loads(preview.stdout)['ready']); self.assertTrue(runtime.exists())
        removed=subprocess.run([*command,'-Apply'],env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(removed.returncode,0,removed.stdout+removed.stderr)
        self.assertEqual(self.target.read_bytes(),b'User rules\r\n'); self.assertEqual(hooks.read_bytes(),existing)
        self.assertFalse(runtime.exists())
        archives=list((self.project/'.codex/operator-uninstalled').iterdir()); self.assertEqual(len(archives),1)
        self.assertEqual((archives[0]/'sessions.json').read_bytes(),b'private fixture retained')
        self.assertEqual((self.project/'user-config/config.toml').read_text(),'model="native-fixture"\n')
        old_journal = (self.project/'.codex/operator-installation/ownership.json').read_bytes()
        # Start with the supported installer, then initialize project rules again.
        for install in reversed(commands):
            result = subprocess.run([PWSH, '-NoProfile', '-File', str(ROOT/'scripts'/install[0]), *install[1:]],
                                    env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        history = list((self.project/'.codex/operator-installation/history').glob('*/ownership.json'))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].read_bytes(), old_journal)
        removed = subprocess.run([*command, '-Apply'], env=env, capture_output=True, text=True,
                                 encoding='utf-8', timeout=30)
        self.assertEqual(removed.returncode, 0, removed.stdout+removed.stderr)
        self.assertEqual(self.target.read_bytes(), b'User rules\r\n')
        self.assertEqual(hooks.read_bytes(), existing)
        self.assertEqual((archives[0]/'sessions.json').read_bytes(), b'private fixture retained')

    def test_shortcut_setup_restores_original_and_never_uses_real_shell_folders(self):
        code = """
. SETUP -ProjectRoot $p -Library
$fixtureDesktop=Join-Path $p 'fake-desktop'
$fixturePrograms=Join-Path $p 'fake-programs'
$fixtureApp=Join-Path $p 'fake-app'
New-Item -ItemType Directory -Force -Path $fixtureDesktop,$fixturePrograms,(Join-Path $fixtureApp 'app') | Out-Null
$fixtureSource=Join-Path $p 'fixture.cs'
[IO.File]::WriteAllText($fixtureSource,'class Fixture { static void Main() {} }')
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
& $compiler /nologo /target:winexe (('/out:')+(Join-Path $fixtureApp 'app/ChatGPT.exe')) $fixtureSource
if($LASTEXITCODE -ne 0){throw 'Fixture build failed'}
function Get-AppxPackage { param($Name) [pscustomobject]@{InstallLocation=$fixtureApp} }
function Get-OperatorDesktopPaths {
    @((Join-Path $fixtureDesktop 'Codex.lnk'),(Join-Path $fixturePrograms 'Codex.lnk'),(Join-Path $fixtureDesktop 'Codex（同步本地模型）.lnk'))
}
function Set-OperatorManagedFile {
    param($ProjectRoot,$Path,[byte[]]$Bytes,$LinkPaths)
    if(-not ([IO.Path]::GetFullPath($Path)).StartsWith(($p+'\\'),[StringComparison]::OrdinalIgnoreCase)){throw 'Real shell target forbidden'}
    operator_installation\\Set-OperatorManagedFile -ProjectRoot $ProjectRoot -Path $Path -Bytes $Bytes -LinkPaths $LinkPaths
}
$wsh=New-Object -ComObject WScript.Shell
$originalPath=Join-Path $fixtureDesktop 'Codex.lnk'
$original=$wsh.CreateShortcut($originalPath); $original.TargetPath=Join-Path $fixtureApp 'app/ChatGPT.exe'; $original.Save()
$originalHash=Get-OperatorFingerprint $originalPath
Install-OperatorDesktopEntry -ProjectRoot $p | Out-Null
Install-OperatorDesktopEntry -ProjectRoot $p | Out-Null
if($wsh.CreateShortcut($originalPath).TargetPath -ine (Join-Path $p '.codex/operator-desktop-entry/Codex.exe')){throw 'Wrong launcher target'}
Restore-OperatorManagedFiles -ProjectRoot $p -LinkPaths @(Get-OperatorDesktopPaths) | Out-Null
if((Get-OperatorFingerprint $originalPath) -cne $originalHash){throw 'Original shortcut was not restored exactly'}
if(Test-Path -LiteralPath (Join-Path $fixturePrograms 'Codex.lnk')){throw 'Created shortcut remained'}
$receipt=@{schema_version=1;project=$p;uninstalled=$true} | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $p '.codex/operator-installation/uninstall-receipt.json'),$receipt)
$marker=Join-Path $p '.codex/operator-desktop-entry/native-only'
[IO.File]::WriteAllText($marker,'native-only')
# Reinstall may revive a retained launcher only after completed recovery.
Install-OperatorDesktopEntry -ProjectRoot $p | Out-Null
if(Test-Path -LiteralPath $marker){throw 'Reinstalled launcher stayed native-only'}
Restore-OperatorManagedFiles -ProjectRoot $p -LinkPaths @(Get-OperatorDesktopPaths) | Out-Null
if((Get-OperatorFingerprint $originalPath) -cne $originalHash){throw 'Second uninstall lost the original shortcut'}
""".replace('SETUP',self.q(ROOT/'scripts/operator_desktop_setup.ps1'))
        result=self.ps(code)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__': unittest.main()
