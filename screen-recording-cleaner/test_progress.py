"""Real progress pipes and media; desktop UI is checked separately."""
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from cleaner import Cleaner, digest, signature
from progress import Progress, run_ffmpeg


class CapturedProgress(Progress):
    def __init__(self):
        super().__init__({'output': '/unused', 'show_progress': False})
        self.updates = []

    def send(self):
        self.updates.append(dict(self.message))


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def helper(self, body):
        import sys
        path = self.root / 'helper'
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o700)
        return path

    def wait(self, predicate, message):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail(message)
            time.sleep(.01)

    def receiver(self):
        return self.helper('import sys\n'
            'with open(sys.argv[1], "w") as output:\n'
            '    for line in sys.stdin:\n'
            '        output.write(line)\n        output.flush()\n')

    def rows(self, path):
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_ui_is_lazy_receives_snapshots_and_exits_at_eof(self):
        helper = self.receiver()
        output = self.root / 'updates.jsonl'
        progress = Progress({'output': str(output)}, executable=helper)
        self.assertIsNone(progress.child)
        try:
            progress.show('Compressing', filename='A recording.mov', total_frames=100, duration=4)
            progress.advance({'frame': '25', 'out_time_us': '1000000'})
            progress.advance({'frame': '70', 'out_time_us': '2800000'})
            self.wait(lambda: any(row.get('percent') == 70 for row in self.rows(output)), 'Latest progress was not delivered')
            child = progress.child
        finally:
            progress.close()
        self.assertEqual(child.returncode, 0)
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual(rows[-1]['percent'], 70)
        self.assertTrue(all(row['filename'] == 'A recording.mov' for row in rows))

    def test_stopped_ui_does_not_fail_the_worker(self):
        progress = Progress({'output': '/unused'}, executable=self.helper('import sys\nsys.stdin.readline()\n'))
        try:
            progress.show('Compressing', total_frames=100)
            progress.child.wait(timeout=3)
            with self.assertLogs('progress', level='WARNING'):
                progress.advance({'frame': '10'})
                self.wait(lambda: not progress.enabled, 'Closed UI was not detected')
            self.assertFalse(progress.enabled)
        finally:
            progress.close()

    def test_backpressure_does_not_block_progress(self):
        progress = Progress({'output': '/unused'}, executable=self.helper('import time\ntime.sleep(30)\n'))
        try:
            progress.show('Compressing', total_frames=2000)
            start = time.monotonic()
            for frame in range(2000):
                progress.advance({'frame': str(frame)})
            self.assertLess(time.monotonic() - start, 3)
        finally:
            progress.close()

    def test_latest_step_is_delivered_after_backpressure_without_another_update(self):
        output = self.root / 'updates.jsonl'
        progress = Progress({'output': str(output)}, executable=self.receiver())
        try:
            progress.show('Compressing', filename='First.mov', total_frames=100000, step=2, total_steps=5)
            self.wait(lambda: bool(self.rows(output)), 'Receiver did not start')
            os.kill(progress.child.pid, signal.SIGSTOP)
            deadline = time.monotonic() + 3
            frame = 0
            while select.select([], [progress.child.stdin.fileno()], [], 0)[1]:
                frame += 1
                progress.advance({'frame': str(frame)})
                self.assertLess(time.monotonic(), deadline, 'Could not fill the real pipe')
                time.sleep(.001)
            progress.show('Waiting to retry', filename='Next.mov')
            os.kill(progress.child.pid, signal.SIGCONT)
            self.wait(lambda: any(row['phase'] == 'Waiting to retry' for row in self.rows(output)),
                      'Pending phase was lost after the reader resumed')
            self.assertEqual(self.rows(output)[-1]['filename'], 'Next.mov')
            self.assertIsNone(self.rows(output)[-1]['step'])
            self.assertIsNone(self.rows(output)[-1]['total_steps'])
        finally:
            progress.close()

    def test_legal_quoted_filename_larger_than_pipe_buf_is_delivered(self):
        source = self.root / ('"' * 251 + '.mov')
        source.touch()
        output = self.root / 'updates.jsonl'
        progress = Progress({'output': str(output)}, executable=self.receiver())
        try:
            progress.show('Inspecting original', filename=source.name)
            self.assertGreater(len(json.dumps(progress.message).encode()), os.fpathconf(progress.child.stdin.fileno(), 'PC_PIPE_BUF'))
            self.wait(lambda: bool(self.rows(output)), 'Long filename suppressed feedback')
            self.assertEqual(self.rows(output)[0]['filename'], source.name)
        finally:
            progress.close()

    def test_repeated_frame_reports_preserve_the_actual_advance_time(self):
        progress = CapturedProgress()
        with patch('progress.time.time', return_value=1000):
            progress.show('Compressing', total_frames=100)
        with patch('progress.time.time', return_value=1010):
            progress.advance({'frame': '20'})
        with patch('progress.time.time', return_value=1100):
            progress.advance({'frame': '20'})
        self.assertEqual(progress.updates[-1]['frame_advanced_at'], 1010)
        self.assertEqual(progress.updates[-1]['updated_at'], 1100)
        progress.show('Checking frame counts')
        self.assertNotIn('frame_advanced_at', progress.updates[-1])

    def test_unknown_progress_and_new_steps_do_not_reuse_a_percentage(self):
        progress = CapturedProgress()
        progress.show('Compressing', total_frames=100)
        progress.advance({'frame': '50'})
        self.assertEqual(progress.updates[-1]['percent'], 50)
        progress.show('Checking copy', step=3, total_steps=5)
        self.assertNotIn('percent', progress.updates[-1])
        self.assertNotIn('frame', progress.updates[-1])
        self.assertEqual(progress.updates[-1]['step'], 3)
        progress.show('Verifying playback', duration=4)
        for key in ('step', 'total_steps'):
            self.assertIsNone(progress.updates[-1][key], f'{key} leaked from the previous step')
        progress.advance({'frame': 'N/A', 'out_time_us': 'N/A'})
        self.assertNotIn('percent', progress.updates[-1])
        progress.advance({'frame': '0', 'out_time_us': '2000000'})
        self.assertEqual(progress.updates[-1]['percent'], 50)

    def test_stderr_cannot_deadlock_progress_and_nonzero_exit_is_preserved(self):
        helper = self.helper('import sys\n'
            'sys.stderr.write("x" * 200000 + "end of encoder error")\n'
            'print("frame=12\\nprogress=end", flush=True)\n'
            'sys.exit(7)\n')
        progress = CapturedProgress()
        progress.show('Compressing', total_frames=24)
        with self.assertRaisesRegex(RuntimeError, 'end of encoder error'):
            run_ffmpeg([str(helper)], progress)
        self.assertEqual(progress.updates[-1]['percent'], 50)

    def test_real_recording_reports_five_steps_and_frame_advances(self):
        source = self.root / 'recordings'
        source.mkdir()
        config = {'source': str(source), 'output': str(self.root / 'output'),
                  'state_dir': str(self.root / 'state'), 'ffmpeg': shutil.which('ffmpeg'),
                  'ffprobe': shutil.which('ffprobe'), 'threads': 2, 'crf': 18,
                  'max_bytes': 20000, 'settle_seconds': 1, 'notifications': False}
        progress = CapturedProgress()
        cleaner = Cleaner(config, progress=progress)
        try:
            cleaner.initialize()
            movie = source / 'Motion.mov'
            subprocess.run([config['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=640x360:rate=30:duration=2', '-c:v', 'libx264', '-crf', '0',
                '-threads', '2', str(movie)], check=True)
            original = digest(movie)
            entry = {'signature': signature(movie), 'status': 'processing'}
            cleaner.state['files'][str(movie)] = entry
            ffmpeg = cleaner.ffmpeg
            def paced_ffmpeg(source, args):
                command = ffmpeg(source, args)
                index = command.index('-i')
                return command[:index] + ['-re'] + command[index:]
            probe = cleaner.probe
            def explained_probe(path, count=False):
                # The correct step must be visible before the blocking frame count.
                message = progress.updates[-1]
                self.assertTrue(count)
                self.assertEqual(message['step'], 1 if path.parent == source else 3)
                self.assertEqual(message['total_steps'], 5)
                self.assertNotIn('percent', message)
                self.assertNotIn('frame', message)
                return probe(path, count=count)
            # A tiny unpaced fixture can finish between progress reports. Pacing
            # input at playback speed proves intermediate updates without a huge fixture.
            with patch.object(cleaner, 'ffmpeg', paced_ffmpeg), patch.object(cleaner, 'probe', explained_probe):
                cleaner.process(movie, entry)
            self.assertEqual(entry['status'], 'done')
            self.assertEqual(entry['decision'], 'quality_encode')
            compressed = Path(entry['output'])
            self.assertEqual(digest(movie), original)
            phases = list(dict.fromkeys(row['phase'] for row in progress.updates))
            self.assertEqual(phases, ['Counting original frames', 'Compressing', 'Checking copy',
                                     'Checking playback', 'Saving finished copy'])
            self.assertEqual(list(dict.fromkeys(row['step'] for row in progress.updates)), [1, 2, 3, 4, 5])
            self.assertTrue(all(row['total_steps'] == 5 for row in progress.updates))
            for phase in ('Compressing', 'Checking playback'):
                frames = [row['frame'] for row in progress.updates if row['phase'] == phase and 'frame' in row]
                self.assertGreater(len(set(frames)), 1, (phase, frames))
                self.assertEqual(frames[-1], 60)
                self.assertLess(frames[0], frames[-1])
            # Copying is still step two; each recording starts its own five steps.
            small = source / 'Small.mov'
            shutil.copyfile(movie, small)
            config['max_bytes'] = small.stat().st_size + 1
            entry = {'signature': signature(small), 'status': 'processing'}
            cleaner.state['files'][str(small)] = entry
            progress.updates.clear()
            with patch.object(cleaner, 'probe', explained_probe):
                cleaner.process(small, entry)
            self.assertEqual(entry['decision'], 'already_small')
            self.assertEqual(digest(Path(entry['output'])), original)
            self.assertEqual(digest(small), original)
            copy = next(row for row in progress.updates if row['phase'] == 'Copying unchanged')
            self.assertEqual(copy['step'], 2)
            self.assertFalse(any(row['phase'] == 'Compressing' for row in progress.updates))
            self.assertEqual(list(dict.fromkeys(row['step'] for row in progress.updates)), [1, 2, 3, 4, 5])

            # A lossless re-encode of the smaller, lossy fixture is larger. Its
            # fallback copy belongs to saving, so the step number never goes backward.
            fallback = source / 'Fallback.mp4'
            shutil.copyfile(compressed, fallback)
            config.update(max_bytes=0, crf=0)
            entry = {'signature': signature(fallback), 'status': 'processing'}
            cleaner.state['files'][str(fallback)] = entry
            progress.updates.clear()
            cleaner.process(fallback, entry)
            self.assertEqual(entry['decision'], 'original_smaller')
            self.assertEqual(digest(Path(entry['output'])), digest(fallback))
            self.assertEqual(digest(fallback), digest(compressed))
            phases = list(dict.fromkeys(row['phase'] for row in progress.updates))
            self.assertEqual(phases[-1], 'Saving smaller original')
            self.assertEqual(list(dict.fromkeys(row['step'] for row in progress.updates)), [1, 2, 3, 4, 5])
        finally:
            cleaner.lock.close()
            for handler in cleaner.log.handlers[:]:
                handler.close()
                cleaner.log.removeHandler(handler)


if __name__ == '__main__':
    unittest.main(verbosity=2)
