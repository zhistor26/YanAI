from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from services import image_service
from services.protocol import conversation
from services.repositories.base import ImageRecordRepository


class InsertOnlyImageRecordRepository(ImageRecordRepository):
    dataset_name = "image_records"
    primary_key = "id"

    def __init__(self) -> None:
        self.inserted: list[dict[str, object]] = []

    def list(self) -> list[dict[str, object]]:
        raise AssertionError("record_image_result should not load all image records")

    def replace_all(self, items: list[dict[str, object]]) -> None:
        raise AssertionError("record_image_result should not replace all image records")

    def upsert(self, item: dict[str, object]) -> None:
        raise AssertionError("record_image_result should insert image records")

    def insert(self, item: dict[str, object]) -> None:
        self.inserted.append(dict(item))

    def delete(self, key: str) -> bool:
        return False

    def count(self) -> int:
        return len(self.inserted)

    def key_set(self) -> set[str]:
        return {str(item.get("id") or "") for item in self.inserted}


class ImageServiceTests(unittest.TestCase):
    def test_list_images_tolerates_legacy_aspect_ratio_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            image_path = images_dir / "2026" / "05" / "25" / "sample.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"image-bytes")

            fake_storage = SimpleNamespace(
                load_image_records=lambda: [
                    {
                        "id": "image-1",
                        "url": "http://127.0.0.1:8000/images/2026/05/25/sample.png",
                        "created_at": "2026-05-25 22:49:30",
                        "size": "4:3",
                    }
                ]
            )
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                cleanup_old_images=lambda: 0,
                get_storage_backend=lambda: fake_storage,
            )

            with mock.patch.object(image_service, "config", fake_config):
                result = image_service.list_images("http://127.0.0.1:8000")

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["size"], len(b"image-bytes"))
        self.assertEqual(result["items"][0]["image_size"], "4:3")

    def test_list_images_returns_paginated_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            records = []
            for index in range(1, 13):
                image_path = images_dir / "2026" / "05" / f"{index:02d}" / "sample.png"
                image_path.parent.mkdir(parents=True)
                image_path.write_bytes(b"image-bytes")
                records.append(
                    {
                        "id": f"image-{index}",
                        "url": f"http://127.0.0.1:8000/images/2026/05/{index:02d}/sample.png",
                        "created_at": f"2026-05-{index:02d} 12:00:00",
                    }
                )
            fake_storage = SimpleNamespace(load_image_records=lambda: records)
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                cleanup_old_images=lambda: 0,
                get_storage_backend=lambda: fake_storage,
            )

            with mock.patch.object(image_service, "config", fake_config):
                result = image_service.list_images("http://127.0.0.1:8000", page=2, page_size=5)

        self.assertEqual(len(result["items"]), 5)
        self.assertEqual(result["pagination"]["total"], 12)
        self.assertEqual(result["pagination"]["page"], 2)
        self.assertEqual(result["pagination"]["page_count"], 3)

    def test_list_images_skips_missing_local_recorded_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            image_path = images_dir / "2026" / "05" / "25" / "keep.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"image-bytes")
            records = [
                {
                    "id": "missing-image",
                    "url": "http://127.0.0.1:8000/images/2026/05/25/missing.png",
                    "created_at": "2026-05-25 13:00:00",
                },
                {
                    "id": "existing-image",
                    "url": "http://127.0.0.1:8000/images/2026/05/25/keep.png",
                    "created_at": "2026-05-25 12:00:00",
                },
            ]
            fake_storage = SimpleNamespace(load_image_records=lambda: records)
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                cleanup_old_images=lambda: 0,
                get_storage_backend=lambda: fake_storage,
            )

            with mock.patch.object(image_service, "config", fake_config):
                result = image_service.list_images("http://127.0.0.1:8000")

        self.assertEqual([item["id"] for item in result["items"]], ["existing-image"])
        self.assertEqual(result["pagination"]["total"], 1)

    def test_list_images_keeps_external_records_without_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            records = [
                {
                    "id": "external-image",
                    "url": "https://example.com/assets/image.png",
                    "created_at": "2026-05-25 12:00:00",
                }
            ]
            fake_storage = SimpleNamespace(load_image_records=lambda: records)
            fake_config = SimpleNamespace(
                images_dir=Path(tmp_dir) / "images",
                cleanup_old_images=lambda: 0,
                get_storage_backend=lambda: fake_storage,
            )

            with mock.patch.object(image_service, "config", fake_config):
                result = image_service.list_images("http://127.0.0.1:8000")

        self.assertEqual([item["id"] for item in result["items"]], ["external-image"])
        self.assertEqual(result["pagination"]["total"], 1)

    def test_delete_images_removes_record_and_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            image_path = images_dir / "2026" / "05" / "25" / "sample.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"image-bytes")
            records = [
                {
                    "id": "image-1",
                    "record_id": "image-1",
                    "url": "http://127.0.0.1:8000/images/2026/05/25/sample.png",
                    "created_at": "2026-05-25 12:00:00",
                },
                {
                    "id": "image-2",
                    "record_id": "image-2",
                    "url": "http://127.0.0.1:8000/images/2026/05/25/keep.png",
                    "created_at": "2026-05-25 13:00:00",
                },
            ]

            def save_image_records(next_records: list[dict[str, object]]) -> None:
                records[:] = next_records

            fake_storage = SimpleNamespace(
                load_image_records=lambda: records,
                save_image_records=save_image_records,
            )
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                get_storage_backend=lambda: fake_storage,
            )

            with mock.patch.object(image_service, "config", fake_config):
                result = image_service.delete_images(record_ids=["image-1"])

        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["removed_records"], 1)
        self.assertEqual(result["removed_files"], 1)
        self.assertFalse(image_path.exists())
        self.assertEqual([record["id"] for record in records], ["image-2"])

    def test_delete_images_removes_file_without_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            image_path = images_dir / "2026" / "05" / "25" / "orphan.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"image-bytes")
            fake_storage = SimpleNamespace(
                load_image_records=lambda: [],
                save_image_records=lambda records: None,
            )
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                get_storage_backend=lambda: fake_storage,
            )

            with mock.patch.object(image_service, "config", fake_config):
                result = image_service.delete_images(
                    urls=["http://127.0.0.1:8000/images/2026/05/25/orphan.png"]
                )

        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["removed_records"], 0)
        self.assertEqual(result["removed_files"], 1)
        self.assertFalse(image_path.exists())

    def test_delete_images_owner_filter_prevents_unowned_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            owned_path = images_dir / "2026" / "05" / "25" / "owned.png"
            other_path = images_dir / "2026" / "05" / "25" / "other.png"
            owned_path.parent.mkdir(parents=True)
            owned_path.write_bytes(b"owned-image")
            other_path.write_bytes(b"other-image")
            records = [
                {
                    "id": "image-owned",
                    "record_id": "image-owned",
                    "owner_user_id": "user-a",
                    "url": "http://127.0.0.1:8000/images/2026/05/25/owned.png",
                    "created_at": "2026-05-25 12:00:00",
                },
                {
                    "id": "image-other",
                    "record_id": "image-other",
                    "owner_user_id": "user-b",
                    "url": "http://127.0.0.1:8000/images/2026/05/25/other.png",
                    "created_at": "2026-05-25 13:00:00",
                },
            ]

            def save_image_records(next_records: list[dict[str, object]]) -> None:
                records[:] = next_records

            fake_storage = SimpleNamespace(
                load_image_records=lambda: records,
                save_image_records=save_image_records,
            )
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                get_storage_backend=lambda: fake_storage,
            )

            with mock.patch.object(image_service, "config", fake_config):
                result = image_service.delete_images(
                    record_ids=["image-other"],
                    urls=["http://127.0.0.1:8000/images/2026/05/25/other.png"],
                    owner_user_id="user-a",
                )

            self.assertEqual(result["removed"], 0)
            self.assertEqual(result["removed_records"], 0)
            self.assertEqual(result["removed_files"], 0)
            self.assertTrue(owned_path.exists())
            self.assertTrue(other_path.exists())
            self.assertEqual([record["id"] for record in records], ["image-owned", "image-other"])

    def test_delete_images_owner_filter_keeps_file_used_by_other_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            shared_path = images_dir / "2026" / "05" / "25" / "shared.png"
            shared_path.parent.mkdir(parents=True)
            shared_path.write_bytes(b"shared-image")
            shared_url = "http://127.0.0.1:8000/images/2026/05/25/shared.png"
            records = [
                {
                    "id": "image-owned",
                    "record_id": "image-owned",
                    "owner_user_id": "user-a",
                    "url": shared_url,
                    "created_at": "2026-05-25 12:00:00",
                },
                {
                    "id": "image-other",
                    "record_id": "image-other",
                    "owner_user_id": "user-b",
                    "url": shared_url,
                    "created_at": "2026-05-25 13:00:00",
                },
            ]

            def save_image_records(next_records: list[dict[str, object]]) -> None:
                records[:] = next_records

            fake_storage = SimpleNamespace(
                load_image_records=lambda: records,
                save_image_records=save_image_records,
            )
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                get_storage_backend=lambda: fake_storage,
            )

            with mock.patch.object(image_service, "config", fake_config):
                result = image_service.delete_images(record_ids=["image-owned"], owner_user_id="user-a")

            self.assertEqual(result["removed"], 1)
            self.assertEqual(result["removed_records"], 1)
            self.assertEqual(result["removed_files"], 0)
            self.assertTrue(shared_path.exists())
            self.assertEqual([record["id"] for record in records], ["image-other"])

    def test_collect_downloadable_images_respects_owner_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            owned_path = images_dir / "2026" / "05" / "25" / "owned.png"
            other_path = images_dir / "2026" / "05" / "25" / "other.png"
            owned_path.parent.mkdir(parents=True)
            owned_path.write_bytes(b"owned-image")
            other_path.write_bytes(b"other-image")
            records = [
                {
                    "id": "image-owned",
                    "record_id": "image-owned",
                    "owner_user_id": "user-a",
                    "url": "http://127.0.0.1:8000/images/2026/05/25/owned.png",
                    "created_at": "2026-05-25 12:00:00",
                },
                {
                    "id": "image-other",
                    "record_id": "image-other",
                    "owner_user_id": "user-b",
                    "url": "http://127.0.0.1:8000/images/2026/05/25/other.png",
                    "created_at": "2026-05-25 13:00:00",
                },
            ]
            fake_storage = SimpleNamespace(load_image_records=lambda: records)
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                get_storage_backend=lambda: fake_storage,
            )

            with mock.patch.object(image_service, "config", fake_config):
                downloads = image_service.collect_downloadable_images(
                    record_ids=["image-owned", "image-other"],
                    owner_user_id="user-a",
                )

        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0]["id"], "image-owned")
        self.assertEqual(downloads[0]["path"], owned_path)

    def test_record_image_result_persists_external_url(self) -> None:
        image_bytes = b"external-image-bytes"
        repository = InsertOnlyImageRecordRepository()
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                base_url="http://127.0.0.1:8000",
                cleanup_old_images=lambda: 0,
                get_repository_provider=lambda: SimpleNamespace(image_records=repository),
            )
            mock_response = mock.Mock()
            mock_response.content = image_bytes
            mock_response.raise_for_status = mock.Mock()
            result = {
                "data": [{"url": "https://cdn.example.com/image.png"}],
            }

            with (
                mock.patch.object(image_service, "config", fake_config),
                mock.patch.object(conversation, "config", fake_config),
                mock.patch.object(conversation, "china_now_text", return_value="2026-05-29 12:00:00"),
                mock.patch("services.image_service.requests.get", return_value=mock_response) as get_mock,
            ):
                created = image_service.record_image_result(
                    {"id": "user-a", "role": "user", "email": "user@example.com"},
                    result,
                    prompt="prompt",
                    mode="generate",
                    model="gpt-image-2",
                )

            get_mock.assert_called_once_with("https://cdn.example.com/image.png", timeout=60)
            self.assertEqual(len(created), 1)
            stored_url = str(created[0]["url"])
            self.assertTrue(stored_url.startswith("/images/"))
            self.assertEqual(result["data"][0]["url"], stored_url)
            self.assertEqual(str(result["data"][0].get("record_id") or ""), str(created[0]["record_id"]))
            self.assertTrue((images_dir / stored_url.removeprefix("/images/")).is_file())

    def test_record_image_result_persists_b64_json(self) -> None:
        import base64

        image_bytes = b"b64-image-bytes"
        repository = InsertOnlyImageRecordRepository()
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                base_url="http://127.0.0.1:8000",
                cleanup_old_images=lambda: 0,
                get_repository_provider=lambda: SimpleNamespace(image_records=repository),
            )
            result = {
                "data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}],
            }

            with (
                mock.patch.object(image_service, "config", fake_config),
                mock.patch.object(conversation, "config", fake_config),
                mock.patch.object(conversation, "china_now_text", return_value="2026-05-29 12:00:00"),
            ):
                created = image_service.record_image_result(
                    {"id": "user-a", "role": "user", "email": "user@example.com"},
                    result,
                    prompt="prompt",
                    mode="generate",
                    model="gpt-image-2",
                )

            self.assertEqual(len(created), 1)
            stored_url = str(created[0]["url"])
            self.assertTrue(stored_url.startswith("/images/"))
            self.assertEqual(result["data"][0]["url"], stored_url)
            self.assertEqual(str(result["data"][0].get("record_id") or ""), str(created[0]["record_id"]))
            self.assertTrue((images_dir / stored_url.removeprefix("/images/")).is_file())

    def test_record_image_result_inserts_without_loading_repository(self) -> None:
        repository = InsertOnlyImageRecordRepository()
        fake_config = SimpleNamespace(
            get_repository_provider=lambda: SimpleNamespace(image_records=repository),
        )
        result = {
            "data": [
                {"url": "http://127.0.0.1:8000/images/2026/05/29/a.png"},
                {"url": "http://127.0.0.1:8000/images/2026/05/29/b.png"},
            ]
        }

        with mock.patch.object(image_service, "config", fake_config):
            created = image_service.record_image_result(
                {"id": "user-a", "role": "user", "email": "user@example.com"},
                result,
                prompt="prompt",
                mode="generate",
                model="gpt-image-2",
                quota_cost=2,
            )

        self.assertEqual(len(created), 2)
        self.assertEqual(len(repository.inserted), 2)
        self.assertTrue(repository.inserted[0]["record_id"])
        self.assertEqual(repository.inserted[0]["owner_user_id"], "user-a")

    def test_save_image_bytes_uses_unique_file_names_for_same_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            images_dir = Path(tmp_dir) / "images"
            fake_config = SimpleNamespace(
                images_dir=images_dir,
                base_url="http://127.0.0.1:8000",
                cleanup_old_images=lambda: 0,
            )

            with (
                mock.patch.object(conversation, "config", fake_config),
                mock.patch.object(conversation, "china_now_text", return_value="2026-05-29 12:00:00"),
            ):
                first = conversation.save_image_bytes(b"same-image")
                second = conversation.save_image_bytes(b"same-image")

            self.assertNotEqual(first, second)
            self.assertEqual(len(list((images_dir / "2026" / "05" / "29").glob("*.png"))), 2)


if __name__ == "__main__":
    unittest.main()
