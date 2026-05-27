import json
import tempfile
import unittest
from pathlib import Path

from services.prompt_service import PromptLibraryService
from services.storage.json_storage import JSONStorageBackend


class PromptLibraryServiceTests(unittest.TestCase):
    def test_bootstrap_create_update_delete_and_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            seed_path = root / "seed.json"
            seed_path.write_text(
                json.dumps(
                    {
                        "prompts": [
                            {
                                "title": "旧提示词",
                                "prompt": "生成一张海报",
                                "mode": "generate",
                                "category": "工作",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            storage = JSONStorageBackend(root / "accounts.json")
            service = PromptLibraryService(storage, bootstrap_paths=(seed_path,), assets_dir=root / "assets")

            self.assertEqual(len(service.list_prompts()), 1)

            created = service.create_prompt(
                {
                    "title": "新提示词",
                    "prompt": "把参考图改成杂志封面",
                    "mode": "edit",
                    "preview": "/example.png",
                    "reference_image_urls": ["/ref.png"],
                }
            )

            self.assertEqual(created["mode"], "edit")
            self.assertEqual(len(storage.load_prompt_library()), 2)

            updated = service.update_prompt(created["id"], {"title": "新版提示词", "reference_image_urls": "/a.png\n/b.png"})
            self.assertIsNotNone(updated)
            self.assertEqual(updated["title"], "新版提示词")
            self.assertEqual(updated["reference_image_urls"], ["/a.png", "/b.png"])

            asset_url = service.save_asset(b"image-bytes", filename="sample.png", content_type="image/png")
            self.assertTrue(asset_url.startswith("/prompt-assets/"))
            self.assertTrue(list((root / "assets").rglob("*.png")))

            self.assertTrue(service.delete_prompt(created["id"]))
            self.assertEqual(len(service.list_prompts()), 1)


if __name__ == "__main__":
    unittest.main()
