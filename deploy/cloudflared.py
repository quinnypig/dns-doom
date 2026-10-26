#!/usr/bin/env python3
"""Install the cloudflared release pinned in cloudflared.version and restart its tunnel.

Usage: cloudflared.py BINARY UNIT HEALTH_URL

Renovate bumps cloudflared.version. The binary is verified against the SHA256
list Cloudflare publishes in the release notes, then swapped in atomically.
If the tunnel does not re-register or the public URL stops answering, the
previous binary is restored and the tunnel restarted again.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPOSITORY = 'cloudflare/cloudflared'
ASSETS = {'aarch64': 'cloudflared-linux-arm64', 'x86_64': 'cloudflared-linux-amd64'}


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'cloudflared-pin'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def installed_version(binary):
    if not binary.exists():
        return ''
    output = subprocess.run([str(binary), '--version'], capture_output=True, text=True).stdout
    match = re.search(r'version (\S+)', output)
    return match.group(1) if match else ''


def restart(unit, health_url):
    started = time.time()
    subprocess.run(['systemctl', '--user', 'restart', unit], check=True)
    for attempt in range(24):
        time.sleep(5)
        log = subprocess.run(['journalctl', '--user', '-u', unit, '--since', f'@{int(started)}',
                              '--no-pager', '-o', 'cat'], capture_output=True, text=True).stdout
        if 'Registered tunnel connection' not in log:
            continue
        try:
            fetch(health_url)
            return
        except Exception:
            pass
    raise RuntimeError(f'{unit} did not come back healthy')


def main():
    binary, unit, health_url = Path(sys.argv[1]).expanduser(), sys.argv[2], sys.argv[3]
    version = Path(__file__).with_name('cloudflared.version').read_text().strip()
    if not re.fullmatch(r'\d{4}\.\d{1,2}\.\d+', version):
        raise ValueError('Unexpected cloudflared version: ' + version)
    if installed_version(binary) == version:
        print('cloudflared', version, 'already installed at', binary)
        return

    asset = ASSETS[os.uname().machine]
    notes = json.loads(fetch(f'https://api.github.com/repos/{REPOSITORY}/releases/tags/{version}'))['body']
    expected = re.search(rf'^{re.escape(asset)}: ([0-9a-f]{{64}})\s*$', notes, re.M)
    if not expected:
        raise ValueError(f'No published checksum for {asset} {version}')
    data = fetch(f'https://github.com/{REPOSITORY}/releases/download/{version}/{asset}')
    if hashlib.sha256(data).hexdigest() != expected.group(1):
        raise ValueError(f'Checksum mismatch for {asset} {version}')

    staged = binary.with_name(binary.name + '.next')
    staged.write_bytes(data)
    staged.chmod(0o755)
    if installed_version(staged) != version:
        raise ValueError(f'Downloaded binary does not report version {version}')
    previous = binary.with_name(binary.name + '.prev')
    if binary.exists():
        shutil.copy2(binary, previous)
    os.replace(staged, binary)
    try:
        restart(unit, health_url)
    except BaseException:
        if previous.exists():
            os.replace(previous, binary)
            subprocess.run(['systemctl', '--user', 'restart', unit])
        raise
    print('Installed cloudflared', version, 'at', binary)


if __name__ == '__main__':
    main()
