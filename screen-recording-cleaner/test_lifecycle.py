"""Real launchd tests in /private/tmp. Never uses the installed recording job."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from cleaner import Cleaner, digest
from install import service_definition
from setup import wait_for_worker


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory(prefix='recording-fixtures-', dir='/private/tmp')
        cls.movie = Path(cls.fixtures.name) / 'fixture.mov'
        subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=1280x720:rate=30:duration=4', '-c:v', 'libx264', '-crf', '0',
            '-threads', '2', str(cls.movie)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='recording-lifecycle-', dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.source = self.root / 'recordings'
        self.source.mkdir()
        self.support = self.root / 'support'
        self.support.mkdir()
        self.output = self.root / 'output'
        self.state = self.support / 'state'
        self.config = {'source': str(self.source), 'output': str(self.output),
            'state_dir': str(self.state), 'python': sys.executable,
            'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe'),
            'threads': 2, 'crf': 18, 'max_bytes': 20000000, 'settle_seconds': 1,
            'notifications': False}
        self.config_path = self.support / 'config.json'
        self.config_path.write_text(json.dumps(self.config))
        shutil.copy2(Path(__file__).with_name('cleaner.py'), self.support / 'cleaner.py')
        instance = Cleaner(self.config)
        instance.initialize()
        instance.lock.close()
        for handler in instance.log.handlers[:]:
            handler.close()
            instance.log.removeHandler(handler)
        self.label = 'local.screen-recording-cleaner.test.' + self.root.name
        self.target = f'gui/{os.getuid()}/{self.label}'
        definition = service_definition(self.support, self.config_path)
        definition.update(Label=self.label, ThrottleInterval=1)
        self.plist = self.root / 'job.plist'
        self.plist.write_bytes(plistlib.dumps(definition))
        self.boot()
        self.wait(lambda: self.idle(), 'startup did not exit')

    def tearDown(self):
        self.ctl('bootout', self.target, check=False)
        self.temp.cleanup()

    def ctl(self, *args, check=True):
        return subprocess.run(['/bin/launchctl', *args], check=check, capture_output=True, text=True)

    def boot(self):
        self.ctl('bootstrap', f'gui/{os.getuid()}', str(self.plist))

    def wait(self, predicate, description, seconds=45):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if predicate():
                return
            time.sleep(.05)
        log = self.support / 'launchd.log'
        self.fail(description + '\n' + (log.read_text() if log.exists() else 'No worker log'))

    def status(self):
        path = self.state / 'runner.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def ledger(self):
        return json.loads((self.state / 'state.json').read_text())['files']

    def idle(self):
        return self.status().get('state') == 'idle' and '\n\tpid = ' not in self.ctl('print', self.target).stdout

    def done(self, path):
        return self.ledger().get(str(path), {}).get('status') == 'done'

    def add(self, name):
        path = self.source / name
        shutil.copyfile(self.movie, path)
        return path

    def test_installer_recognizes_real_worker_that_exits_immediately(self):
        def command(args, **kwargs):
            args = [self.target if arg == f'gui/{os.getuid()}/local.screen-recording-cleaner'
                    else arg for arg in args]
            return subprocess.run(args, **kwargs)
        wait_for_worker(self.support, command)
        self.wait(self.idle, 'installer test worker did not exit')

    def test_native_arrivals_slow_writes_overlaps_and_idle(self):
        first = self.source / 'Created.mov'
        first.write_bytes(self.movie.read_bytes())
        self.wait(lambda: self.done(first) and self.idle(), 'creation did not complete')
        copied = self.add('Copied.mov')
        self.wait(lambda: self.done(copied) and self.idle(), 'copy did not complete')
        staged = self.root / 'staged'
        shutil.copyfile(self.movie, staged)
        renamed = self.source / 'Renamed.mov'
        staged.rename(renamed)
        self.wait(lambda: self.done(renamed) and self.idle(), 'atomic arrival did not complete')

        slow = self.source / 'Still writing.mov'
        contents = self.movie.read_bytes()
        with slow.open('wb') as stream:
            stream.write(contents[:1000])
            stream.flush()
            time.sleep(1.3)
            stream.write(contents[1000:2000])
            stream.flush()
            time.sleep(1.3)
            self.assertFalse(self.done(slow))
            self.assertFalse(any(self.output.glob('Still writing - clean*')))
            stream.write(contents[2000:])
            stream.flush()
            time.sleep(1.3)  # Stable size, but the writer is still open.
            self.assertFalse(self.done(slow))
            overlap = self.add('Arrived while busy.mov')
        self.wait(lambda: self.done(slow) and self.done(overlap) and self.idle(), 'overlapping arrivals did not finish')

        incomplete = self.source / 'Incomplete.mov'
        incomplete.write_bytes(b'incomplete MOV')
        self.wait(lambda: self.ledger().get(str(incomplete), {}).get('status') == 'retry', 'incomplete file did not remain pending')
        self.assertFalse(any(self.output.glob('Incomplete - clean*')))
        new_arrival = self.add('During retry.mov')
        self.wait(lambda: self.done(new_arrival), 'a retry blocked a new recording')
        incomplete.unlink()
        self.wait(self.idle, 'deleted pending file left worker alive')

        before = self.status()
        before_stat = (self.state / 'runner.json').stat().st_mtime_ns
        time.sleep(3)
        self.assertTrue(self.idle())
        self.assertEqual(self.status(), before)
        self.assertEqual((self.state / 'runner.json').stat().st_mtime_ns, before_stat)
        self.assertEqual(digest(first), digest(self.movie))

    def test_crash_preserves_original_and_resume_collects_stopped_arrivals(self):
        # Force a real encode. Kill the job after it has created an FFmpeg child.
        self.ctl('bootout', self.target)
        self.config['max_bytes'] = 3000000
        self.config_path.write_text(json.dumps(self.config))
        source = self.add('Crash.mov')
        original = digest(source)
        self.boot()
        def encoding_child():
            status = self.status()
            if status.get('state') != 'processing':
                return False
            rows = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,comm='], capture_output=True, text=True, check=True).stdout
            return any(int(parts[1]) == status['pid'] and parts[3].endswith('/ffmpeg')
                       for row in rows.splitlines() if len(parts := row.split(None, 3)) == 4)
        self.wait(encoding_child, 'no real encoder appeared')
        crashed_pid = self.status()['pid']
        os.kill(crashed_pid, signal.SIGKILL)
        self.wait(lambda: '\n\tpid = ' not in self.ctl('print', self.target).stdout, 'crashed worker did not exit')
        self.assertEqual(digest(source), original)
        # Recovery is intentionally explicit, rather than an idle restart loop.
        self.ctl('bootout', self.target)
        stopped = self.add('Created while stopped.mov')
        self.boot()
        self.wait(lambda: self.done(source) and self.done(stopped) and self.idle(), 'resume did not recover pending work', seconds=90)
        self.assertEqual(digest(source), original)
        self.assertEqual(len(list(self.output.glob('Crash - clean*'))), 1)
        rows = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid=,pgid=,comm='], capture_output=True, text=True, check=True).stdout
        self.assertFalse(any(int(parts[2]) == crashed_pid for row in rows.splitlines()
                             if len(parts := row.split(None, 3)) == 4))


if __name__ == '__main__':
    unittest.main(verbosity=2)
