"""Opt-in native menu and isolated launchd pause/resume checks. No personal recordings."""
import fcntl
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
from install import paused_agent_path, paused_service_definition, service_definition
from progress import UI_BINARY, UI_PLIST

BUNDLE = Path(__file__).resolve().parent

# Reuse the actual menu class and production entry points. Test hooks inspect
# AppKit rows and select the real menu action by index.
HARNESS = r'''
extension ProgressMenu {
    func rows() -> [String] {
        menu.items.filter { !$0.isHidden && !$0.isSeparatorItem }.map { $0.title }
    }
    func inspection() -> [String: Any] {
        ["rows": rows(), "width": menu.size.width, "title": item.button?.title ?? "",
         "has_timer": menuClock != nil || staleCheck != nil]
    }
    func pressControl() {
        let index = menu.index(of: pauseRow)
        precondition(index >= 0 && pauseRow.isEnabled)
        menu.performActionForItem(at: index)
    }
    func writeReady() throws {
        let name = resumeAgent == nil ? ".menu-test-ready" : ".paused-menu-test-ready"
        var snapshot = inspection()
        snapshot["pid"] = getpid()
        try JSONSerialization.data(withJSONObject: snapshot).write(to: output.appendingPathComponent(name), options: .atomic)
    }
}
let arguments = CommandLine.arguments
if arguments[1] == "render" || arguments[1] == "render-paused" {
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    let paused = arguments[1] == "render-paused"
    let controller = ProgressMenu(output: URL(fileURLWithPath: "/private/tmp"),
        launchdTarget: paused ? "gui/\(getuid())/local.screen-recording-cleaner.test.render" : nil,
        resumeAgent: paused ? URL(fileURLWithPath: "/private/tmp/render.plist") : nil)
    let samples = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: arguments[2]))) as! [[String: Any]]
    var results = [[String: Any]]()
    for sample in samples {
        if paused {
            controller.menuWillOpen(NSMenu())
        } else {
            var data = try JSONSerialization.data(withJSONObject: sample)
            data.append(10)
            controller.read(data)
        }
        results.append(controller.inspection())
    }
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: results))
    exit(0)
}
var testControl: DispatchSourceSignal?
func installTestControl(_ controller: ProgressMenu) {
    signal(SIGUSR2, SIG_IGN)
    testControl = DispatchSource.makeSignalSource(signal: SIGUSR2, queue: .main)
    testControl!.setEventHandler { controller.pressControl() }
    testControl!.resume()
    try! controller.writeReady()
}
'''


class MenuControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='recording-menu-tests-', dir='/private/tmp')
        root = Path(cls.build.name)
        source = (BUNDLE / 'ProgressMenu.swift').read_text()
        body, separator, entry = source.partition('\n// Command-line entry point.')
        assert separator, 'Native entry point moved; keep the real menu class in this test.'
        swift = root / 'MenuTest.swift'
        # Keep the production entry points, including disabled-state checks.
        entry = entry.replace('    app.run()', '    installTestControl(controller)\n    app.run()')
        entry = entry.replace('\napp.run()', '\ninstallTestControl(controller)\napp.run()')
        swift.write_text(body + HARNESS + entry)
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

    def test_paused_menu_is_clear_and_has_no_progress_timers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'snapshots.json'
            path.write_text('[{}]')
            result = subprocess.run([str(self.menu), 'render-paused', str(path)], capture_output=True,
                                    text=True, check=True, timeout=15)
            menu = json.loads(result.stdout)[0]
            self.assertEqual(menu['title'].strip(), 'Paused')
            self.assertEqual(menu['rows'], ['Screen Recording Cleaner', 'Paused',
                                           'Open finished recordings', 'Resume processing'])
            self.assertFalse(menu['has_timer'])

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
            for name in ('cleaner.py', 'progress.py', 'Resume.command'):
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
            paused_agent = paused_agent_path(agent)
            paused_definition = paused_service_definition(support, config_path, agent)
            paused_definition['ThrottleInterval'] = 1
            paused_agent.write_bytes(plistlib.dumps(paused_definition))
            paused_target = target + '.paused-menu'

            def ctl(*args, check=True):
                return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, check=check)

            def paused_pid():
                result = ctl('print', paused_target, check=False)
                match = re.search(r'\bpid = (\d+)', result.stdout)
                return int(match[1]) if match else None

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
            menu_registered_here = False
            try:
                ctl('bootstrap', f'gui/{os.getuid()}', str(paused_agent))
                menu_registered_here = True
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
                wait(lambda: paused_pid() is not None and (output / '.paused-menu-test-ready').exists())
                self.assertNotIn(str(paused_pid()), owned)
                snapshot = json.loads((output / '.paused-menu-test-ready').read_text())
                self.assertIn('Resume processing', snapshot['rows'])
                self.assertFalse(snapshot['has_timer'])
                # A crashed paused menu is restored by macOS, without restarting processing.
                first_paused_pid = paused_pid()
                os.kill(first_paused_pid, signal.SIGKILL)
                wait(lambda: paused_pid() not in (None, first_paused_pid)
                     and json.loads((output / '.paused-menu-test-ready').read_text())['pid'] == paused_pid())
                # Reload its registration to exercise the same startup used at login.
                ctl('bootout', paused_target)
                ctl('bootstrap', f'gui/{os.getuid()}', str(paused_agent))
                wait(lambda: paused_pid() is not None
                     and json.loads((output / '.paused-menu-test-ready').read_text())['pid'] == paused_pid())
                self.assertNotEqual(ctl('print', target, check=False).returncode, 0)
                before = (support / 'state/state.json').read_bytes()
                second = incoming / 'While paused.mov'
                shutil.copyfile(fixture, second)
                self.assertNotIn(str(second), ledger())
                self.assertFalse(list(output.glob('* - clean.*')))
                time.sleep(.5)  # Observe that incoming recordings do not wake the paused worker.
                self.assertEqual((support / 'state/state.json').read_bytes(), before)
                self.assertFalse(group())
                # Select Resume on the actual menu, not a substitute launchctl call.
                os.kill(paused_pid(), signal.SIGUSR2)
                wait(lambda: all(ledger().get(str(path), {}).get('status') == 'done' for path in (first, second)) and not group())
                self.assertEqual(ledger()[str(archive)]['status'], 'existing')
                for path in (first, second, archive):
                    self.assertEqual(digest(path), digest(fixture))
                self.assertEqual(len(list(output.glob('* - clean.*'))), 2)
                self.assertFalse(list(output.glob('.processing-*')))
                wait(lambda: paused_pid() is None)
                # Hold the transition lock while menu recovery and external
                # Resume both arrive. Neither may change worker state until it
                # owns the lock, and either acquisition order must finish resumed.
                ctl('disable', target)
                with (support / 'control.lock').open('a') as control_lock:
                    fcntl.flock(control_lock, fcntl.LOCK_EX)
                    ctl('kickstart', paused_target)
                    wait(lambda: paused_pid() is not None)
                    resumed = subprocess.Popen(['/bin/bash', str(BUNDLE / 'Resume.command'), str(agent)],
                                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    try:
                        time.sleep(.25)
                        self.assertIsNone(resumed.poll(), 'Resume bypassed the transition lock')
                        self.assertEqual(ctl('print', target, check=False).returncode, 0,
                                         'Menu startup stopped the worker without the transition lock')
                    finally:
                        fcntl.flock(control_lock, fcntl.LOCK_UN)
                stdout, stderr = resumed.communicate(timeout=15)
                self.assertEqual(resumed.returncode, 0, stdout + stderr)
                wait(lambda: paused_pid() is None and not group()
                     and 'state = not running' in ctl('print', target).stdout
                     and json.loads((support / 'state/runner.json').read_text())['state'] == 'idle')
                services = [ctl('print', name).stdout for name in (target, paused_target)]
                histories = (support / 'state/state.json').read_bytes()
                time.sleep(3)
                for old, name in zip(services, (target, paused_target)):
                    new = ctl('print', name).stdout
                    self.assertEqual(re.search(r'\bruns = \d+', old)[0], re.search(r'\bruns = \d+', new)[0])
                self.assertEqual((support / 'state/state.json').read_bytes(), histories)
                self.assertFalse(group())
                self.assertIsNone(paused_pid())
            finally:
                if menu_registered_here:
                    ctl('bootout', paused_target, check=False)
                if registered_here:
                    ctl('bootout', target, check=False)
                    ctl('enable', target, check=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
