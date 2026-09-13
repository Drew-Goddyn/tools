"""One-click setup orchestration. Runtime processing stays in the existing worker."""
import json
import os
from pathlib import Path
import re
import subprocess
import time

from cleaner import digest, save
from install import install
from upgrade import check_installation, confirm, locations, rollback, stop, upgrade


def wait_for_worker(support, command, timeout=30):
    deadline = time.monotonic() + timeout
    status_path = support / 'state/runner.json'
    target = f'gui/{os.getuid()}/local.screen-recording-cleaner'
    code = digest(support / 'cleaner.py')
    launched = command(['/bin/launchctl', 'kickstart', '-p', target],
                       check=True, capture_output=True, text=True)
    started_pid = int(launched.stdout.strip())
    while time.monotonic() < deadline:
        registered = command(['/bin/launchctl', 'print', target], capture_output=True, text=True)
        pid = re.search(r'\bpid = (\d+)', registered.stdout or '')
        if registered.returncode == 0 and status_path.exists():
            status = json.loads(status_path.read_text())
            if status.get('code_sha256') == code and status.get('pid') == started_pid:
                if status.get('error'):
                    raise RuntimeError(status['error'])
                if pid and status.get('pid') == int(pid.group(1)) and status.get('state') in ('waiting', 'processing'):
                    return
                if not pid and status.get('state') == 'idle' and re.search(r'last exit code = 0\b', registered.stdout or ''):
                    return
        # This bounded setup check is not installed as a recurring service.
        time.sleep(.25)
    raise RuntimeError('The worker did not start cleanly; inspect launchd.log and any macOS folder permission prompt')


def capture_location(command):
    result = command(['/usr/bin/defaults', 'read', 'com.apple.screencapture', 'location'],
                     capture_output=True, text=True)
    return result.stdout.rstrip('\n') if result.returncode == 0 else None


def configure_capture_location(support, command):
    config = json.loads((support / 'config.json').read_text())
    path = support / 'capture-location.json'
    backup = json.loads(path.read_text()) if path.exists() else {'version': 1, 'previous': capture_location(command)}
    backup['configured'] = config['source']
    save(path, backup)
    command(['/usr/bin/defaults', 'write', 'com.apple.screencapture', 'location', '-string', config['source']], check=True)


def restore_capture_location(user_home=Path.home(), command=subprocess.run):
    support, _ = locations(user_home)
    backup = json.loads((support / 'capture-location.json').read_text())
    if capture_location(command) != backup['configured']:
        print('Screenshot already uses another location; left that preference unchanged.')
        return
    if backup['previous'] is None:
        command(['/usr/bin/defaults', 'delete', 'com.apple.screencapture', 'location'], check=True)
    else:
        command(['/usr/bin/defaults', 'write', 'com.apple.screencapture', 'location', '-string', backup['previous']], check=True)
    print('Restored the Screenshot save location from before setup.')


def setup(user_home=Path.home(), command=subprocess.run):
    support, _ = locations(user_home)
    marker = support / 'installation.json'
    previous = json.loads(marker.read_text()) if marker.exists() else None
    updating = bool(previous and previous.get('phase') == 'active')
    replaced = False
    if updating:
        check_installation(support)
        replaced = upgrade(user_home, command)
    else:
        install(user_home, command)
    try:
        wait_for_worker(support, command)
        configure_capture_location(support, command)
        if updating:
            confirm(user_home)
    except Exception:
        if replaced:
            rollback(user_home, command)
        elif not updating:
            stop(command)
            journal = json.loads(marker.read_text())
            journal['phase'] = 'initialized'
            save(marker, journal)
        raise
