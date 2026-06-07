from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.auth_service import AuthService
from services.storage.json_storage import JSONStorageBackend


class UserImageChannelTests(unittest.TestCase):
    def test_blank_api_key_preserves_existing_personal_channel_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = JSONStorageBackend(Path(tmp_dir) / "accounts.json")
            service = AuthService(storage)
            user, _ = service.create_user(
                email="user@example.com",
                password="secret123",
                quota=3,
            )

            saved = service.save_user_image_channel_config(
                str(user["id"]),
                {
                    "enabled": True,
                    "name": "Mine",
                    "base_url": "https://old.example",
                    "api_key": "sk-old",
                    "models": ["gpt-image-2"],
                    "timeout": 30,
                },
            )
            updated = service.save_user_image_channel_config(
                str(user["id"]),
                {
                    "enabled": True,
                    "name": "Mine",
                    "base_url": "https://new.example",
                    "api_key": "",
                    "models": ["gpt-image-2", "gpt-5-5"],
                    "timeout": 45,
                },
            )
            raw = service.get_user_image_channel_config(str(user["id"]), include_api_key=True)
            public_user = service.get_user(str(user["id"]))

            self.assertTrue(saved["has_api_key"])
            self.assertTrue(updated["has_api_key"])
            self.assertNotIn("api_key", updated)
            self.assertEqual(raw["api_key"], "sk-old")
            self.assertEqual(raw["base_url"], "https://new.example")
            self.assertEqual(raw["models"], ["gpt-image-2", "gpt-5-5"])
            self.assertEqual(raw["timeout"], 45)
            self.assertIsNotNone(public_user)
            self.assertTrue(public_user["image_channel"]["has_api_key"])  # type: ignore[index]
            self.assertNotIn("api_key", public_user["image_channel"])  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
