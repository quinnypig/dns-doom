"""Local release receipts bind CPU checks, source, and retained historical bytes."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess

POLICY = 'experiment-evidence-v1'
PASSED = ('recorded-artifact-integrity', 'recorded-claims', 'cpu-correctness')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return 'sha256:' + value.hexdigest()


def local(root, name):
    require(isinstance(name, str) and name and not Path(name).is_absolute() and '\\' not in name, 'invalid artifact path')
    target = (root / name).resolve()
    require(target.is_relative_to(root.resolve()) and target.is_file() and not (root / name).is_symlink(), 'artifact path escapes checkout or is missing')
    return target


def recorded(root):
    record = json.loads((root / '.fleet/recorded-results.json').read_text())
    require(set(record) == {'schema_version', 'historical_source_revision', 'provenance', 'artifacts', 'claims'}, 'malformed recorded evidence')
    require(type(record['schema_version']) is int and record['schema_version'] == 1, 'unsupported recorded evidence version')
    require(record['historical_source_revision'] is None and record['provenance'] == 'historical-run-source-not-recorded', 'historical provenance must remain explicitly unknown')
    require(isinstance(record['artifacts'], dict) and record['artifacts'], 'missing historical artifacts')
    for name, expected in record['artifacts'].items():
        require(digest(local(root, name)) == expected, 'historical artifact checksum mismatch: ' + name)
    return record


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


def clean_source(root):
    require(not git(root, 'status', '--porcelain', '--untracked-files=normal'), 'receipt requires a clean source checkout')
    return git(root, 'rev-parse', 'HEAD')


def input_manifest(root, revision, toolchain):
    files = git(root, 'ls-files', '-z').split('\0')
    return dict(schema_version=1, source_revision=revision, toolchain=toolchain,
                artifacts={name: digest(local(root, name)) for name in files if name})


def check_receipt(receipt, manifest, component, revision, unobserved):
    require(set(receipt) == {'schema_version', 'component_id', 'source_revision', 'artifact', 'toolchain', 'verification', 'deployment'}, 'malformed release evidence')
    require(type(receipt['schema_version']) is int and receipt['schema_version'] == 1, 'unsupported release evidence version')
    require(receipt['component_id'] == component and receipt['source_revision'] == revision, 'release source/component mismatch')
    require(set(manifest) == {'schema_version', 'source_revision', 'toolchain', 'artifacts'} and type(manifest['schema_version']) is int and manifest['schema_version'] == 1, 'unsupported input manifest')
    require(manifest['source_revision'] == revision and manifest['toolchain'] == receipt['toolchain'], 'source/toolchain binding mismatch')
    require(isinstance(receipt['toolchain'], dict) and receipt['toolchain'] and all(isinstance(v, str) and v for v in receipt['toolchain'].values()), 'missing toolchain')
    require(set(receipt['artifact']) == {'uri', 'digest'}, 'malformed artifact binding')
    require(set(receipt['verification']) == {'policy_version', 'checks'}, 'malformed verification binding')
    require(receipt['verification'].get('policy_version') == POLICY, 'unsupported verification policy')
    checks = receipt['verification'].get('checks', [])
    expected = {**dict.fromkeys(PASSED, 'passed'), **dict.fromkeys(unobserved, 'not-observed')}
    require(isinstance(checks, list) and len(checks) == len(expected), 'missing or duplicate required checks')
    require(all(set(check) == {'id', 'result'} for check in checks), 'malformed verification check')
    require({check['id']: check['result'] for check in checks} == expected, 'missing required checks or unsupported result claim')
    require(receipt['deployment'] == dict(desired_revision=revision, observed_revision=None, verified_at=None, result='not-observed'), 'CPU checks cannot establish deployment')


def emit(root, output, component, unobserved, validate, cpu_checks):
    revision = clean_source(root)
    validate(root, recorded(root))
    cpu_checks()
    toolchain = {'python': platform.python_version(), 'platform': platform.machine()}
    compiler = subprocess.run(['c++', '--version'], capture_output=True, text=True) if shutil.which('c++') else None
    if compiler and compiler.returncode == 0:
        toolchain['c++'] = compiler.stdout.splitlines()[0]
    manifest = input_manifest(root, revision, toolchain)
    output.mkdir(parents=True, exist_ok=True)
    inputs = output / 'inputs.json'
    inputs.write_text(json.dumps(manifest, indent=2) + '\n')
    receipt = dict(schema_version=1, component_id=component, source_revision=revision,
                   artifact=dict(uri='local:inputs.json', digest=digest(inputs)), toolchain=toolchain,
                   verification=dict(policy_version=POLICY, checks=[dict(id=id, result='passed') for id in PASSED] + [dict(id=id, result='not-observed') for id in unobserved]),
                   deployment=dict(desired_revision=revision, observed_revision=None, verified_at=None, result='not-observed'))
    check_receipt(receipt, manifest, component, revision, unobserved)
    (output / 'release.json').write_text(json.dumps(receipt, indent=2) + '\n')
    verify_release(root, output / 'release.json', component, unobserved, validate)
    print(json.dumps(dict(source_revision=revision, cpu_checks='passed', historical_run_source=None, runtime_checks=dict.fromkeys(unobserved, 'not-observed'))))


def verify_release(root, file, component, unobserved, validate):
    receipt = json.loads(file.read_text())
    require(receipt.get('artifact', {}).get('uri') == 'local:inputs.json', 'unsupported artifact URI')
    inputs = file.parent / 'inputs.json'
    require(digest(inputs) == receipt['artifact'].get('digest'), 'input manifest checksum mismatch')
    manifest = json.loads(inputs.read_text())
    revision = clean_source(root)
    check_receipt(receipt, manifest, component, revision, unobserved)
    require(manifest == input_manifest(root, revision, receipt['toolchain']), 'bound source/artifact inputs changed')
    validate(root, recorded(root))
    return receipt
