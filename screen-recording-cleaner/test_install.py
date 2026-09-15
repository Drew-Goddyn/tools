import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from install import install, resume_shortcut


class InstallationTests(unittest.TestCase):
    def test_resume_shortcut_preserves_collisions_and_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            support = root / 'support'
            support.mkdir()
            script = support / 'Resume.command'
            script.write_text('#!/bin/bash\n')
            output = root / 'finished'
            output.mkdir()
            collision = output / 'Resume Screen Recording Cleaner.command'
            collision.write_text('Unrelated user file')
            shortcut = resume_shortcut(support, output)
            self.assertEqual(collision.read_text(), 'Unrelated user file')
            self.assertEqual(shortcut.readlink(), script)
            self.assertTrue(script.stat().st_mode & 0o100)
            self.assertEqual(resume_shortcut(support, output), shortcut)
            self.assertEqual(len(list(output.iterdir())), 2)

    def test_registration_failure_resumes_without_resetting_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            incoming = root / "Desktop/screen recordings"
            incoming.mkdir(parents=True)
            (incoming / "Old.mov").write_bytes(b"existing archive entry")
            fail_registration = True
            def command(args, **kwargs):
                if args[0] != "/bin/launchctl":
                    return subprocess.run(args, **kwargs)
                if args[1] == "print":
                    return subprocess.CompletedProcess(args, 1)
                if args[1] == "bootstrap" and fail_registration:
                    raise subprocess.CalledProcessError(5, args)
                return subprocess.CompletedProcess(args, 0)
            with self.assertRaises(subprocess.CalledProcessError):
                install(root, command)
            support = root / "Library/Application Support/Screen Recording Cleaner"
            state_path = support / "state/state.json"
            original_state = state_path.read_bytes()
            (incoming / "New.mov").write_bytes(b"arrived after initial baseline")
            fail_registration = False
            with contextlib.redirect_stdout(io.StringIO()):
                install(root, command)
            self.assertEqual(state_path.read_bytes(), original_state)
            self.assertEqual(json.loads((support / "installation.json").read_text())["phase"], "active")

    def test_unrecognized_installation_is_not_modified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Desktop/screen recordings").mkdir(parents=True)
            support = root / "Library/Application Support/Screen Recording Cleaner"
            support.mkdir(parents=True)
            marker = support / "unrelated.txt"
            marker.write_text("preserve")
            with self.assertRaisesRegex(RuntimeError, "unrecognized"):
                install(root)
            self.assertEqual(marker.read_text(), "preserve")
            self.assertEqual(len(list(support.iterdir())), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
