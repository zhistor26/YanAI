from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy.engine.url import make_url

from services.storage.factory import _mask_password, _normalize_database_url, create_storage_backend


class StorageFactoryTest(unittest.TestCase):
    def test_normalize_database_url_encodes_raw_password_characters(self) -> None:
        url = _normalize_database_url("postgresql://yanai:pa@ss@@192.3.60.166:5432/yanai")

        parsed = make_url(url)
        self.assertEqual(parsed.username, "yanai")
        self.assertEqual(parsed.password, "pa@ss@")
        self.assertEqual(parsed.host, "192.3.60.166")
        self.assertEqual(parsed.database, "yanai")
        self.assertEqual(_mask_password(url), "postgresql://yanai:****@192.3.60.166:5432/yanai")

    def test_normalize_database_url_strips_wrapping_quotes(self) -> None:
        url = _normalize_database_url("'postgresql://yanai:pass@192.3.60.166:5432/yanai'")

        parsed = make_url(url)
        self.assertEqual(parsed.username, "yanai")
        self.assertEqual(parsed.password, "pass")
        self.assertEqual(parsed.host, "192.3.60.166")

    def test_normalize_database_url_keeps_encoded_password_idempotent(self) -> None:
        url = "postgresql://yanai:pa%40ss%40@192.3.60.166:5432/yanai"

        self.assertEqual(_normalize_database_url(url), url)

    def test_normalize_database_url_keeps_at_signs_in_path_and_query(self) -> None:
        url = "postgresql://yanai:pass@192.3.60.166:5432/yanai?application_name=a@b"

        self.assertEqual(_normalize_database_url(url), url)

    def test_normalize_database_url_ignores_sqlite_file_paths(self) -> None:
        url = "sqlite:///tmp/db@local.sqlite3"

        self.assertEqual(_normalize_database_url(url), url)

    def test_create_git_storage_backend_uses_dataset_path_env_vars(self) -> None:
        env = {
            "STORAGE_BACKEND": "git",
            "GIT_REPO_URL": "https://github.com/example/private-data.git",
            "GIT_TOKEN": "token",
            "GIT_BRANCH": "main",
            "GIT_FILE_PATH": "data/accounts.json",
            "GIT_AUTH_KEYS_FILE_PATH": "data/auth_keys.json",
            "GIT_USERS_FILE_PATH": "data/users.json",
            "GIT_SESSIONS_FILE_PATH": "data/sessions.json",
            "GIT_REDEEM_CODES_FILE_PATH": "data/redeem_codes.json",
            "GIT_CHANNELS_FILE_PATH": "data/channels.json",
            "GIT_PROMPT_LIBRARY_FILE_PATH": "data/prompt_library.json",
            "GIT_IMAGE_RECORDS_FILE_PATH": "data/image_records.json",
        }

        with mock.patch.dict("os.environ", env, clear=True), mock.patch(
            "services.storage.factory.GitStorageBackend"
        ) as backend_cls:
            create_storage_backend(Path("data"))

        backend_cls.assert_called_once_with(
            repo_url="https://github.com/example/private-data.git",
            token="token",
            branch="main",
            file_path="data/accounts.json",
            auth_keys_file_path="data/auth_keys.json",
            users_file_path="data/users.json",
            sessions_file_path="data/sessions.json",
            redeem_codes_file_path="data/redeem_codes.json",
            channels_file_path="data/channels.json",
            prompt_library_file_path="data/prompt_library.json",
            image_records_file_path="data/image_records.json",
            local_cache_dir=Path("data") / "git_cache",
        )


if __name__ == "__main__":
    unittest.main()
