"""Build a release on the production runner, then promote or roll it back."""
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
import urllib.request


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def settings(path):
    return dict(shlex.split(line)[0].split('=', 1) for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#') and '=' in line)


def request(base, token, path='/api/status'):
    req = urllib.request.Request(base + path, headers={
        'Cookie': 'dnsdemo=' + token, 'User-Agent': 'DNSDoom-Deployment/1.0 Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as response:
        data = response.read()
        return json.loads(data) if path == '/api/status' else data


def point(current, target):
    temporary = current.with_name(current.name + '.next')
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target, target_is_directory=True)
    temporary.replace(current)


def main():
    source = Path(__file__).resolve().parent.parent
    home = Path.home()
    config = home / '.config/dnsdoom'
    lock = (config / 'deploy.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX)
    revision = os.environ.get('GITHUB_SHA') or subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Expected a full commit SHA')
    releases = home / 'src/dns-doom-releases'
    releases.mkdir(exist_ok=True)
    release = releases / (revision + '-' + str(time.time_ns()))
    release.mkdir()
    run('rsync', '-a', '--exclude=.git', '--exclude=artifacts', '--exclude=__pycache__',
        '--exclude=*.so', str(source) + '/', str(release) + '/')
    (release / 'REVISION').write_text(revision + '\n')
    base_image = 'dnsdoom-base:' + revision
    image = 'dnsdoom:' + revision
    run('docker', 'build', '--pull', '-t', base_image, '.', cwd=release)
    run('docker', 'build', '-f', 'Dockerfile.rv32', '--build-arg',
        'BASE_IMAGE=' + base_image, '-t', image, '.', cwd=release)
    image_id = subprocess.check_output(['docker', 'image', 'inspect', image,
                                        '--format', '{{.Id}}'], text=True).strip()
    container = ['docker', 'run', '--rm', '--network', 'none', '--shm-size', '4g',
                 '--memory', '6g', '--cpus', '2', '--pids-limit', '256',
                 '--user', f'{os.getuid()}:{os.getgid()}',
                 '--mount', f'type=bind,src={release},dst=/work']
    for script in ('./build-rv32.sh', './build-native.sh'):
        run(*container, '--entrypoint', 'bash', image_id, script)
    run(*container, '--entrypoint', 'python', image_id, '-m', 'dns32.reference',
        'artifacts/rv32/doom-small.elf', '--checkpoint', 'D_DoomLoop',
        '--checkpoint-hit', '2', '--output', 'artifacts/tick-checkpoint')
    run(*container, image_id, 'artifacts/rv32/fibonacci.elf', '--steps', '1000',
        '--client', 'native', '--pipeline', '--optimize', '--packed', '--blocks',
        '--output', 'artifacts/deploy-smoke')

    current = home / 'src/dns-doom'
    env_path = config / 'service.env'
    old_env = env_path.read_text()
    token = settings(env_path).get('DNSDEMO_ACCESS_TOKEN', '')
    # Give any existing computation time to finish before replacing its code.
    deadline = time.monotonic() + 1800
    while request('http://127.0.0.1:8770', token).get('phase') in ('starting', 'running'):
        if time.monotonic() > deadline:
            raise TimeoutError('An active computation prevented deployment')
        time.sleep(5)

    shared = home / '.local/share/dnsdoom/live'
    shared.mkdir(parents=True, exist_ok=True)
    run('systemctl', '--user', 'stop', 'dnsdoom.service')
    old_target = current.resolve()
    promoted = False
    try:
        live = current / 'artifacts/live'
        if live.exists() and live.resolve() != shared.resolve():
            run('rsync', '-a', str(live) + '/', str(shared) + '/')
        (release / 'artifacts/live').symlink_to(shared, target_is_directory=True)
        if not current.is_symlink():
            old_target = releases / ('bootstrap-' + str(time.time_ns()))
            current.rename(old_target)
        point(current, release)
        promoted = True
        lines = [line for line in old_env.splitlines() if not line.startswith('DNSDEMO_IMAGE=')]
        temporary = env_path.with_suffix('.next')
        temporary.write_text('\n'.join(lines) + '\nDNSDEMO_IMAGE=' + image_id + '\n')
        temporary.chmod(0o600)
        temporary.replace(env_path)
        run('systemctl', '--user', 'restart', 'dnsdoom.service')
        for attempt in range(12):
            try:
                request('http://127.0.0.1:8770', token)
                request('https://doom.shitposting.cloud', token)
                request('https://doom.shitposting.cloud', token, '/')
                break
            except Exception:
                if attempt == 11:
                    raise
                time.sleep(5)
    except BaseException:
        if promoted or not current.exists():
            point(current, old_target)
        env_path.write_text(old_env)
        env_path.chmod(0o600)
        run('systemctl', '--user', 'restart', 'dnsdoom.service')
        raise
    (config / 'deployment.json').write_text(json.dumps({
        'revision': revision, 'release': str(release), 'previous': str(old_target),
        'image': image_id, 'deployed_at': time.time()}, indent=2) + '\n')
    print('Deployed', revision, 'to https://doom.shitposting.cloud')


if __name__ == '__main__':
    main()
