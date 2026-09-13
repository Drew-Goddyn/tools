"""Real files and installer code, isolated home; launchd/preferences use test doubles."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import setup
import upgrade
from cleaner import digest

BUNDLE = Path(__file__).resolve().parent


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / 'Test User'
        self.support, self.agent = upgrade.locations(self.home)
        self.loaded = False
        self.preference = None
        self.calls = []
        self.fail_next_start = False

    def tearDown(self):
        self.temp.cleanup()

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == '/bin/launchctl':
            action = args[1]
            if action == 'print':
                return subprocess.CompletedProcess(args, 0 if self.loaded else 1,
                                                   stdout='state = not running\nlast exit code = 0\n' if self.loaded else '')
            if action == 'bootout':
                self.loaded = False
            if action == 'bootstrap':
                self.loaded = True
                error = 'Could not start the worker' if self.fail_next_start else None
                self.fail_next_start = False
                (self.support / 'state/runner.json').write_text(json.dumps({
                    'version': 1, 'pid': 12345, 'code_sha256': digest(self.support / 'cleaner.py'),
                    'state': 'error' if error else 'idle', 'error': error}))
            if action == 'kickstart' and '-p' in args:
                return subprocess.CompletedProcess(args, 0, stdout='12345\n')
            return subprocess.CompletedProcess(args, 0, stdout='')
        if args[0] == '/usr/bin/defaults':
            if args[1] == 'read':
                return subprocess.CompletedProcess(args, 0 if self.preference is not None else 1,
                                                   stdout=(self.preference + '\n') if self.preference is not None else '')
            self.preference = args[-1] if args[1] == 'write' else None
            return subprocess.CompletedProcess(args, 0, stdout='')
        return subprocess.run(args, **kwargs)

    def run_setup(self):
        with contextlib.redirect_stdout(io.StringIO()):
            setup.setup(self.home, self.command)

    def establish_confirmed_install(self):
        self.run_setup()
        self.run_setup()

    def release(self):
        release = self.root / 'next release'
        release.mkdir()
        for name in ('cleaner.py', 'README.md', 'install.py'):
            shutil.copy2(BUNDLE / name, release / name)
        (release / 'README.md').write_text('Documentation for a later release\n')
        return release / 'upgrade.py'

    def test_fresh_setup_and_reruns_preserve_history_and_runtime_paths(self):
        source = self.home / 'Desktop/screen recordings'
        source.mkdir(parents=True)
        (source / 'Archive.mov').write_bytes(b'previous archive')
        self.preference = str(self.home / 'Desktop/old folder')
        self.run_setup()
        ledger = self.support / 'state/state.json'
        before = ledger.read_bytes()
        (source / 'After-install.mov').write_bytes(b'new arrival')
        self.run_setup()
        self.run_setup()
        self.assertEqual(ledger.read_bytes(), before)
        self.assertTrue(self.loaded)
        self.assertEqual(self.preference, str(source))
        config = json.loads((self.support / 'config.json').read_text())
        self.assertEqual(Path(config['python']).resolve(), Path(sys.executable).resolve())
        self.assertEqual(config['ffmpeg'], shutil.which('ffmpeg'))
        self.assertEqual(json.loads((self.support / 'capture-location.json').read_text())['previous'],
                         str(self.home / 'Desktop/old folder'))
        self.assertEqual(json.loads((self.support / 'upgrade.json').read_text())['phase'], 'confirmed')

    def test_later_release_upgrades_confirmed_install_and_rolls_back_with_latest_history(self):
        self.establish_confirmed_install()
        old_readme = (self.support / 'README.md').read_bytes()
        config = (self.support / 'config.json').read_bytes()
        with patch.object(upgrade, '__file__', str(self.release())):
            self.run_setup()
        self.assertNotEqual((self.support / 'README.md').read_bytes(), old_readme)
        self.assertEqual((self.support / 'config.json').read_bytes(), config)
        ledger = self.support / 'state/state.json'
        latest = json.loads(ledger.read_text())
        latest['files']['Completed-after-upgrade.mov'] = {'status': 'done'}
        ledger.write_text(json.dumps(latest))
        with contextlib.redirect_stdout(io.StringIO()):
            upgrade.rollback(self.home, self.command)
        self.assertEqual(json.loads(ledger.read_text()), latest)
        self.assertEqual((self.support / 'README.md').read_bytes(), old_readme)

    def test_failed_new_worker_restores_previous_release(self):
        self.establish_confirmed_install()
        original = (self.support / 'README.md').read_bytes()
        original_history = (self.support / 'state/state.json').read_bytes()
        self.fail_next_start = True
        with patch.object(upgrade, '__file__', str(self.release())):
            with self.assertRaisesRegex(RuntimeError, 'Could not start'):
                self.run_setup()
        self.assertTrue(self.loaded)
        self.assertEqual((self.support / 'README.md').read_bytes(), original)
        self.assertEqual((self.support / 'state/state.json').read_bytes(), original_history)
        self.assertEqual(json.loads((self.support / 'upgrade.json').read_text())['phase'], 'rolled_back')

    def test_same_build_startup_error_does_not_roll_back_an_older_upgrade(self):
        self.establish_confirmed_install()
        (self.support / 'state/runner.json').write_text(json.dumps({
            'pid': 12345, 'code_sha256': digest(self.support / 'cleaner.py'),
            'state': 'error', 'error': 'Existing problem'}))
        before = len([c for c in self.calls if c[:2] == ['/bin/launchctl', 'bootout']])
        with self.assertRaisesRegex(RuntimeError, 'Existing problem'):
            self.run_setup()
        self.assertEqual(len([c for c in self.calls if c[:2] == ['/bin/launchctl', 'bootout']]), before)
        self.assertEqual(json.loads((self.support / 'upgrade.json').read_text())['phase'], 'confirmed')

    def test_failed_fresh_setup_is_resumable_without_reseeding(self):
        self.fail_next_start = True
        with self.assertRaisesRegex(RuntimeError, 'Could not start'):
            self.run_setup()
        ledger = self.support / 'state/state.json'
        before = ledger.read_bytes()
        self.assertFalse(self.loaded)
        self.run_setup()
        self.assertEqual(ledger.read_bytes(), before)
        self.assertTrue(self.loaded)

    def test_old_error_is_not_attributed_to_a_new_worker(self):
        self.run_setup()
        status = self.support / 'state/runner.json'
        code = digest(self.support / 'cleaner.py')
        status.write_text(json.dumps({'pid': 99, 'code_sha256': code,
                                      'state': 'error', 'error': 'Old failed run'}))
        observed_old_error = False
        def command(args, **kwargs):
            nonlocal observed_old_error
            if args[:2] == ['/bin/launchctl', 'print']:
                if observed_old_error:
                    status.write_text(json.dumps({'pid': 12345, 'code_sha256': code,
                                                  'state': 'idle', 'error': None}))
                observed_old_error = True
            return self.command(args, **kwargs)
        setup.wait_for_worker(self.support, command)
        self.assertTrue(observed_old_error)

    def test_capture_location_restore_respects_later_user_choice(self):
        self.run_setup()
        with contextlib.redirect_stdout(io.StringIO()):
            setup.restore_capture_location(self.home, self.command)
        self.assertIsNone(self.preference)
        self.preference = 'A location chosen later'
        with contextlib.redirect_stdout(io.StringIO()):
            setup.restore_capture_location(self.home, self.command)
        self.assertEqual(self.preference, 'A location chosen later')

    def test_changed_source_can_still_restore_the_original_capture_location(self):
        self.preference = 'Original save location'
        self.run_setup()
        config_path = self.support / 'config.json'
        config = json.loads(config_path.read_text())
        config['source'] = str(self.home / 'Custom recordings')
        Path(config['source']).mkdir()
        config_path.write_text(json.dumps(config))
        self.run_setup()
        self.assertEqual(self.preference, config['source'])
        with contextlib.redirect_stdout(io.StringIO()):
            setup.restore_capture_location(self.home, self.command)
        self.assertEqual(self.preference, 'Original save location')


class CommandTests(unittest.TestCase):
    def test_finder_entrypoint_handles_spaces_and_calls_setup(self):
        # Execute the actual shell entry point with fake Homebrew/setup commands.
        # No package manager, launchd service, or real preferences are changed.
        with tempfile.TemporaryDirectory(prefix='recording installer ') as temp:
            root = Path(temp)
            bundle = root / 'Downloaded Package'
            bundle.mkdir()
            fake_brew = root / 'homebrew'
            binaries = fake_brew / 'bin'
            binaries.mkdir(parents=True)
            (binaries / 'python3').symlink_to(sys.executable)
            for name in ('ffmpeg', 'ffprobe'):
                (binaries / name).write_text('#!/bin/sh\nexit 0\n')
                (binaries / name).chmod(0o700)
            brew = binaries / 'brew'
            brew.write_text('#!/bin/sh\n[ "$1" = --prefix ] || exit 3\nprintf "%s\\n" "$TEST_BREW_PREFIX"\n')
            brew.chmod(0o700)
            shutil.copy2(BUNDLE / 'Install.command', bundle / 'Install.command')
            (bundle / 'install.py').write_text(
                'from pathlib import Path\nimport sys\n'
                'root=Path(__file__).parent\n'
                'assert sys.argv[1:]==["setup"]\n(root/"setup-called").touch()\n')
            result = subprocess.run(['/bin/bash', str(bundle / 'Install.command')], cwd='/',
                env=dict(os.environ, PATH=str(binaries) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                         TEST_BREW_PREFIX=str(fake_brew)), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((bundle / 'setup-called').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
