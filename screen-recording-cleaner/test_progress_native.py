"""Opt-in desktop lifecycle checks. Shows a temporary menu item, uses no recordings."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from build_progress import build_progress
from progress import Progress, UI_BINARY, UI_PLIST

BUNDLE = Path(__file__).resolve().parent


class NativeProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='recording-progress-', dir='/private/tmp')
        self.root = Path(self.temp.name)
        self.executable = self.root / UI_BINARY
        self.executable.parent.mkdir(parents=True)
        shutil.copy2(build_progress(BUNDLE), self.executable)
        shutil.copy2(BUNDLE / 'ProgressInfo.plist', self.root / UI_PLIST)

    def tearDown(self):
        self.temp.cleanup()

    def process_exists(self, pid):
        return bool(subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'pid='],
                                   capture_output=True, text=True, check=False).stdout.strip())

    def test_native_display_exits_normally_on_eof(self):
        progress = Progress({'output': str(self.root)}, executable=self.executable)
        try:
            progress.show('Compressing', filename='Desktop lifecycle check.mov', total_frames=100)
            progress.advance({'frame': '25'})
            child = progress.child
            time.sleep(.5)
            self.assertIsNone(child.poll(), 'Native UI exited before its input closed')
        finally:
            progress.close()
        self.assertEqual(child.returncode, 0, 'UI needed forced termination instead of exiting on EOF')
        self.assertFalse(self.process_exists(child.pid))

    def test_parent_crash_closes_display_without_a_resident_watchdog(self):
        script = ('import signal,sys\n'
                  'sys.path.insert(0,sys.argv[1])\n'
                  'from progress import Progress\n'
                  'p=Progress({"output":sys.argv[3]},executable=sys.argv[2])\n'
                  'p.show("Compressing",filename="Parent crash check.mov",total_frames=100)\n'
                  'p.advance({"frame":"25"})\n'
                  'print(p.child.pid,flush=True)\n'
                  'signal.pause()\n')
        parent = subprocess.Popen([sys.executable, '-c', script, str(BUNDLE), str(self.executable), str(self.root)],
                                  stdout=subprocess.PIPE, text=True)
        pid = int(parent.stdout.readline())
        try:
            time.sleep(.5)
            self.assertTrue(self.process_exists(pid))
            parent.kill()
            parent.wait(timeout=3)
            deadline = time.monotonic() + 3
            while self.process_exists(pid) and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertFalse(self.process_exists(pid), 'Native UI survived its parent and the pipe closing')
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait()
            parent.stdout.close()
            if self.process_exists(pid):
                os.kill(pid, signal.SIGKILL)


if __name__ == '__main__':
    unittest.main(verbosity=2)
