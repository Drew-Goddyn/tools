#!/usr/bin/env python3
"""Install or resume this per-user helper without resetting queue history."""
import fcntl
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from cleaner import save


def service_definition(support, config_path):
    config = json.loads(config_path.read_text())
    python = config.get('python') or shutil.which('python3')
    if not python or not Path(python).is_absolute():
        raise RuntimeError('An absolute Python path is required')
    return {"Label": "local.screen-recording-cleaner",
            "ProgramArguments": [python, str(support / 'cleaner.py'), '--config', str(config_path), 'drain'],
            "WatchPaths": [config['source']], "RunAtLoad": True, "ThrottleInterval": 30,
            "ProcessType": "Background", "LowPriorityIO": True, "Umask": 63,
            "StandardOutPath": str(support / "launchd.log"),
            "StandardErrorPath": str(support / "launchd.log")}


def put_once(path, content):
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f"Refusing to replace changed or unrelated file: {path}")
    else:
        with path.open("xb") as f:
            f.write(content)


def install(user_home=Path.home(), command=subprocess.run):
    os.umask(0o077)
    bundle = Path(__file__).resolve().parent
    support = user_home / "Library/Application Support/Screen Recording Cleaner"
    agent = user_home / "Library/LaunchAgents/local.screen-recording-cleaner.plist"
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11 or newer is required")
    python = Path(sys.executable)
    visible_python = shutil.which("python3")
    if visible_python and Path(visible_python).resolve() == python.resolve():
        python = Path(visible_python)
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg and ffprobe are required; run Install.command to install dependencies")
    source = user_home / "Desktop/screen recordings"
    output = user_home / "Downloads/screen-recordings"
    marker = support / "installation.json"
    identity = "local.screen-recording-cleaner.v1"
    for required in (python, Path(ffmpeg), Path(ffprobe)):
        if not required.exists():
            raise RuntimeError(f"Required path is missing: {required}")
    if support.exists() and not marker.exists() and any(support.iterdir()):
        raise RuntimeError("Existing unrecognized support directory; refusing to alter it")
    if agent.exists() and not marker.exists():
        raise RuntimeError("Existing unrecognized launch agent; refusing to alter it")
    source.mkdir(parents=True, exist_ok=True)
    support.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (support / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = json.loads(marker.read_text()) if marker.exists() else {"installer": identity, "phase": "staging"}
        if journal.get("installer") != identity:
            raise RuntimeError("Installation marker is not recognized")
        if journal["phase"] == "active":
            raise RuntimeError("Already installed; use status or the documented resume command")
        save(marker, journal)
        output.mkdir(parents=True, exist_ok=True)
        put_once(support / "cleaner.py", (bundle / "cleaner.py").read_bytes())
        put_once(support / "README.md", (bundle / "README.md").read_bytes())
        config = {"source": str(source), "output": str(output), "state_dir": str(support / "state"),
                  "python": str(python), "ffmpeg": ffmpeg, "ffprobe": ffprobe,
                  "threads": 4, "crf": 18, "max_bytes": 20000000, "settle_seconds": 30,
                  "notifications": True}
        config_path = support / "config.json"
        put_once(config_path, (json.dumps(config, indent=2) + "\n").encode())
        state_path = support / "state/state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state.get("version") != 1 or "initialized_at" not in state or not isinstance(state.get("files"), dict):
                raise RuntimeError("Queue history is invalid; refusing to reset it")
        else:
            command([str(python), str(support / "cleaner.py"), "--config", str(config_path), "init"], check=True)
        journal["phase"] = "initialized"
        save(marker, journal)
        definition = service_definition(support, config_path)
        agent.parent.mkdir(parents=True, exist_ok=True)
        put_once(agent, plistlib.dumps(definition))
        target = f"gui/{os.getuid()}/local.screen-recording-cleaner"
        registered = command(["/bin/launchctl", "print", target], capture_output=True)
        if registered.returncode:
            command(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent)], check=True)
        journal["phase"] = "registered"
        save(marker, journal)
        command(["/bin/launchctl", "kickstart", target], check=True)
        journal["phase"] = "active"
        journal["trigger"] = "launchd-watchpaths"
        save(marker, journal)
        print(json.dumps({"installed": str(support), "output": str(output), "launch_agent": str(agent)}, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", nargs="?", choices=("setup", "install", "upgrade", "rollback", "confirm", "restore-capture-location"), default="install")
    action = parser.parse_args().action
    if action in ("setup", "restore-capture-location"):
        from setup import setup, restore_capture_location
        {"setup": setup, "restore-capture-location": restore_capture_location}[action]()
    elif action == "install":
        install()
    else:
        from upgrade import upgrade, rollback, confirm
        {"upgrade": upgrade, "rollback": rollback, "confirm": confirm}[action]()
