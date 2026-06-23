from __future__ import annotations

import unittest

from services.openai_backend_api import OpenAIBackendAPI
from services.protocol.openai_v1_response import messages_from_input


class ResponsesMultimodalTests(unittest.TestCase):
    def test_messages_from_input_preserves_input_image_blocks(self) -> None:
        messages = messages_from_input([
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "输出截图里的内容"},
                    {"type": "input_image", "image_url": r"C:\Users\sunmiao\AppData\Local\Temp\shot.png"},
                ],
            }
        ])

        self.assertEqual(len(messages), 1)
        content = messages[0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "输出截图里的内容"})
        self.assertEqual(content[1]["type"], "input_image")
        self.assertEqual(content[1]["image_url"], r"C:\Users\sunmiao\AppData\Local\Temp\shot.png")

    def test_backend_converts_image_blocks_to_web_attachments(self) -> None:
        backend = object.__new__(OpenAIBackendAPI)
        uploaded_refs: list[tuple[str, str]] = []

        def fake_upload(image: str, file_name: str = "image.png") -> dict[str, object]:
            uploaded_refs.append((image, file_name))
            return {
                "file_id": "file_test123",
                "file_name": file_name,
                "file_size": 1234,
                "mime_type": "image/png",
                "width": 640,
                "height": 480,
            }

        backend._upload_image = fake_upload

        converted = backend._api_messages_to_conversation_messages([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别图片"},
                    {"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U="},
                ],
            }
        ])

        self.assertEqual(uploaded_refs, [("data:image/png;base64,aW1hZ2U=", "image_1.png")])
        message = converted[0]
        self.assertEqual(message["author"]["role"], "user")
        self.assertEqual(message["content"]["content_type"], "multimodal_text")
        self.assertEqual(message["content"]["parts"][0], "请识别图片")
        self.assertEqual(message["content"]["parts"][1]["content_type"], "image_asset_pointer")
        self.assertEqual(message["content"]["parts"][1]["asset_pointer"], "file-service://file_test123")
        self.assertEqual(message["metadata"]["attachments"][0]["id"], "file_test123")
        self.assertEqual(message["metadata"]["attachments"][0]["mimeType"], "image/png")


if __name__ == "__main__":
    unittest.main()
