from __future__ import annotations

import json
import unittest

from services.protocol.conversation import assistant_message_text, iter_conversation_payloads


class ConversationTextParsingTests(unittest.TestCase):
    def test_assistant_message_text_reads_object_parts(self) -> None:
        message = {
            "author": {"role": "assistant"},
            "content": {
                "content_type": "text",
                "parts": [
                    {"type": "text", "text": "你好"},
                    {"content_type": "text", "text": "，有什么可以帮你？"},
                ],
            },
        }

        self.assertEqual(assistant_message_text(message), "你好，有什么可以帮你？")

    def test_iter_payloads_reads_nested_text_patch_path(self) -> None:
        payloads = iter([
            json.dumps({"p": "/message/content/parts/0/text", "o": "append", "v": "你"}, ensure_ascii=False),
            json.dumps({"p": "/message/content/parts/0/text", "o": "append", "v": "好"}, ensure_ascii=False),
            "[DONE]",
        ])

        events = list(iter_conversation_payloads(payloads))
        deltas = [event.get("delta") for event in events if event.get("type") == "conversation.delta"]

        self.assertEqual(deltas, ["你", "好"])

    def test_iter_payloads_reads_deep_text_patch_path(self) -> None:
        payloads = iter([
            json.dumps({"p": "/message/content/parts/0/content/text", "o": "append", "v": "深层文本"}, ensure_ascii=False),
            "[DONE]",
        ])

        events = list(iter_conversation_payloads(payloads))
        deltas = [event.get("delta") for event in events if event.get("type") == "conversation.delta"]

        self.assertEqual(deltas, ["深层文本"])

    def test_iter_payloads_reads_append_operation_without_path(self) -> None:
        payloads = iter([
            json.dumps({"o": "append", "v": "无路径文本"}, ensure_ascii=False),
            "[DONE]",
        ])

        events = list(iter_conversation_payloads(payloads))
        deltas = [event.get("delta") for event in events if event.get("type") == "conversation.delta"]

        self.assertEqual(deltas, ["无路径文本"])

    def test_iter_payloads_reads_message_carried_directly_in_v(self) -> None:
        payloads = iter([
            json.dumps({
                "v": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": [{"text": "可以正常输出"}]},
                },
            }, ensure_ascii=False),
            "[DONE]",
        ])

        events = list(iter_conversation_payloads(payloads))
        deltas = [event.get("delta") for event in events if event.get("type") == "conversation.delta"]

        self.assertEqual(deltas, ["可以正常输出"])


if __name__ == "__main__":
    unittest.main()
