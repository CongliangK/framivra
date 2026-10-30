"""Public raw-write parser: one existing parser, not a second JSON authority."""
import json
import unittest
from unittest.mock import patch

from vtx import api
from vtx.timeline_structure import strict_loads  # core implementation equivalence only


class WriteJsonAPI(unittest.TestCase):
    def test_public_export_and_exact_delegation(self):
        self.assertIn("parse_write_json", api.__all__)
        payload = b'[1]'
        sentinel = object()
        with patch.object(api, "_strict_loads", return_value=sentinel) as parser:
            self.assertIs(api.parse_write_json(payload), sentinel)
            parser.assert_called_once_with(payload)
        error = ValueError("original parser error")
        with patch.object(api, "_strict_loads", side_effect=error):
            with self.assertRaises(ValueError) as caught:
                api.parse_write_json(payload)
            self.assertIs(caught.exception, error)

    def test_json_types_unicode_and_unknown_nested_values(self):
        values = [None, True, False, 9007199254740993, 0.1, "原文 😀", [],
                  [False, 1], {"unknown": {"typed": [False, 1, "你好"]}}]
        for value in values:
            with self.subTest(value=value):
                raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
                actual = api.parse_write_json(raw)
                self.assertIs(type(actual), type(value))
                self.assertEqual(actual, value)
                self.assertEqual(actual, strict_loads(raw))

    def test_existing_safe_numeric_contract(self):
        for raw in (b'0.1', b'0.5', b'1e-3', b'5e-1', b'1e20', b'-0.0',
                    b'9007199254740993', b'0e123'):
            with self.subTest(raw=raw):
                self.assertEqual(api.parse_write_json(raw), strict_loads(raw))

    def test_same_original_rejections_without_parser_loosening(self):
        cases = [b'{', b'"\xff"', b'{"x":1,"x":2}', b'NaN', b'Infinity',
                 b'-Infinity', b'1e999', b'1e-999',
                 b'[{"unknown":{"x":0.12345678901234567890123456789}}]',
                 b'0e99999999999999999999999999999999999999']
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError) as original:
                    strict_loads(raw)
                with self.assertRaises(type(original.exception)) as public:
                    api.parse_write_json(raw)
                self.assertEqual(str(public.exception), str(original.exception))

    def test_nesting_exception_is_not_reinterpreted(self):
        raw = b'[' * 100000 + b'0' + b']' * 100000
        with self.assertRaises(RecursionError):
            strict_loads(raw)
        with self.assertRaises(RecursionError):
            api.parse_write_json(raw)


if __name__ == "__main__":
    unittest.main()
