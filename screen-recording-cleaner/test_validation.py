"""Real media checks for the two-pass processing path and prompt termination."""
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from cleaner import Cleaner, digest, frame_times, signature


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'incoming'
        self.source.mkdir()
        self.config = dict(source=str(self.source), output=str(self.root / 'finished'),
                           state_dir=str(self.root / 'state'), ffmpeg=shutil.which('ffmpeg'),
                           ffprobe=shutil.which('ffprobe'), threads=2, crf=18,
                           max_bytes=1, settle_seconds=0, notifications=False, show_progress=False)
        self.cleaner = Cleaner(self.config)
        self.cleaner.initialize()

    def tearDown(self):
        self.cleaner.lock.close()
        for handler in self.cleaner.log.handlers[:]:
            handler.close()
            self.cleaner.log.removeHandler(handler)
        self.temp.cleanup()

    def movie(self, *, audio='aac', variable=False):
        path = self.source / 'Recording.mov'
        args = [self.config['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=320x240:rate=30:duration=2', '-f', 'lavfi', '-i',
                'sine=frequency=440:duration=2', '-map', '0:v', '-map', '1:a',
                '-c:v', 'libx264', '-crf', '0', '-threads', '2', '-c:a', audio, '-ac', '2']
        if variable:
            args += ['-vf', r'select=not(eq(mod(n\,5)\,1))', '-fps_mode', 'vfr']
        subprocess.run(args + [str(path)], check=True)
        return path

    def complete(self, path):
        entry = dict(signature=signature(path), status='processing')
        self.cleaner.state['files'][str(path)] = entry
        self.cleaner.process(path, entry)
        self.assertEqual(entry['status'], 'done')
        return Path(entry['output'])

    def packets(self, path, stream):
        result = subprocess.check_output([self.config['ffprobe'], '-v', 'error', '-select_streams', stream,
            '-show_packets', '-show_data_hash', 'sha256', '-of', 'json', str(path)])
        return json.loads(result)['packets']

    def test_variable_timing_and_copied_audio_survive_two_full_video_passes(self):
        source = self.movie(variable=True)
        before = digest(source)
        commands = []
        ffmpeg = self.cleaner.ffmpeg
        def capture(path, args):
            command = ffmpeg(path, args)
            commands.append(command)
            return command
        with patch.object(self.cleaner, 'ffmpeg', capture), patch.object(self.cleaner, 'probe', wraps=self.cleaner.probe) as probe:
            output = self.complete(source)
        self.assertEqual(len(commands), 2, 'A redundant full decode pass returned')
        self.assertTrue(all(not call.kwargs.get('count') for call in probe.call_args_list))
        self.assertEqual(commands[0][commands[0].index('-preset') + 1], 'fast')
        self.assertEqual(digest(source), before)
        source_frames = self.packets(source, 'v:0')
        target_frames = self.packets(output, 'v:0')
        self.assertEqual(len(source_frames), len(target_frames))
        # Inspect independently of the worker's decoded-frame statistics.
        first = sorted(Fraction(p['pts_time']) for p in source_frames)
        second = sorted(Fraction(p['pts_time']) for p in target_frames)
        self.assertEqual([t - first[0] for t in first], [t - second[0] for t in second])
        self.assertGreater(len(set(b - a for a, b in zip(first, first[1:]))), 1)
        self.assertEqual([p['data_hash'] for p in self.packets(source, 'a:0')],
                         [p['data_hash'] for p in self.packets(output, 'a:0')])

    def test_exact_copy_needs_only_one_decode_and_keeps_every_byte(self):
        source = self.movie()
        self.config['max_bytes'] = source.stat().st_size + 1
        with patch.object(self.cleaner, 'ffmpeg', wraps=self.cleaner.ffmpeg) as ffmpeg:
            output = self.complete(source)
        self.assertEqual(ffmpeg.call_count, 1)
        self.assertEqual(digest(source), digest(output))

    def test_pcm_audio_is_converted_and_alac_is_copied(self):
        for codec in ('pcm_s16le', 'alac'):
            with self.subTest(codec=codec):
                source = self.movie(audio=codec)
                output = self.complete(source)
                info = self.cleaner.probe(output)
                audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
                self.assertEqual(audio['codec_name'], 'alac' if codec == 'alac' else 'aac')
                self.assertEqual(audio['channels'], 2)
                if codec == 'alac':
                    self.assertEqual([p['data_hash'] for p in self.packets(source, 'a:0')],
                                     [p['data_hash'] for p in self.packets(output, 'a:0')])
                source.unlink()

    def test_missing_frame_or_changed_timing_cannot_pass_validation(self):
        source = self.movie()
        info = self.cleaner.probe(source)
        original = [Fraction(i, 30) for i in range(60)]
        for name, video_filter, expected in (
            ('dropped', r'select=not(eq(n\,30))', 'frame count differs'),
            ('retimed', r'setpts=PTS+if(eq(N\,30)\,0.01/TB\,0)', 'frame timing changed'),
        ):
            with self.subTest(name=name):
                target = self.root / (name + '.mp4')
                subprocess.run([self.config['ffmpeg'], '-v', 'error', '-i', str(source),
                    '-map', '0:v', '-map', '0:a', '-vf', video_filter, '-c:v', 'libx264',
                    '-crf', '18', '-threads', '2', '-c:a', 'copy', '-fps_mode', 'passthrough',
                    '-enc_time_base', 'demux', str(target)], check=True)
                with self.assertRaisesRegex(RuntimeError, expected):
                    self.cleaner.validate(info, target, original)

    def test_corrupt_complete_container_is_not_published_even_when_copied(self):
        source = self.movie()
        packet = self.packets(source, 'v:0')[20]
        with source.open('r+b') as stream:
            stream.seek(int(packet['pos']))
            stream.write(b'\x00' * int(packet['size']))
        self.config['max_bytes'] = source.stat().st_size + 1
        self.cleaner.scan()
        self.cleaner.scan()
        self.assertEqual(self.cleaner.state['files'][str(source)]['status'], 'retry')
        self.assertFalse(list(Path(self.config['output']).glob('* - clean.*')))

    def test_frame_evidence_rejects_skips_and_empty_results(self):
        trace = self.root / 'trace.txt'
        for contents in ('', '0 0 1/30 0\n1 2 1/30 2\n'):
            trace.write_text(contents)
            with self.assertRaises(RuntimeError):
                frame_times(trace)

    def test_worker_term_reaps_a_child_that_ignores_term(self):
        source = self.movie()
        self.cleaner.scan()  # Register a stable, unfinished recording.
        self.cleaner.lock.close()
        helper = self.root / 'stubborn-ffmpeg'
        child_pid = self.root / 'child.pid'
        helper.write_text('#!' + sys.executable + '\nimport os,signal,time\n'
            'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
            'open(' + repr(str(child_pid)) + ',"w").write(str(os.getpid()))\n'
            'while True: time.sleep(60)\n')
        helper.chmod(0o700)
        self.config['ffmpeg'] = str(helper)
        config = self.root / 'config.json'
        config.write_text(json.dumps(self.config))
        worker = subprocess.Popen([sys.executable, str(Path(__file__).with_name('cleaner.py')),
            '--config', str(config), 'scan'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pid = None
        try:
            deadline = time.monotonic() + 10
            while not child_pid.exists():
                self.assertIsNone(worker.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.02)
            pid = int(child_pid.read_text())
            worker.terminate()  # Signal only Python; it must clean up its own child.
            stdout, stderr = worker.communicate(timeout=5)
            self.assertEqual(worker.returncode, 143, (stdout, stderr))
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
            self.assertFalse(list(Path(self.config['output']).glob('* - clean.*')))
            self.assertTrue(source.exists())
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.communicate()
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    unittest.main(verbosity=2)
