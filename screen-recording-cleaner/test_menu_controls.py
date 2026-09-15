"""Opt-in native menu and isolated launchd pause/resume checks. No personal recordings."""
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from cleaner import Cleaner, digest
from install import service_definition
from progress import UI_BINARY, UI_PLIST

BUNDLE = Path(__file__).resolve().parent

# Reuse the actual menu class; replace only its command-line entry point. The
# extension inspects AppKit's rows and dispatches the real menu action by index.
HARNESS = r'''
extension ProgressMenu {
    func rows() -> [String] {
        menu.items.filter { !$0.isHidden && !$0.isSeparatorItem }.map { $0.title }
    }
    func width() -> CGFloat { menu.size.width }
    func pressPause() {
        let index = menu.index(of: pauseRow)
        precondition(index >= 0 && pauseRow.isEnabled)
        menu.performActionForItem(at: index)
    }
}
let arguments = CommandLine.arguments
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
if arguments[1] == "render" {
    let controller = ProgressMenu(output: URL(fileURLWithPath: "/private/tmp"))
    let samples = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: arguments[2]))) as! [[String: Any]]
    var results = [[String: Any]]()
    for sample in samples {
        var data = try JSONSerialization.data(withJSONObject: sample)
        data.append(10)
        controller.read(data)
        results.append(["rows": controller.rows(), "width": controller.width()])
    }
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: results))
    exit(0)
}
let target = arguments[2]
guard target.hasPrefix("gui/\(getuid())/local.screen-recording-cleaner.test.") else { exit(77) }
let output = URL(fileURLWithPath: arguments[1])
let controller = ProgressMenu(output: output, launchdTarget: target)
let input = FileHandle.standardInput
input.readabilityHandler = { handle in
    let data = handle.availableData
    if data.isEmpty {
        handle.readabilityHandler = nil
        DispatchQueue.main.async { controller.finish() }
    } else {
        DispatchQueue.main.async { controller.read(data) }
    }
}
signal(SIGUSR2, SIG_IGN)
let pauseSignal = DispatchSource.makeSignalSource(signal: SIGUSR2, queue: .main)
pauseSignal.setEventHandler { controller.pressPause() }
pauseSignal.resume()
try Data().write(to: output.appendingPathComponent(".menu-test-ready"), options: .atomic)
app.run()
'''


class MenuControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='recording-menu-tests-', dir='/private/tmp')
        root = Path(cls.build.name)
        source = (BUNDLE / 'ProgressMenu.swift').read_text()
        body, separator, _ = source.partition('\nguard (2...3).contains')
        assert separator, 'Native entry point moved; keep the real menu class in this test.'
        swift = root / 'MenuTest.swift'
        swift.write_text(body + HARNESS)
        cls.menu = root / 'MenuTest'
        subprocess.run(['/usr/bin/xcrun', 'swiftc', '-O', '-framework', 'AppKit',
                        '-module-cache-path', str(root / 'modules'), str(swift), '-o', str(cls.menu)],
                       check=True, timeout=90)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def test_actual_rows_show_steps_and_clear_them_when_waiting(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'snapshots.json'
            base = dict(version=1, filename='Menu check.mov', phase_started_at=time.time(), updated_at=time.time())
            path.write_text(json.dumps([
                dict(base, phase='Legacy snapshot'),
                dict(base, phase='Counting original frames', step=1, total_steps=5),
                dict(base, phase='Compressing', step=2, total_steps=5, frame=37, total_frames=100, percent=37),
                dict(base, phase='Waiting to retry', step=None, total_steps=None),
            ]))
            result = subprocess.run([str(self.menu), 'render', str(path)], capture_output=True, text=True,
                                    check=True, timeout=15)
            samples = json.loads(result.stdout)
            self.assertIn('Legacy snapshot', samples[0]['rows'])
            self.assertIn('Step 1 of 5 · Counting original frames', samples[1]['rows'])
            self.assertIn('Step 2 of 5 · Compressing · 37%', samples[2]['rows'])
            self.assertIn('Frames: 37 of 100', samples[2]['rows'])
            self.assertIn('Waiting to retry', samples[3]['rows'])
            self.assertFalse(any('Step ' in row or 'Frames:' in row or '%' in row for row in samples[3]['rows']))
            self.assertTrue(all(100 < sample['width'] < 650 for sample in samples))
            self.assertFalse(any('Pause processing' in sample['rows'] for sample in samples),
                             'Manual previews must not control the installed job')

    def test_menu_pause_stops_children_and_resume_collects_recordings(self):
        with tempfile.TemporaryDirectory(prefix='recording-pause-', dir='/private/tmp') as temp:
            root = Path(temp)
            incoming = root / 'incoming'
            incoming.mkdir()
            support = root / 'support'
            support.mkdir()
            output = root / 'finished'
            fixture = root / 'fixture.mov'
            ffmpeg = shutil.which('ffmpeg')
            subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=640x360:rate=30:duration=3', '-c:v', 'libx264', '-crf', '0',
                            '-threads', '2', str(fixture)], check=True)
            paced = root / 'paced-ffmpeg'
            paced.write_text('#!' + sys.executable + '\nimport os,sys\na=sys.argv[1:]\ni=a.index("-i")\n'
                             'os.execv(' + repr(ffmpeg) + ',[' + repr(ffmpeg) + ']+a[:i]+["-re"]+a[i:])\n')
            paced.chmod(0o700)
            for name in ('cleaner.py', 'progress.py'):
                shutil.copy2(BUNDLE / name, support / name)
            (support / UI_BINARY).parent.mkdir(parents=True)
            # Run the real menu class inside the job, with only a test signal to
            # select its Pause item. This exercises its own process group stopping.
            shutil.copy2(self.menu, support / UI_BINARY)
            shutil.copy2(BUNDLE / 'ProgressInfo.plist', support / UI_PLIST)
            config = dict(source=str(incoming), output=str(output), state_dir=str(support / 'state'),
                          python=sys.executable, ffmpeg=str(paced), ffprobe=shutil.which('ffprobe'),
                          threads=2, crf=18, max_bytes=1, settle_seconds=1, notifications=False)
            config_path = support / 'config.json'
            config_path.write_text(json.dumps(config))
            archive = incoming / 'Archive.mov'
            shutil.copyfile(fixture, archive)
            cleaner = Cleaner(config)
            cleaner.initialize()
            cleaner.lock.close()
            for handler in cleaner.log.handlers[:]:
                handler.close()
                cleaner.log.removeHandler(handler)
            # Reuse one test-only label so repeated tests do not add disabled-state entries.
            label = 'local.screen-recording-cleaner.test.pause-controls'
            target = f'gui/{os.getuid()}/{label}'
            home = root / 'home'
            agent = home / 'Library/LaunchAgents/local.screen-recording-cleaner.plist'
            agent.parent.mkdir(parents=True)
            definition = service_definition(support, config_path)
            definition.update(Label=label, ThrottleInterval=1)
            args = definition['ProgramArguments']
            args[args.index('--launchd-target') + 1] = target
            agent.write_bytes(plistlib.dumps(definition))

            def ctl(*args, check=True):
                return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, check=check)

            def ledger():
                return json.loads((support / 'state/state.json').read_text())['files']

            def group():
                status = support / 'state/runner.json'
                if not status.exists():
                    return []
                pid = str(json.loads(status.read_text())['pid'])
                rows = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,comm='], text=True)
                return [row.split(None, 2) for row in rows.splitlines() if row.split()[1] == pid]

            def wait(predicate):
                deadline = time.monotonic() + 45
                while not predicate():
                    if time.monotonic() >= deadline:
                        self.fail((support / 'launchd.log').read_text())
                    time.sleep(.05)

            registered_here = False
            try:
                ctl('bootstrap', f'gui/{os.getuid()}', str(agent))
                registered_here = True
                first = incoming / 'First.mov'
                shutil.copyfile(fixture, first)
                wait(lambda: (output / '.menu-test-ready').exists()
                     and any(Path(row[2]).name == 'ffmpeg' for row in group()))
                owned = {row[0] for row in group()}
                menu_pid = next(row[0] for row in group() if Path(row[2]).name == 'RecordingProgress')
                os.kill(int(menu_pid), signal.SIGUSR2)
                wait(lambda: ctl('print', target, check=False).returncode != 0 and not group())
                live = subprocess.check_output(['/bin/ps', '-axo', 'pid='], text=True).split()
                self.assertFalse(owned.intersection(live))
                self.assertRegex(ctl('print-disabled', f'gui/{os.getuid()}').stdout,
                                 re.escape(f'"{label}"') + r'\s*=>\s*(?:true|disabled)\b')
                self.assertNotEqual(ctl('bootstrap', f'gui/{os.getuid()}', str(agent), check=False).returncode, 0)
                second = incoming / 'While paused.mov'
                shutil.copyfile(fixture, second)
                self.assertNotIn(str(second), ledger())
                self.assertFalse(list(output.glob('* - clean.*')))
                subprocess.run(['/bin/bash', str(BUNDLE / 'Resume.command'), str(agent)],
                               check=True, capture_output=True, text=True)
                wait(lambda: all(ledger().get(str(path), {}).get('status') == 'done' for path in (first, second)) and not group())
                self.assertEqual(ledger()[str(archive)]['status'], 'existing')
                for path in (first, second, archive):
                    self.assertEqual(digest(path), digest(fixture))
                self.assertEqual(len(list(output.glob('* - clean.*'))), 2)
                self.assertFalse(list(output.glob('.processing-*')))
            finally:
                if registered_here:
                    ctl('bootout', target, check=False)
                    ctl('enable', target, check=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
