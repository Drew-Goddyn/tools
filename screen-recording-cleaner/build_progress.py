"""Build the small native menu UI locally before changing an installed service."""
import hashlib
from pathlib import Path
import platform
import subprocess
import tempfile


def build_progress(bundle):
    source = bundle / 'ProgressMenu.swift'
    version = subprocess.check_output(['/usr/bin/xcrun', 'swiftc', '--version'])
    key = hashlib.sha256(source.read_bytes() + version + platform.machine().encode()).hexdigest()
    build = bundle / '.build'
    build.mkdir(exist_ok=True)
    target = build / ('RecordingProgress-' + key)
    if not target.exists():
        with tempfile.TemporaryDirectory(dir=build) as temp:
            staged = Path(temp) / 'RecordingProgress'
            subprocess.run(['/usr/bin/xcrun', 'swiftc', '-O', '-framework', 'AppKit',
                            '-module-cache-path', str(build / 'modules'), str(source), '-o', str(staged)], check=True)
            staged.replace(target)
    return target


if __name__ == '__main__':
    print(build_progress(Path(__file__).resolve().parent))
