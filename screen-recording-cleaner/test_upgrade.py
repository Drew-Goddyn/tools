"""Real filesystem/lock tests; launchctl is a deliberate test double."""
import contextlib
import fcntl
import io
import json
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import upgrade


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.support, self.agent = upgrade.locations(self.home)
        (self.support / 'state').mkdir(parents=True)
        self.agent.parent.mkdir(parents=True)
        self.ledger = self.support / 'state/state.json'
        self.ledger.write_text(json.dumps({'version': 1, 'initialized_at': 1,
                                          'files': {'archive.mov': {'status': 'existing'}}}))
        (self.support / 'installation.json').write_text(json.dumps({
            'installer': 'local.screen-recording-cleaner.v1', 'phase': 'active'}))
        (self.support / 'cleaner.py').write_text('old worker')
        (self.support / 'README.md').write_text('old documentation')
        self.config = {'source': str(self.home / 'recordings'), 'unchanged': True}
        (self.support / 'config.json').write_text(json.dumps(self.config))
        self.agent.write_bytes(plistlib.dumps({'Label': 'local.screen-recording-cleaner', 'StartInterval': 15}))
        self.original_ledger = self.ledger.read_bytes()
        self.original_agent = self.agent.read_bytes()
        self.loaded = True
        self.fail_start = False
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def command(self, args, **kwargs):
        action = args[1]
        self.calls.append(action)
        if action == 'print':
            return subprocess.CompletedProcess(args, 0 if self.loaded else 1)
        if action == 'bootout':
            self.loaded = False
        if action == 'bootstrap':
            # Startup must happen after migration releases the worker lock.
            with (self.support / 'state/lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.fail_start:
                self.fail_start = False
                raise subprocess.CalledProcessError(5, args)
            self.loaded = True
        return subprocess.CompletedProcess(args, 0)

    def run_upgrade(self):
        with contextlib.redirect_stdout(io.StringIO()):
            upgrade.upgrade(self.home, self.command)

    def test_upgrade_preserves_history_and_rollback_keeps_new_progress(self):
        self.run_upgrade()
        self.assertTrue((self.support / 'progress.py').is_file())
        self.assertTrue((self.support / upgrade.UI_BINARY).stat().st_mode & 0o100)
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)
        agent = plistlib.loads(self.agent.read_bytes())
        self.assertNotIn('StartInterval', agent)
        self.assertNotIn('KeepAlive', agent)
        self.assertEqual(agent['WatchPaths'], [self.config['source']])
        self.assertEqual(agent['ThrottleInterval'], 30)
        self.assertEqual(json.loads((self.support / 'config.json').read_text()), self.config)
        latest = json.loads(self.ledger.read_text())
        latest['files']['new.mov'] = {'status': 'done', 'output': 'new - clean.mov'}
        self.ledger.write_text(json.dumps(latest))
        with contextlib.redirect_stdout(io.StringIO()):
            upgrade.rollback(self.home, self.command)
        self.assertEqual(json.loads(self.ledger.read_text()), latest)
        self.assertEqual(self.agent.read_bytes(), self.original_agent)
        self.assertEqual((self.support / 'cleaner.py').read_text(), 'old worker')
        self.assertFalse((self.support / 'progress.py').exists())
        self.assertFalse((self.support / upgrade.PROGRESS_APP).exists())
        self.assertFalse((self.support / 'recording-listener').exists())
        self.assertTrue(self.loaded)

    def test_activation_failure_restores_old_service(self):
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'previous service restored'):
            self.run_upgrade()
        self.assertEqual(self.agent.read_bytes(), self.original_agent)
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)
        self.assertTrue(self.loaded)

    def test_ui_build_failure_leaves_active_installation_untouched(self):
        original = {path: path.read_bytes() for path in self.support.rglob('*') if path.is_file()}
        with patch.object(upgrade, 'build_progress', side_effect=RuntimeError('Swift build failed')):
            with self.assertRaisesRegex(RuntimeError, 'Swift build failed'):
                self.run_upgrade()
        self.assertEqual(self.calls, [])
        self.assertTrue(self.loaded)
        self.assertEqual({path: path.read_bytes() for path in self.support.rglob('*') if path.is_file()}, original)

    def test_resident_listener_is_removed_and_can_be_restored(self):
        listener = self.support / 'recording-listener'
        listener.write_bytes(b'previous native executable')
        self.run_upgrade()
        self.assertFalse(listener.exists())
        with contextlib.redirect_stdout(io.StringIO()):
            upgrade.rollback(self.home, self.command)
        self.assertEqual(listener.read_bytes(), b'previous native executable')

    def test_resume_after_process_interruption_during_replacement(self):
        real_copy = upgrade.atomic_copy
        interrupted = False
        def copy(source, target):
            nonlocal interrupted
            real_copy(source, target)
            if target == self.support / 'cleaner.py' and not interrupted:
                interrupted = True
                raise KeyboardInterrupt('simulated installer process interruption')
        with patch.object(upgrade, 'atomic_copy', copy):
            with self.assertRaises(KeyboardInterrupt):
                self.run_upgrade()
        self.assertFalse(self.loaded)
        self.run_upgrade()
        self.assertTrue(self.loaded)
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)
        self.assertEqual(json.loads((self.support / 'upgrade.json').read_text())['phase'], 'installed')

    def test_invalid_history_is_never_reseeded(self):
        self.ledger.write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'will not be reset'):
            self.run_upgrade()
        self.assertEqual(self.ledger.read_text(), '{}')
        self.assertEqual(self.calls, [])

    def test_upgrade_waits_for_active_worker_lock(self):
        holder = subprocess.Popen([sys.executable, '-c',
            'import fcntl,sys; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX); '
            'print("locked",flush=True); sys.stdin.read()', str(self.support / 'state/lock')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        failures = []
        def migrate():
            try:
                self.run_upgrade()
            except BaseException as error:
                failures.append(error)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'locked')
            thread = threading.Thread(target=migrate)
            thread.start()
            time.sleep(.3)
            self.assertTrue(thread.is_alive())
            self.assertEqual(self.calls, [])
            self.assertEqual(self.agent.read_bytes(), self.original_agent)
            holder.stdin.close()
            holder.wait(timeout=5)
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(self.loaded)
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait()
            holder.stdout.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
