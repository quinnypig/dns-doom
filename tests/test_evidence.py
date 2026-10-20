import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from evidence_contract import check_receipt, digest, recorded

spec = importlib.util.spec_from_file_location('experiment_verifier', ROOT / 'scripts/verify-evidence.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class EvidenceContractTests(unittest.TestCase):
    def receipt(self):
        manifest = dict(schema_version=1, source_revision='fixture-source', toolchain={'python': 'fixture'}, artifacts={})
        checks = [dict(id=id, result='passed') for id in ('recorded-artifact-integrity', 'recorded-claims', 'cpu-correctness')]
        checks.extend(dict(id=id, result='not-observed') for id in verifier.UNOBSERVED)
        release = dict(schema_version=1, component_id=verifier.COMPONENT, source_revision='fixture-source', artifact=dict(uri='local:inputs.json', digest='sha256:' + 'a' * 64), toolchain=manifest['toolchain'], verification=dict(policy_version='experiment-evidence-v1', checks=checks), deployment=dict(desired_revision='fixture-source', observed_revision=None, verified_at=None, result='not-observed'))
        return release, manifest

    def check(self, receipt, manifest):
        check_receipt(receipt, manifest, verifier.COMPONENT, 'fixture-source', verifier.UNOBSERVED)

    def test_unknown_version_and_missing_required_checks_rejected(self):
        receipt, manifest = self.receipt()
        self.check(receipt, manifest)
        bad = copy.deepcopy(receipt)
        bad['schema_version'] = 2
        with self.assertRaises(ValueError):
            self.check(bad, manifest)
        bad = copy.deepcopy(receipt)
        bad['verification']['checks'].pop(0)
        with self.assertRaisesRegex(ValueError, 'required checks'):
            self.check(bad, manifest)

    def test_cpu_success_cannot_promote_runtime_or_deployment(self):
        receipt, manifest = self.receipt()
        receipt['verification']['checks'][-1]['result'] = 'passed'
        with self.assertRaisesRegex(ValueError, 'unsupported result'):
            self.check(receipt, manifest)
        receipt, manifest = self.receipt()
        receipt['deployment']['result'] = 'verified'
        with self.assertRaisesRegex(ValueError, 'deployment'):
            self.check(receipt, manifest)

    def test_wrong_source_and_toolchain_rejected(self):
        receipt, manifest = self.receipt()
        manifest['source_revision'] = 'different'
        with self.assertRaisesRegex(ValueError, 'binding'):
            self.check(receipt, manifest)
        receipt, manifest = self.receipt()
        manifest['toolchain'] = {'python': 'different'}
        with self.assertRaisesRegex(ValueError, 'binding'):
            self.check(receipt, manifest)

    def test_corrupted_and_missing_retained_artifact_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.fleet').mkdir()
            artifact = root / 'artifact.bin'
            artifact.write_bytes(b'correct')
            record = dict(schema_version=1, historical_source_revision=None, provenance='historical-run-source-not-recorded', artifacts={'artifact.bin': digest(artifact)}, claims={})
            (root / '.fleet/recorded-results.json').write_text(json.dumps(record))
            recorded(root)
            artifact.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                recorded(root)
            artifact.unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                recorded(root)

    def test_claims_require_exact_supported_fields_and_retained_values(self):
        record = recorded(ROOT)
        record['claims']['unsupported-performance'] = 999
        with self.assertRaisesRegex(ValueError, 'unsupported claim'):
            verifier.validate(ROOT, record)
        record = recorded(ROOT)
        record['artifacts'].pop(next(iter(record['artifacts'])))
        with self.assertRaisesRegex(ValueError, 'missing historical'):
            verifier.validate(ROOT, record)


class DoomOracleTests(unittest.TestCase):
    def test_wrong_register_and_pixel_outputs_rejected_independently(self):
        report = json.loads((ROOT / verifier.REPORT).read_text())
        oracle = json.loads((ROOT / verifier.ORACLE).read_text())
        pixels = (ROOT / verifier.PIXELS).read_bytes()
        reference = (ROOT / verifier.REFERENCE).read_bytes()
        bad = copy.deepcopy(report)
        bad['registers']['r01'] = 'ffffffff'
        with self.assertRaisesRegex(ValueError, 'oracle disagreement'):
            verifier.check_outputs(bad, oracle, pixels, reference)
        bad_pixels = bytes([pixels[0] ^ 1]) + pixels[1:]
        with self.assertRaisesRegex(ValueError, 'pixel output'):
            verifier.check_outputs(report, oracle, bad_pixels, reference)
        report['reference_pc_match'] = False
        with self.assertRaisesRegex(ValueError, 'required recorded correctness'):
            verifier.check_outputs(report, oracle, pixels, reference)
