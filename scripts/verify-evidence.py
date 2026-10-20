#!/usr/bin/env python3
"""Verify retained Doom evidence; emit receipts only for actual local CPU checks."""
import argparse
import hashlib
import json
import re
import os
from pathlib import Path
import subprocess
import sys

from evidence_contract import emit, recorded, require, verify_release

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = 'dns-doom'
UNOBSERVED = ('live-dns-rerun', 'full-doom-rerun')
REPORT = 'artifacts/dns32-tick/run.json'
ORACLE = 'artifacts/reference-verification/expected-state.json'
PIXELS = 'artifacts/dns32-tick/dns-view.png.indices'
REFERENCE = 'artifacts/reference-verification/reference-view.png.indices'


def check_outputs(report, oracle, pixels, reference):
    require(report.get('backend') == 'DNS / stock bind', 'unsupported DNS backend claim')
    require(oracle.get('backend') == 'LOCAL REFERENCE (not DNS)', 'independent oracle identity missing')
    require(report.get('status') == 'checkpoint-returned', 'incomplete Doom result')
    for key in ('instructions', 'pc', 'registers', 'engine_after', 'pixels_sha256'):
        require(report.get(key) == oracle.get(key), 'independent oracle disagreement: ' + key)
    require(len(report['registers']) == 32 and len(pixels) == 96 * 48, 'incomplete machine/frame evidence')
    require(pixels == reference and hashlib.sha256(pixels).hexdigest() == report['pixels_sha256'], 'independent pixel output disagreement')
    require(all(report.get(key) is True for key in ('reference_pixels_match', 'reference_registers_match', 'reference_game_state_match', 'reference_pc_match')), 'missing required recorded correctness check')


def validate(root, record):
    require(set(record['artifacts']) == {REPORT, ORACLE, PIXELS, REFERENCE}, 'unsupported or missing historical evidence')
    report = json.loads((root / REPORT).read_text())
    oracle = json.loads((root / ORACLE).read_text())
    check_outputs(report, oracle, (root / PIXELS).read_bytes(), (root / REFERENCE).read_bytes())
    claims = dict(instructions=report['instructions'], tick_seconds=round(report['elapsed'], 2), dns_transactions=report['dns_transactions'], viewport_pixels=len((root / PIXELS).read_bytes()))
    require(record['claims'] == claims, 'unsupported claim or claim disagrees with retained result')
    headline = re.search(r'latest run executed ([\d,]+) RISC-V instructions and ([\d,]+) DNS\s+transactions in ([\d.]+) seconds', (root / 'README.md').read_text().replace('**', ''), re.I)
    require(headline is not None and (int(headline[1].replace(',', '')), int(headline[2].replace(',', '')), float(headline[3])) == (claims['instructions'], claims['dns_transactions'], claims['tick_seconds']), 'README headline claim is not supported by recorded results')


def cpu_checks():
    subprocess.run(['bash', 'build-native.sh'], cwd=ROOT, env=dict(os.environ, PYTHON_BIN=sys.executable), check=True)
    subprocess.run([sys.executable, 'scripts/check-cpu.py'], cwd=ROOT, check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--write-release', type=Path)
    actions.add_argument('--verify-release', type=Path)
    args = parser.parse_args()
    if args.write_release:
        emit(ROOT, args.write_release, COMPONENT, UNOBSERVED, validate, cpu_checks)
    elif args.verify_release:
        verify_release(ROOT, args.verify_release, COMPONENT, UNOBSERVED, validate)
    else:
        validate(ROOT, recorded(ROOT))
        print('Retained Doom outputs and claims agree with the independent recorded oracle; live DNS was not rerun.')
