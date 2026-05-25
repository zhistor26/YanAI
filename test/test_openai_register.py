import unittest
from unittest import mock

from services.register import openai_register


class OpenAIRegisterTests(unittest.TestCase):
    def test_add_registered_account_preserves_oauth_credentials(self) -> None:
        saved_items = []

        class FakeAccountService:
            def add_account_items(self, items):
                saved_items.extend(items)

        result = {
            "email": "user@example.com",
            "password": "password-value",
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "id_token": "id-token",
            "created_at": "2026-05-26T00:00:00+00:00",
        }

        with mock.patch.object(openai_register, "account_service", FakeAccountService()):
            openai_register._add_registered_account(result)

        self.assertEqual(
            saved_items,
            [
                {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "id_token": "id-token",
                    "email": "user@example.com",
                    "password": "password-value",
                    "created_at": "2026-05-26T00:00:00+00:00",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
