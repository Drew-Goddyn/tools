"""Best-effort, active-only menu feedback. A closed pipe ends the native UI."""
import json
import logging
import math
import os
from pathlib import Path
import select
import subprocess
import tempfile
import threading
import time

PROGRESS_APP = 'Recording Progress.app'
UI_BINARY = PROGRESS_APP + '/Contents/MacOS/RecordingProgress'
UI_PLIST = PROGRESS_APP + '/Contents/Info.plist'


class Progress:
    def __init__(self, config, executable=None, launchd_target=None):
        self.executable = Path(executable or Path(__file__).parent / UI_BINARY)
        self.enabled = config.get('show_progress', True) and self.executable.is_file()
        self.output = config['output']
        self.launchd_target = launchd_target
        self.child = None
        self.message = {}
        self.writer = None
        self.pending = None
        self.stopping = False
        self.lock = threading.Lock()
        self.wake_read = self.wake_write = None

    def show(self, phase, filename=None, total_frames=None, duration=None,
             step=None, total_steps=None):
        message = {'version': 1, 'phase': phase,
                   'filename': filename if filename is not None else self.message.get('filename', ''),
                   'total_frames': total_frames, 'duration': duration,
                   'step': step, 'total_steps': total_steps}
        if all(self.message.get(key) == value for key, value in message.items()):
            return
        message.update(phase_started_at=time.time(), updated_at=time.time())
        self.message = message
        self.send()

    def advance(self, values):
        try:
            frame = max(0, int(values.get('frame', 0)))
            frames = float(self.message.get('total_frames') or 0)
            duration = float(self.message.get('duration') or 0)
            seconds = max(0, float(values.get('out_time_us', 0)) / 1_000_000)
            fraction = frame / frames if frames > 0 else seconds / duration if duration > 0 else None
            percent = min(100, max(0, int(fraction * 100))) if fraction is not None and math.isfinite(fraction) else None
        except (ValueError, TypeError, OverflowError):
            return  # Some FFmpeg fields are N/A before the first output frame.
        now = time.time()
        if frame > self.message.get('frame', 0):
            self.message['frame_advanced_at'] = now
        self.message.update(frame=frame, percent=percent, updated_at=now)
        self.send()

    def wake(self):
        try:
            os.write(self.wake_write, b'x')
        except OSError:
            pass  # A full wakeup pipe is already readable by the delivery thread.

    def send(self):
        if not self.enabled:
            return
        try:
            if self.child is None:
                args = [str(self.executable), self.output]
                if self.launchd_target:
                    args.append(self.launchd_target)
                self.child = subprocess.Popen(args, stdin=subprocess.PIPE,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.set_blocking(self.child.stdin.fileno(), False)
                self.wake_read, self.wake_write = os.pipe()
                os.set_blocking(self.wake_write, False)
                self.writer = threading.Thread(target=self.deliver, daemon=True)
                self.writer.start()
            # Coalesce unsent snapshots. The worker never waits for the display.
            data = (json.dumps(self.message, ensure_ascii=False) + '\n').encode()
            with self.lock:
                self.pending = data
            self.wake()
        except OSError as error:
            self.enabled = False
            logging.getLogger(__name__).warning('Progress indicator unavailable: %s', error)

    def deliver(self):
        data = b''
        offset = 0
        fd = self.child.stdin.fileno()
        try:
            while True:
                with self.lock:
                    if self.stopping:
                        return
                    # Never replace a partially written JSON line. At most that
                    # line and the newest full snapshot are retained in memory.
                    if offset == 0 and self.pending is not None:
                        data, self.pending = self.pending, None
                readable, writable, _ = select.select([self.wake_read], [fd] if data else [], [])
                if readable:
                    os.read(self.wake_read, 65536)
                if writable:
                    try:
                        offset += os.write(fd, data[offset:])
                    except BlockingIOError:
                        continue
                    if offset == len(data):
                        data, offset = b'', 0
        except OSError as error:
            self.enabled = False
            if not self.stopping:
                logging.getLogger(__name__).warning('Progress indicator unavailable: %s', error)
        finally:
            self.child.stdin.close()
            os.close(self.wake_read)

    def close(self):
        if self.child is not None:
            if self.writer is not None:
                with self.lock:
                    self.stopping = True
                self.wake()
                self.writer.join()  # select is woken above; writes are nonblocking.
                os.close(self.wake_write)
            else:
                self.child.stdin.close()
            try:
                self.child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
            self.child = None


def run_ffmpeg(args, progress=None):
    """Read FFmpeg's documented progress stream; preserve its failure behavior."""
    args = [args[0], '-progress', 'pipe:1', '-stats_period', '1'] + args[1:]
    # stderr cannot fill a second pipe while stdout waits for the next update.
    with tempfile.TemporaryFile() as errors:
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=errors, text=True)
        try:
            values = {}
            for line in child.stdout:
                key, separator, value = line.rstrip('\n').partition('=')
                if separator:
                    values[key] = value
                if key == 'progress':
                    if progress is not None:
                        progress.advance(values)
                    values = {}
            code = child.wait()
            if code:
                errors.seek(0, os.SEEK_END)
                errors.seek(max(0, errors.tell() - 3000))
                raise RuntimeError(errors.read().decode(errors='replace') or f'Command exited {code}')
        finally:
            child.stdout.close()
            if child.poll() is None:
                child.kill()
                child.wait()
