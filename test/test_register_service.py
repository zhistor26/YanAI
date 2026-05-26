import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import register_service


class RegisterServiceSaveTests(unittest.TestCase):
    def test_atomic_write_preserves_existing_file_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "register.json"
            target.write_text('{"proxy": "old"}\n', encoding="utf-8")

            with mock.patch.object(register_service.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    register_service._atomic_write_text(target, '{"proxy": "new"}\n')

            self.assertEqual(target.read_text(encoding="utf-8"), '{"proxy": "old"}\n')
            self.assertEqual(list(Path(tmp_dir).glob(".register.json.*.tmp")), [])

    def test_register_service_save_uses_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "register.json"
            service = register_service.RegisterService(target)

            with mock.patch.object(register_service.os, "replace", wraps=register_service.os.replace) as replace:
                saved = service.update({"proxy": "http://127.0.0.1:7890", "total": 3, "threads": 2})

            self.assertTrue(replace.called)
            self.assertEqual(Path(replace.call_args.args[1]), target)
            self.assertEqual(saved["proxy"], "http://127.0.0.1:7890")
            self.assertEqual(saved["total"], 3)
            self.assertEqual(saved["threads"], 2)


if __name__ == "__main__":
    unittest.main()
