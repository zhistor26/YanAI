from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from services import image_service


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


if __name__ == "__main__":
    unittest.main()
