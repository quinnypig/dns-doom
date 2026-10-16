"""Validate component declarations with the pinned canonical fleet schema."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REVISION = '977e851fbaacabb250b14ed2bb28c6aff9187e85'
SCHEMA_SHA256 = 'a96529156301c6827b421f960fa126275aec3f430161f72b4546e789073772b4'


def verify_provenance(root=ROOT):
    tools = root / '.fleet/tools'
    record = json.loads((tools / 'components.schema.provenance.json').read_text())
    expected = dict(schema_version=1, package='fleet-component-schema', version='1', source_repository='quinnypig/fleet', source_revision=SOURCE_REVISION, source_path='src/fleet/schemas/components.schema.json', artifact='components.schema.json', sha256=SCHEMA_SHA256)
    if record != expected or hashlib.sha256((tools / 'components.schema.json').read_bytes()).hexdigest() != SCHEMA_SHA256:
        raise ValueError('canonical component schema provenance/digest mismatch')
    return tools / 'components.schema.json'


def validator(root=ROOT):
    from jsonschema import Draft202012Validator, FormatChecker
    schema = json.loads(verify_provenance(root).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-provenance', action='store_true')
    args = parser.parse_args()
    verify_provenance()
    if not args.verify_provenance:
        validator().validate(json.loads((ROOT / '.fleet/components.json').read_text()))
    print('Pinned fleet component schema integrity verified' if args.verify_provenance else 'Components conform to canonical fleet schema version 1')
