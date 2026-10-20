import copy
import json
from pathlib import Path
import tempfile
import unittest

from component_schema import ROOT, validator, verify_provenance
from jsonschema import Draft202012Validator, SchemaError, ValidationError


class CanonicalSchemaTests(unittest.TestCase):
    def test_current_declaration(self):
        validator().validate(json.loads((ROOT / '.fleet/components.json').read_text()))

    def test_negative_path_traversal_and_unsupported_version(self):
        invalid = json.loads((ROOT / '.fleet/fixtures/components-invalid.json').read_text())
        with self.assertRaises(ValidationError):
            validator().validate(invalid)
        actual = json.loads((ROOT / '.fleet/components.json').read_text())
        bad = copy.deepcopy(actual)
        bad['schema_version'] = 2
        with self.assertRaises(ValidationError):
            validator().validate(bad)
        bad = copy.deepcopy(actual)
        bad['components'][0]['profile'] = 'unregistered-profile'
        with self.assertRaises(ValidationError):
            validator().validate(bad)

    def test_malformed_schema_rejected(self):
        schema = json.loads((ROOT / '.fleet/fixtures/schema-invalid.json').read_text())
        with self.assertRaises(SchemaError):
            Draft202012Validator.check_schema(schema)

    def test_tampered_artifact_cannot_keep_pinned_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / '.fleet/tools'
            tools.mkdir(parents=True)
            for name in ['components.schema.json', 'components.schema.provenance.json']:
                (tools / name).write_bytes((ROOT / '.fleet/tools' / name).read_bytes())
            verify_provenance(root)
            (tools / 'components.schema.json').write_text('{}')
            with self.assertRaises(ValueError):
                verify_provenance(root)


if __name__ == '__main__':
    unittest.main()
