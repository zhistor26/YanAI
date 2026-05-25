import base64
import json
import unittest

from services.sub2api_service import _build_local_account_item, _build_remote_account_payload


def _jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{encoded}.signature"


class Sub2APIExportTests(unittest.TestCase):
    def test_export_does_not_send_chatgpt_account_id(self) -> None:
        payload = _build_remote_account_payload(
            {"group_id": ""},
            {
                "access_token": _jwt({"exp": 1770000000}),
                "email": "user@example.com",
                "user_id": "user-real",
                "type": "Free",
                "refresh_token": "refresh-token",
                "id_token": "id-token",
                "password": "password-value",
                "created_at": "2026-05-26T00:00:00+00:00",
                "chatgpt_account_id": "should-not-export",
            },
        )

        credentials = payload["credentials"]
        self.assertEqual(credentials["access_token"].count("."), 2)
        self.assertEqual(credentials["email"], "user@example.com")
        self.assertEqual(credentials["user_id"], "user-real")
        self.assertEqual(credentials["refresh_token"], "refresh-token")
        self.assertEqual(credentials["id_token"], "id-token")
        self.assertEqual(credentials["password"], "password-value")
        self.assertEqual(credentials["created_at"], "2026-05-26T00:00:00+00:00")
        self.assertNotIn("chatgpt_account_id", credentials)

    def test_import_preserves_full_oauth_credentials(self) -> None:
        item = _build_local_account_item(
            {"name": "user@example.com"},
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "id_token": "id-token",
                "email": "user@example.com",
                "user_id": "user-real",
                "chatgpt_account_id": "account-real",
                "plan_type": "Plus",
                "expires_at": "2026-06-01T00:00:00Z",
            },
            "access-token",
        )

        self.assertEqual(item["access_token"], "access-token")
        self.assertEqual(item["refresh_token"], "refresh-token")
        self.assertEqual(item["id_token"], "id-token")
        self.assertEqual(item["email"], "user@example.com")
        self.assertEqual(item["user_id"], "user-real")
        self.assertEqual(item["chatgpt_account_id"], "account-real")
        self.assertEqual(item["type"], "Plus")


if __name__ == "__main__":
    unittest.main()
