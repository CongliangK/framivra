#!/usr/bin/env python3
"""Read-only full JSON Schema validation against the real product schema.

Uses the existing jsonschema package as a test-only dependency. Never renders,
probes media, installs a dependency, or substitutes a partial schema validator.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema', type=Path,
                        default=ROOT.parents[1] / 'vtx/timeline.schema.json')
    parser.add_argument('--timeline', type=Path, default=ROOT / 'timeline.json')
    args = parser.parse_args()
    try:
        from jsonschema import Draft7Validator
    except ImportError:
        print('BLOCKED: full schema check requires an existing jsonschema installation; '
              'no dependency was installed and validation did not pass.', file=sys.stderr)
        return 2

    schema_bytes = args.schema.read_bytes()
    schema = json.loads(schema_bytes)
    timeline = json.loads(args.timeline.read_bytes())
    Draft7Validator.check_schema(schema)
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(timeline),
                    key=lambda e: (str(list(e.absolute_path)), e.message))
    result = {
        'status': 'FAIL' if errors else 'PASS_FULL_PRODUCT_JSON_SCHEMA',
        'schema': str(args.schema.resolve()),
        'schema_sha256': hashlib.sha256(schema_bytes).hexdigest(),
        'timeline_sha256': hashlib.sha256(args.timeline.read_bytes()).hexdigest(),
        'validator': 'jsonschema.Draft7Validator',
        'validator_version': importlib.metadata.version('jsonschema'),
        'errors': [{'path': list(e.absolute_path), 'message': e.message} for e in errors],
        'scope': 'Complete structural schema only; runtime semantic and media checks are UNRUN',
        'native_render': 'UNRUN',
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
