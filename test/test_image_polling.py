from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from services.openai_backend_api import OpenAIBackendAPI
from services.protocol import conversation


class OpenAIBackendImagePollingTests(unittest.TestCase):
    def test_extract_image_tool_records_reads_assistant_asset_pointers(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        data = {
            "mapping": {
                "msg-1": {
                    "message": {
                        "author": {"role": "assistant"},
                        "create_time": 10,
                        "metadata": {"async_task_type": "image_gen"},
                        "content": {
                            "content_type": "multimodal_text",
                            "parts": [
                                {
                                    "content_type": "image_asset_pointer",
                                    "asset_pointer": "file-service://file_00000000aaaaaaaaaaaaaaaaaaaaaaaa",
                                },
                                {"asset_pointer": "sediment://sediment-1"},
                            ],
                        },
                    },
                }
            }
        }

        records = backend._extract_image_tool_records(data)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["file_ids"], ["file_00000000aaaaaaaaaaaaaaaaaaaaaaaa"])
        self.assertEqual(records[0]["sediment_ids"], ["sediment-1"])

    def test_resolve_conversation_image_urls_polls_when_no_initial_ids(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        calls: list[tuple[str, list[str], list[str]]] = []

        def poll(conversation_id: str, timeout: float, file_ids: list[str], sediment_ids: list[str]) -> tuple[list[str], list[str]]:
            calls.append((conversation_id, list(file_ids), list(sediment_ids)))
            return ["file_00000000bbbbbbbbbbbbbbbbbbbbbbbb"], []

        backend._poll_image_results = poll  # type: ignore[method-assign]
        backend._resolve_image_urls = lambda conversation_id, file_ids, sediment_ids: [  # type: ignore[method-assign]
            f"https://example.test/{file_ids[0]}.png"
        ]
        fake_config = SimpleNamespace(
            image_poll_timeout_secs=12,
            image_check_before_hit_enabled=True,
            image_settle_enabled=True,
        )

        with mock.patch("services.openai_backend_api.config", fake_config):
            urls = backend.resolve_conversation_image_urls("conv-1", [], [])

        self.assertEqual(urls, ["https://example.test/file_00000000bbbbbbbbbbbbbbbbbbbbbbbb.png"])
        self.assertEqual(calls, [("conv-1", [], [])])

    def test_resolve_conversation_image_urls_polls_with_initial_ids_when_check_enabled(self) -> None:
        backend = OpenAIBackendAPI.__new__(OpenAIBackendAPI)
        calls: list[tuple[str, list[str], list[str]]] = []

        def poll(conversation_id: str, timeout: float, file_ids: list[str], sediment_ids: list[str]) -> tuple[list[str], list[str]]:
            calls.append((conversation_id, list(file_ids), list(sediment_ids)))
            return ["file_00000000cccccccccccccccccccccccc"], []

        backend._poll_image_results = poll  # type: ignore[method-assign]
        backend._resolve_image_urls = lambda conversation_id, file_ids, sediment_ids: list(file_ids)  # type: ignore[method-assign]
        fake_config = SimpleNamespace(
            image_poll_timeout_secs=12,
            image_check_before_hit_enabled=True,
            image_settle_enabled=True,
        )

        with mock.patch("services.openai_backend_api.config", fake_config):
            urls = backend.resolve_conversation_image_urls(
                "conv-1",
                ["file_00000000bbbbbbbbbbbbbbbbbbbbbbbb"],
                [],
            )

        self.assertEqual(
            urls,
            [
                "file_00000000bbbbbbbbbbbbbbbbbbbbbbbb",
                "file_00000000cccccccccccccccccccccccc",
            ],
        )
        self.assertEqual(calls, [("conv-1", ["file_00000000bbbbbbbbbbbbbbbbbbbbbbbb"], [])])


class StreamImageOutputPollingTests(unittest.TestCase):
    def test_text_tool_reply_is_polled_before_message_failure(self) -> None:
        image_bytes = b"fake-png-bytes"
        payloads = iter([
            json.dumps(
                {
                    "conversation_id": "conv-1",
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"content_type": "text", "parts": ['{"size":"1024x1024","n":1}']},
                    },
                },
                ensure_ascii=False,
            ),
            "[DONE]",
        ])
        backend = SimpleNamespace(
            stream_conversation=lambda **kwargs: payloads,
            resolve_conversation_image_urls=lambda conversation_id, file_ids, sediment_ids, poll_timeout_secs=None: [
                "https://example.test/result.png"
            ],
            download_image_bytes=lambda urls: [image_bytes],
        )
        request = conversation.ConversationRequest(
            model="gpt-image-2",
            prompt="画一只红色杯子",
            response_format="b64_json",
            base_url="http://127.0.0.1:8000",
        )
        fake_config = SimpleNamespace(
            cleanup_old_images=lambda: 0,
            images_dir=None,
            base_url="http://127.0.0.1:8000",
            image_poll_timeout_secs=1,
        )

        with mock.patch.object(conversation, "config", fake_config), mock.patch.object(
            conversation,
            "save_image_bytes",
            lambda data, base_url=None: f"{base_url}/images/result.png",
        ):
            outputs = list(conversation.stream_image_outputs(backend, request))

        results = [output for output in outputs if output.kind == "result"]
        messages = [output for output in outputs if output.kind == "message"]
        self.assertEqual(len(results), 1)
        self.assertEqual(messages, [])
        self.assertEqual(results[0].data[0]["url"], "http://127.0.0.1:8000/images/result.png")
        self.assertEqual(results[0].data[0]["b64_json"], base64.b64encode(image_bytes).decode("ascii"))


if __name__ == "__main__":
    unittest.main()
