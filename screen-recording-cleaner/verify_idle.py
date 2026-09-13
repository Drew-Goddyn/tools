#!/usr/bin/env python3
"""Manually observe a quiet installed job. This script is never installed or scheduled."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time


def observe(support, label, seconds=300, interval=10):
    target = f'gui/{os.getuid()}/{label}'
    watched = ('state/runner.json', 'state/state.json', 'state/events.log', 'launchd.log')

    def sample():
        result = subprocess.run(['/bin/launchctl', 'print', target], capture_output=True, text=True, check=True)
        fields = {}
        for key in ('pid', 'runs', 'last exit code', 'state'):
            match = re.search(r'^\s*' + re.escape(key) + r' = (.+)$', result.stdout, re.M)
            fields[key] = match.group(1) if match else None
        files = {}
        for name in watched:
            path = support / name
            files[name] = ({'mtime_ns': path.stat().st_mtime_ns,
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} if path.exists() else None)
        return {'service': fields, 'files': files}

    started = time.monotonic()
    first = sample()
    if first['service']['pid'] or first['service']['state'] != 'not running':
        raise RuntimeError('The processor is still running; finish pending work before observing idle')
    if first['service']['last exit code'] != '0':
        raise RuntimeError('The last run did not exit successfully')
    checks = 1
    while time.monotonic() - started < seconds:
        time.sleep(min(interval, max(0, seconds - (time.monotonic() - started))))
        latest = sample()
        checks += 1
        if latest != first:
            raise RuntimeError('The job or its files changed during the quiet window: ' + json.dumps(latest))
    return {'version': 1, 'passed': True, 'seconds': time.monotonic() - started,
            'observations': checks, 'processes_at_each_observation': 0,
            'worker_launches': 0, 'status_log_and_history_writes': 0,
            'snapshot': first,
            'scope': 'The observer is manually invoked, not part of the installed service. '
                     'launchd run count and file hashes also detect work between observations.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--support', type=Path,
        default=Path.home() / 'Library/Application Support/Screen Recording Cleaner')
    parser.add_argument('--label', default='local.screen-recording-cleaner')
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = observe(args.support, args.label, args.seconds)
    except Exception as error:
        args.output.write_text(json.dumps({'passed': False, 'error': str(error)}, indent=2) + '\n')
        raise
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
