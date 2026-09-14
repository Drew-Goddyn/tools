import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("cleaner", HERE / "cleaner.py")
cleaner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleaner)
FFMPEG = shutil.which("ffmpeg")


class QueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.movie = Path(cls.fixtures.name) / "fixture.mov"
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=320x240:rate=60:duration=2", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=2", "-c:v", "libx264", "-crf", "0",
                        "-threads", "2", "-c:a", "aac", "-ac", "2", str(cls.movie)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "incoming"
        self.source.mkdir()
        self.output = self.root / "output"
        self.c = cleaner.Cleaner({"source": str(self.source), "output": str(self.output),
                                 "state_dir": str(self.root / "state"), "ffmpeg": FFMPEG,
                                 "ffprobe": shutil.which("ffprobe"), "threads": 2,
                                 "crf": 18, "max_bytes": 20000000, "settle_seconds": 0,
                                 "notifications": False})

    def tearDown(self):
        self.c.lock.close()
        for handler in self.c.log.handlers[:]:
            handler.close()
            self.c.log.removeHandler(handler)
        self.temp.cleanup()

    def add(self, name="Screen Recording.mov"):
        path = self.source / name
        shutil.copyfile(self.movie, path)
        return path

    def complete(self, path):
        self.c.scan()
        self.c.scan()
        entry = self.c.state["files"][str(path)]
        self.assertEqual(entry["status"], "done", entry)
        return entry

    def test_existing_and_renamed_archive_stay_excluded(self):
        old = self.add("Old.mov")
        self.c.initialize()
        os.chmod(old, 0o600)
        os.utime(old, None)
        renamed = old.with_name("Renamed.mov")
        old.rename(renamed)
        (self.source / "Screenshot.png").write_bytes(b"not a video")
        self.c.scan()
        self.c.scan()
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(self.c.state["files"][str(renamed)]["status"], "existing")

    def test_small_copy_is_exact_and_does_not_duplicate(self):
        self.c.initialize()
        source = self.add()
        original = cleaner.digest(source)
        entry = self.complete(source)
        self.assertEqual(cleaner.digest(Path(entry["output"])), original)
        os.chmod(source, 0o600)
        renamed = source.with_name("Renamed.mov")
        source.rename(renamed)
        self.c.scan()
        self.assertEqual(len(list(self.output.iterdir())), 1)
        self.assertEqual(cleaner.digest(renamed), original)

    def test_stable_open_writer_is_not_processed(self):
        self.c.initialize()
        source = self.add()
        self.c.scan()
        with source.open("r+b"):
            self.c.scan()
            self.assertEqual(self.c.state["files"][str(source)]["status"], "waiting")
            self.assertEqual(list(self.output.iterdir()), [])
        self.c.scan()
        self.assertEqual(self.c.state["files"][str(source)]["status"], "done")

    def test_settling_wait_is_observed(self):
        self.c.c["settle_seconds"] = 30
        self.c.initialize()
        source = self.add()
        self.c.scan()
        self.c.scan()
        self.assertEqual(list(self.output.iterdir()), [])
        self.c.state["files"][str(source)]["stable_since"] -= 31
        self.c.scan()
        self.assertEqual(self.c.state["files"][str(source)]["status"], "done")

    def test_existing_destination_is_never_overwritten(self):
        self.c.initialize()
        source = self.add()
        collision = self.output / "Screen Recording - clean.mov"
        collision.write_bytes(b"keep this unrelated file")
        entry = self.complete(source)
        self.assertNotEqual(Path(entry["output"]), collision)
        self.assertEqual(collision.read_bytes(), b"keep this unrelated file")

    def test_partial_video_retries_after_it_becomes_complete(self):
        self.c.initialize()
        path = self.source / "Partial.mov"
        path.write_bytes(b"unfinished")
        self.c.scan()
        self.c.scan()
        entry = self.c.state["files"][str(path)]
        self.assertEqual(entry["status"], "retry")
        self.assertEqual(list(self.output.iterdir()), [])
        self.c.scan()
        self.assertEqual(entry["attempts"], 1)
        shutil.copyfile(self.movie, path)
        self.complete(path)

    def test_encode_keeps_frames_resolution_and_stereo_audio(self):
        self.c.initialize()
        source = self.add()
        before = cleaner.digest(source)
        self.c.c["max_bytes"] = 150000
        entry = self.complete(source)
        self.assertEqual(entry["decision"], "quality_encode")
        output = self.c.probe(Path(entry["output"]), count=True)
        v = next(s for s in output["streams"] if s["codec_type"] == "video")
        a = next(s for s in output["streams"] if s["codec_type"] == "audio")
        self.assertEqual((v["width"], v["height"], v["nb_read_frames"]), (320, 240, "120"))
        self.assertEqual(a["channels"], 2)
        self.assertEqual(cleaner.digest(source), before)

    def test_soft_size_target_keeps_quality_copy_without_additional_encodes(self):
        self.c.initialize()
        source = self.source / "Motion.mov"
        subprocess.run([FFMPEG, "-v", "error", "-i", str(self.movie), "-c:v", "copy",
                        "-an", str(source)], check=True)
        self.c.c["max_bytes"] = 26000
        with patch.object(self.c, 'encode', wraps=self.c.encode) as encode:
            entry = self.complete(source)
        self.assertEqual(encode.call_count, 1)
        self.assertEqual(entry["decision"], "quality_encode")
        self.assertFalse(entry["under_limit"])
        self.assertIsNone(entry["similarity"])
        self.assertLess(entry['bytes'], source.stat().st_size)

    def test_changed_source_during_encoding_is_not_published(self):
        self.c.initialize()
        source = self.add()
        self.c.c["max_bytes"] = 150000
        original_encode = self.c.encode
        def change_after_encode(*args, **kwargs):
            original_encode(*args, **kwargs)
            with source.open("ab") as f:
                f.write(b"changed")
        self.c.encode = change_after_encode
        self.c.scan()
        self.c.scan()
        self.assertEqual(self.c.state["files"][str(source)]["status"], "retry")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_recovery_after_publication_does_not_duplicate(self):
        self.c.initialize()
        source = self.add()
        entry = self.complete(source)
        entry.update(status="processing", stable_since=0)
        self.c.write()
        self.c.scan()
        self.assertEqual(entry["status"], "done")
        self.assertEqual(len(list(self.output.iterdir())), 1)

    def test_rename_to_a_historical_name_does_not_duplicate(self):
        self.c.initialize()
        first = self.add("First.mov")
        self.complete(first)
        second = self.add("Second.mov")
        self.complete(second)
        second.unlink()
        first.rename(second)
        self.c.scan()
        self.assertEqual(self.c.state["files"][str(second)]["status"], "done")
        self.assertEqual(len(list(self.output.iterdir())), 2)

    def test_deadlines_exist_only_for_present_nonempty_pending_recordings(self):
        self.c.c['settle_seconds'] = 30
        self.c.initialize()
        empty = self.source / 'Empty.mov'
        empty.touch()
        ready = self.add()
        reply = self.c.scan()
        self.assertEqual(reply['version'], 1)
        self.assertIsNone(reply['error'])
        jobs = {j['path']: j for j in reply['pending']}
        self.assertIsNone(jobs[str(empty)]['next_check_at'])
        self.assertGreater(jobs[str(ready)]['next_check_at'], self.c.state['last_scan'])
        ready.unlink()
        self.assertEqual(self.c.scan()['pending'], [jobs[str(empty)]])
        empty.unlink()
        self.assertEqual(self.c.scan()['pending'], [])

    def test_failed_recording_reports_existing_retry_deadline(self):
        self.c.initialize()
        broken = self.source / 'Broken.mov'
        broken.write_bytes(b'incomplete MOV')
        self.c.scan()
        reply = self.c.scan()
        entry = self.c.state['files'][str(broken)]
        self.assertEqual(reply['pending'], [{'path': str(broken), 'state': 'retry',
                                            'next_check_at': entry['retry_after']}])
        self.assertEqual(self.c.scan()['pending'], reply['pending'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
