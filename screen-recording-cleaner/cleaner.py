#!/usr/bin/env python3
"""Local screen-recording queue. Originals are read-only; publication is exclusive."""
import argparse
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile
import time


def signature(path):
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]


def save(path, data):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
        tmp = f.name
    os.replace(tmp, path)


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def run(args, timeout=None):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:] or f"Command exited {result.returncode}")
    return result.stdout


def videos(folder):
    # Deliberately nonrecursive. Ignore screenshots, temporary files and symlinks.
    return sorted(p for p in folder.iterdir() if not p.is_symlink() and p.is_file()
                  and not p.name.startswith(".") and p.suffix.lower() in (".mov", ".mp4"))


def snapshot(folder):
    """Catch arrivals or changes during a scan; the final exit race is documented."""
    found = {}
    for path in videos(folder):
        try:
            found[str(path)] = signature(path)
        except FileNotFoundError:
            pass
    return found


class Cleaner:
    def __init__(self, config):
        self.c = config
        self.source = Path(config["source"])
        self.output = Path(config["output"])
        self.state_dir = Path(config["state_dir"])
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "state.json"
        self.lock = (self.state_dir / "lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.log = logging.getLogger(str(self.state_dir))
        self.log.setLevel(logging.INFO)
        if not self.log.handlers:
            handler = RotatingFileHandler(self.state_dir / "events.log", maxBytes=1000000,
                                          backupCount=2)
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            self.log.addHandler(handler)
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else None

    def write(self):
        save(self.state_path, self.state)

    def notify(self, message):
        if not self.c.get("notifications", True):
            return
        # Standard Additions notification only; does not control any application.
        script = 'on run argv\ndisplay notification (item 1 of argv) with title "Screen recordings"\nend run'
        try:
            subprocess.run(["/usr/bin/osascript", "-e", script, message], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as error:
            self.log.warning("Notification unavailable: %s", error)

    def initialize(self):
        if self.state is not None:
            raise RuntimeError("Already initialized; refusing to reset recording history")
        self.output.mkdir(parents=True, exist_ok=True)
        entries = {str(p): {"signature": signature(p), "status": "existing"}
                   for p in videos(self.source)}
        self.state = {"version": 1, "initialized_at": time.time(), "files": entries}
        self.write()
        self.log.info("Initialized; left %d existing recordings alone", len(entries))

    def probe(self, path, count=False):
        args = [self.c["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json"]
        if count:
            args += ["-count_frames"]
        info = json.loads(run(args + [str(path)]))
        return info

    def ffmpeg(self, source, args):
        return [self.c["ffmpeg"], "-hide_banner", "-loglevel", "error", "-xerror",
                "-nostdin", "-y", "-threads", str(self.c["threads"]), "-i", str(source)] + args

    def encode(self, source, target, info):
        audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
        args = ["-map", "0:v:0", "-c:v", "libx264", "-preset", "slow", "-threads",
                str(self.c["threads"]), "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
                "-enc_time_base", "demux"]
        args += ["-crf", str(self.c["crf"]), "-map", "0:a?"]
        if all(s["codec_name"] in ("aac", "alac") for s in audio):
            args += ["-c:a", "copy"]
        else:
            args += ["-c:a", "aac", "-b:a", "192k"]
        args += ["-movflags", "+faststart", str(target)]
        run(self.ffmpeg(source, args))

    def validate(self, source_info, target):
        info = self.probe(target, count=True)
        source_v = next(s for s in source_info["streams"] if s["codec_type"] == "video")
        target_v = next(s for s in info["streams"] if s["codec_type"] == "video")
        for field in ("width", "height", "nb_read_frames"):
            if source_v.get(field) != target_v.get(field):
                raise RuntimeError(f"Video verification failed: {field} differs")
        if abs(float(source_info["format"]["duration"]) - float(info["format"]["duration"])) > 0.15:
            raise RuntimeError("Video verification failed: duration changed")
        original_audio = [s for s in source_info["streams"] if s["codec_type"] == "audio"]
        output_audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
        if len(original_audio) != len(output_audio):
            raise RuntimeError("Audio verification failed: track count changed")
        for a, b in zip(original_audio, output_audio):
            if a.get("channels") != b.get("channels"):
                raise RuntimeError("Audio verification failed: channel count changed")
            if "duration" in a and "duration" in b and abs(float(a["duration"]) - float(b["duration"])) > 0.15:
                raise RuntimeError("Audio verification failed: track duration changed")
        run(self.ffmpeg(target, ["-map", "0:v", "-map", "0:a?", "-f", "null", os.devnull]))
        return info

    def process(self, source, entry):
        before = signature(source)
        self.output.mkdir(parents=True, exist_ok=True)
        info = self.probe(source, count=True)
        vid = [s for s in info["streams"] if s["codec_type"] == "video"]
        if len(vid) != 1 or float(info["format"].get("duration", 0)) <= 0:
            raise RuntimeError("Expected one complete video track")
        # Do not silently flatten HDR, rotation, or higher-bit-depth recordings.
        supported = (vid[0].get("pix_fmt") == "yuv420p"
                     and vid[0].get("color_transfer") not in ("smpte2084", "arib-std-b67")
                     and not any(s.get("rotation", 0) for s in vid[0].get("side_data_list", [])))
        if entry.get("workdir"):
            previous = Path(entry["workdir"])
            if previous.parent == self.output and previous.name.startswith(".processing-") and not previous.is_symlink():
                if previous.exists():
                    shutil.rmtree(previous)
        with tempfile.TemporaryDirectory(prefix=".processing-", dir=self.output) as temp:
            work = Path(temp)
            entry["workdir"] = str(work)
            self.write()
            selected = work / ("recording" + source.suffix)
            decision = "already_small" if before[2] < self.c["max_bytes"] else "original_format_preserved"
            if before[2] < self.c["max_bytes"] or not supported:
                shutil.copyfile(source, selected)
                if digest(source) != digest(selected):
                    raise RuntimeError("Original copy verification failed")
                self.validate(info, selected)
            else:
                selected = work / "quality.mp4"
                self.encode(source, selected, info)
                self.validate(info, selected)
                decision = "quality_encode"
                if selected.stat().st_size >= before[2]:
                    selected = work / ("original" + source.suffix)
                    shutil.copyfile(source, selected)
                    if digest(source) != digest(selected):
                        raise RuntimeError("Original copy verification failed")
                    decision = "original_smaller"
            if signature(source) != before:
                raise RuntimeError("Recording changed during processing; waiting for the completed file")
            checksum = digest(selected)
            target = self.output / (source.stem + " - clean" + selected.suffix)
            if entry.get("output") and Path(entry["output"]).exists() and entry.get("sha256") == digest(Path(entry["output"])):
                target = Path(entry["output"])
            else:
                suffix = hashlib.sha256(json.dumps(before).encode()).hexdigest()[:10]
                counter = 0
                while target.exists():
                    counter += 1
                    target = self.output / f"{source.stem} - clean-{suffix}-{counter}{selected.suffix}"
                # Save publication intent first so a crash after linking can recover.
                entry.update(output=str(target), sha256=checksum)
                self.write()
                os.link(selected, target)  # Atomic, same filesystem, never replaces another file.
            entry.update(status="done", output=str(target), bytes=target.stat().st_size,
                         decision=decision, similarity=None, completed_at=time.time(),
                         under_limit=target.stat().st_size < self.c["max_bytes"])
            entry.pop("error", None)
            self.write()
            self.log.info("Completed %s: %s bytes, %s", source.name, entry["bytes"], decision)
            if not entry["under_limit"]:
                self.notify(f"{source.name}: ready in Downloads/screen-recordings. Kept over 20 MB to preserve picture quality.")

    def scan(self):
        if self.state is None:
            raise RuntimeError("Not initialized; refusing to process the existing archive")
        self.state["last_scan"] = time.time()
        next_checks = {}
        try:
            paths = videos(self.source)
            self.state.pop("scan_error", None)
        except OSError as error:
            self.state["scan_error"] = str(error)
            self.write()
            raise
        baseline = {tuple(e["signature"][:2]) for e in self.state["files"].values()
                    if e["status"] == "existing"}
        for path in paths:
            now = time.time()
            try:
                sig = signature(path)
            except FileNotFoundError:
                continue
            entry = self.state["files"].get(str(path))
            if tuple(sig[:2]) in baseline:
                if entry is None:
                    self.state["files"][str(path)] = {"signature": sig, "status": "existing"}
                continue
            previous = next((e for e in self.state["files"].values()
                             if e["status"] == "done" and e["signature"] == sig), None)
            if previous is not None:
                self.state["files"][str(path)] = dict(previous)
                continue
            if entry is None or entry["signature"] != sig:
                old_workdir = entry.get("workdir") if entry else None
                entry = {"signature": sig, "status": "waiting", "stable_since": now, "attempts": 0}
                if old_workdir:
                    entry["workdir"] = old_workdir
                self.state["files"][str(path)] = entry
                self.write()
                if sig[2]:
                    next_checks[str(path)] = now + self.c["settle_seconds"]
                continue
            if entry["status"] in ("existing", "done") or sig[2] == 0:
                continue
            if now - entry["stable_since"] < self.c["settle_seconds"] or now < entry.get("retry_after", 0):
                next_checks[str(path)] = max(entry["stable_since"] + self.c["settle_seconds"],
                                             entry.get("retry_after", 0))
                continue
            # A paused writer can have a stable size. Do not encode until it closes.
            opened = subprocess.run(["/usr/sbin/lsof", "-F", "a", "--", str(path)],
                                    capture_output=True, text=True, timeout=15)
            if opened.returncode not in (0, 1):
                raise RuntimeError("Could not check whether the recording is still open")
            if any(line in ("aw", "au") for line in opened.stdout.splitlines()):
                next_checks[str(path)] = now + max(1, self.c["settle_seconds"])
                continue
            entry["status"] = "processing"
            self.write()
            try:
                self.process(path, entry)
            except Exception as error:
                entry["attempts"] += 1
                entry.update(status="retry", error=str(error),
                             retry_after=time.time() + min(3600, 60 * 3 ** min(entry["attempts"], 4)))
                self.log.error("Could not finish %s: %s", path.name, error)
                self.write()
                if entry["attempts"] == 1:
                    self.notify(f"Could not finish {path.name}. The original is safe; the cleaner will retry.")
                next_checks[str(path)] = entry["retry_after"]
        self.state["last_scan_completed"] = time.time()
        self.write()
        pending = []
        for path in paths:
            entry = self.state["files"].get(str(path))
            if entry is None or entry["status"] in ("done", "existing"):
                continue
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            pending.append({"path": str(path), "state": entry["status"],
                            "next_check_at": next_checks.get(str(path)) if size else None})
        return {"version": 1, "pending": pending, "error": None}


def drain(config):
    """Run on demand, waiting only while known recordings or retries remain."""
    state_dir = Path(config['state_dir'])
    status_path = state_dir / 'runner.json'
    previous = json.loads(status_path.read_text()) if status_path.exists() else {}
    status = {'version': 1, 'pid': os.getpid(), 'started_at': time.time(),
              'code_sha256': digest(Path(__file__)),
              'launches': previous.get('launches', 0) + 1,
              'scans': previous.get('scans', 0), 'waits': previous.get('waits', 0)}

    def transition(state, next_check=None, error=None):
        status.update(state=state, next_check_at=next_check, error=error, updated_at=time.time())
        save(status_path, status)

    transition('processing')
    watcher = select.kqueue()
    folder_fd = None
    try:
        folder_fd = os.open(config['source'], os.O_EVTONLY)
        watcher.control([select.kevent(folder_fd, filter=select.KQ_FILTER_VNODE,
            flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
            fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE)], 0, 0)
        while True:
            # The ledger lock covers a scan/encode, not a retry sleep. Installation
            # can therefore wait for encoding, then stop this job safely.
            cleaner = Cleaner(config)
            try:
                before = snapshot(cleaner.source)
                status['scans'] += 1
                result = cleaner.scan()
                if cleaner.state.pop('service_error', None) is not None:
                    cleaner.write()
                after = snapshot(cleaner.source)
            except Exception as error:
                if cleaner.state is not None:
                    if cleaner.state.get('service_error') != str(error):
                        cleaner.log.error('Service needs attention: %s', error)
                        cleaner.notify('The recording cleaner needs attention. Originals are safe. Check its status and run it again.')
                    cleaner.state['service_error'] = str(error)
                    cleaner.write()
                raise
            finally:
                cleaner.lock.close()
            if before != after:
                continue
            pending = [item for item in result['pending'] if item['path'] in after]
            if not pending:
                transition('idle')
                return
            # Empty files are known unfinished work too. Once deleted, or once
            # their recording completes, the next pass exits without another timer.
            deadline = min(item['next_check_at'] or time.time() + max(1, config['settle_seconds'])
                           for item in pending)
            status['waits'] += 1
            transition('waiting', deadline)
            # Only while work remains: a new arrival wakes a pending retry early.
            watcher.control(None, 1, max(0, deadline - time.time()))
            transition('processing')
    except Exception as error:
        transition('error', error=str(error))
        raise
    finally:
        watcher.close()
        if folder_fd is not None:
            os.close(folder_fd)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--supervised", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("action", choices=("init", "scan", "drain", "status"))
    args = parser.parse_args()
    if args.supervised and args.action == "scan" and os.getpgrp() != os.getpid():
        # The listener can stop this worker and its encoders as one owned group.
        os.setpgid(0, 0)
    config = json.loads(args.config.read_text())
    if args.action == "status":
        state = json.loads((Path(config["state_dir"]) / "state.json").read_text())
        runner = Path(config["state_dir"]) / "runner.json"
        if runner.exists():
            state["runner"] = json.loads(runner.read_text())
        print(json.dumps(state, indent=2, ensure_ascii=False))
        return
    if args.action == 'drain':
        drain(config)
        return
    try:
        cleaner = Cleaner(config)
    except BlockingIOError:
        if args.action == "scan":
            print(json.dumps({"version": 1, "pending": [], "error": "Worker lock is busy"}))
            raise SystemExit(75)
        return
    if args.action == "init":
        cleaner.initialize()
    elif args.action == "scan":
        try:
            result = cleaner.scan()
            if cleaner.state.pop("service_error", None) is not None:
                cleaner.write()
            print(json.dumps(result, ensure_ascii=False))
        except Exception as error:
            if cleaner.state is not None:
                if cleaner.state.get("service_error") != str(error):
                    cleaner.log.error("Service needs attention: %s", error)
                    cleaner.notify("The recording cleaner needs attention. Originals are safe. Check its status before relying on automatic copies.")
                cleaner.state["service_error"] = str(error)
                cleaner.write()
            print(json.dumps({"version": 1, "pending": [], "error": str(error)}))
            raise SystemExit(1)


if __name__ == "__main__":
    main()
