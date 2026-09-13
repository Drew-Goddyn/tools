"""Resumable event-trigger upgrade. Rollback restores programs, never queue history."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import time
from cleaner import save
from install import service_definition

FILES = ('cleaner.py', 'README.md', 'recording-listener', 'installation.json')


def atomic_copy(source, target):
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as f:
        temporary = Path(f.name)
        with source.open('rb') as src:
            shutil.copyfileobj(src, f)
        f.flush()
        os.fsync(f.fileno())
    temporary.chmod(source.stat().st_mode & 0o777)
    temporary.replace(target)


def locations(user_home):
    support = user_home / 'Library/Application Support/Screen Recording Cleaner'
    agent = user_home / 'Library/LaunchAgents/local.screen-recording-cleaner.plist'
    return support, agent


def registered(command):
    return command(['/bin/launchctl', 'print', f'gui/{os.getuid()}/local.screen-recording-cleaner'],
                   capture_output=True).returncode == 0


def stop(command):
    if registered(command):
        command(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/local.screen-recording-cleaner'], check=True)


def start(command, agent):
    if not registered(command):
        command(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(agent)], check=True)


def check_installation(support):
    marker = json.loads((support / 'installation.json').read_text())
    if marker.get('installer') != 'local.screen-recording-cleaner.v1':
        raise RuntimeError('Unrecognized installation; refusing to change it')
    state = json.loads((support / 'state/state.json').read_text())
    if state.get('version') != 1 or 'initialized_at' not in state or not isinstance(state.get('files'), dict):
        raise RuntimeError('Queue history is invalid; it will not be reset')
    return marker


def restore(support, agent, journal):
    backup = Path(journal['backup'])
    for name in FILES:
        target = support / name
        prior = backup / name
        if prior.exists():
            atomic_copy(prior, target)
    atomic_copy(backup / 'launch-agent.plist', agent)
    journal['phase'] = 'rolled_back'
    save(support / 'upgrade.json', journal)


def upgrade(user_home=Path.home(), command=subprocess.run):
    os.umask(0o077)
    support, agent = locations(user_home)
    bundle = Path(__file__).resolve().parent
    check_installation(support)
    candidate = {name: hashlib.sha256((bundle / name).read_bytes()).hexdigest()
                 for name in ('cleaner.py', 'README.md', 'install.py')}
    journal_path = support / 'upgrade.json'
    with (support / 'install.lock').open('a') as installation_lock:
        fcntl.flock(installation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = json.loads(journal_path.read_text()) if journal_path.exists() else None
        # A completed installation can receive a later release without rolling it back first.
        if journal and journal['phase'] == 'confirmed' and journal['candidate'] != candidate:
            journal = None
        if journal and journal['phase'] != 'rolled_back':
            if journal['candidate'] != candidate:
                raise RuntimeError('Candidate changed during upgrade; roll back before staging a different build')
            if journal['phase'] == 'confirmed':
                start(command, agent)
                print(json.dumps({'phase': journal['phase'], 'backup': journal['backup']}))
                return False
        else:
            journal = {'version': 1, 'phase': 'preparing', 'candidate': candidate,
                       'backup': str(support / 'backups' / str(time.time_ns()))}
        rollback_needed = journal['phase'] in ('stopped', 'installed')
        try:
            # Blocks only for an active recording, then excludes both old and new workers.
            with (support / 'state/lock').open('a') as worker_lock:
                fcntl.flock(worker_lock, fcntl.LOCK_EX)
                check_installation(support)
                backup = Path(journal['backup'])
                if journal['phase'] == 'preparing':
                    backup.mkdir(parents=True, exist_ok=True)
                    for name in FILES:
                        if (support / name).exists():
                            shutil.copy2(support / name, backup / name)
                    shutil.copy2(agent, backup / 'launch-agent.plist')
                    shutil.copy2(support / 'config.json', backup / 'config.json')
                    shutil.copy2(support / 'state/state.json', backup / 'state-snapshot.json')
                    journal['phase'] = 'prepared'
                    save(journal_path, journal)
                stop(command)
                rollback_needed = True
                journal['phase'] = 'stopped'
                save(journal_path, journal)
                for name in ('cleaner.py', 'README.md'):
                    atomic_copy(bundle / name, support / name)
                (support / 'recording-listener').unlink(missing_ok=True)
                with tempfile.NamedTemporaryFile(dir=agent.parent, delete=False) as f:
                    plistlib.dump(service_definition(support, support / 'config.json'), f)
                    f.flush()
                    os.fsync(f.fileno())
                    temporary = Path(f.name)
                temporary.replace(agent)
                journal['phase'] = 'installed'
                save(journal_path, journal)
            # Do not launch a worker until the migration releases its ledger lock.
            start(command, agent)
            print(json.dumps({'phase': 'installed', 'backup': journal['backup'],
                              'next': 'Verify the installed on-demand job, then confirm; rollback remains available.'}))
            return True
        except Exception as error:
            if rollback_needed:
                with (support / 'state/lock').open('a') as worker_lock:
                    fcntl.flock(worker_lock, fcntl.LOCK_EX)
                    stop(command)
                    restore(support, agent, journal)
                start(command, agent)
            raise RuntimeError(f'Upgrade failed; previous service restored if cutover had begun: {error}') from error


def rollback(user_home=Path.home(), command=subprocess.run):
    support, agent = locations(user_home)
    check_installation(support)
    with (support / 'install.lock').open('a') as installation_lock:
        fcntl.flock(installation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = json.loads((support / 'upgrade.json').read_text())
        if journal['phase'] != 'rolled_back':
            with (support / 'state/lock').open('a') as worker_lock:
                fcntl.flock(worker_lock, fcntl.LOCK_EX)
                stop(command)
                restore(support, agent, journal)
        start(command, agent)
        print(json.dumps({'phase': 'rolled_back', 'queue_history': 'preserved'}))


def confirm(user_home=Path.home()):
    support, _ = locations(user_home)
    with (support / 'install.lock').open('a') as installation_lock:
        fcntl.flock(installation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = json.loads((support / 'upgrade.json').read_text())
        if journal['phase'] not in ('installed', 'confirmed'):
            raise RuntimeError('No installed upgrade to confirm')
        journal['phase'] = 'confirmed'
        save(support / 'upgrade.json', journal)
        marker = json.loads((support / 'installation.json').read_text())
        marker['trigger'] = 'launchd-watchpaths'
        save(support / 'installation.json', marker)
        print(json.dumps({'phase': 'confirmed'}))
