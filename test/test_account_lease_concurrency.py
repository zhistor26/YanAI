from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from threading import Barrier, Event, Lock
import time
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "test-auth")

from services.account_service import AccountService
from services.protocol import conversation, openai_v1_response
from services.storage.database_storage import DatabaseStorageBackend


class AccountLeaseConcurrencyTest(unittest.TestCase):
    def test_concurrent_leases_do_not_exceed_account_max_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = DatabaseStorageBackend(f"sqlite:///{(Path(tmp_dir) / 'lease.db').as_posix()}")
            service = AccountService(storage.repository_provider)
            service.add_account_items(
                [
                    {"access_token": "token-a", "status": "正常", "quota": 20, "max_concurrency": 2},
                    {"access_token": "token-b", "status": "正常", "quota": 20, "max_concurrency": 1},
                ]
            )

            start = Barrier(20)
            release = Event()
            lock = Lock()
            leased_tokens: list[str] = []
            failures = 0

            def worker() -> None:
                nonlocal failures
                start.wait()
                try:
                    lease = service.lease_available_account(ttl_seconds=60)
                except RuntimeError:
                    with lock:
                        failures += 1
                    return
                with lock:
                    leased_tokens.append(lease.access_token)
                release.wait(timeout=2)
                service.release_image_account(lease, success=True)

            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(worker) for _ in range(20)]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with lock:
                        if len(leased_tokens) + failures >= 20:
                            break
                    time.sleep(0.01)
                release.set()
                for future in as_completed(futures):
                    future.result()

            self.assertEqual(len(leased_tokens), 3)
            self.assertEqual(failures, 17)
            self.assertLessEqual(leased_tokens.count("token-a"), 2)
            self.assertLessEqual(leased_tokens.count("token-b"), 1)

            accounts = {item["access_token"]: item for item in service.export_accounts(["token-a", "token-b"])["items"]}
            self.assertEqual(accounts["token-a"]["inflight_count"], 0)
            self.assertEqual(accounts["token-b"]["inflight_count"], 0)
            self.assertEqual(accounts["token-a"]["success"] + accounts["token-b"]["success"], 3)
            storage.close()

    def test_expired_lease_can_be_reacquired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = DatabaseStorageBackend(f"sqlite:///{(Path(tmp_dir) / 'expired.db').as_posix()}")
            service = AccountService(storage.repository_provider)
            past = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
            service.add_account_items(
                [
                    {
                        "access_token": "token-a",
                        "status": "正常",
                        "quota": 5,
                        "max_concurrency": 1,
                        "inflight_count": 1,
                        "lease_owner": "old-owner",
                        "lease_owners": ["old-owner"],
                        "leased_until": past,
                    }
                ]
            )

            lease = service.lease_available_account(lease_owner="new-owner", ttl_seconds=60)

            self.assertEqual(lease.access_token, "token-a")
            account = service.export_accounts(["token-a"])["items"][0]
            self.assertEqual(account["inflight_count"], 1)
            self.assertEqual(account["lease_owner"], "new-owner")
            self.assertEqual(account["lease_owners"], ["new-owner"])
            storage.close()

    def test_account_update_preserves_active_lease_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = DatabaseStorageBackend(f"sqlite:///{(Path(tmp_dir) / 'refresh.db').as_posix()}")
            service = AccountService(storage.repository_provider)
            service.add_account_items([{"access_token": "token-a", "status": "正常", "quota": 5}])

            lease = service.lease_available_account(lease_owner="task-owner", ttl_seconds=60)
            service.update_account("token-a", {"quota": 4, "status": "正常"})

            account = service.export_accounts(["token-a"])["items"][0]
            self.assertEqual(account["inflight_count"], 1)
            self.assertEqual(account["lease_owner"], "task-owner")
            self.assertEqual(account["lease_owners"], ["task-owner"])

            service.release_image_account(lease, success=False)
            account = service.export_accounts(["token-a"])["items"][0]
            self.assertEqual(account["inflight_count"], 0)
            self.assertIsNone(account["lease_owner"])
            self.assertEqual(account["fail"], 1)
            storage.close()

    def test_generator_close_after_result_releases_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            storage = DatabaseStorageBackend(f"sqlite:///{(Path(tmp_dir) / 'closed.db').as_posix()}")
            service = AccountService(storage.repository_provider)
            service.add_account_items([{"access_token": "token-a", "status": "正常", "quota": 5}])

            class FakeBackend:
                def __init__(self, access_token: str) -> None:
                    self.access_token = access_token

            class FakeLogService:
                def add(self, *args, **kwargs) -> None:
                    return None

            def fake_stream_image_outputs(backend, request, index, total):
                self.assertEqual(backend.access_token, "token-a")
                yield conversation.ImageOutput(
                    kind="result",
                    model=request.model,
                    index=index,
                    total=total,
                    data=[{"b64_json": "aW1hZ2U="}],
                )

            with (
                patch.object(conversation, "account_service", service),
                patch.object(conversation, "OpenAIBackendAPI", FakeBackend),
                patch.object(conversation, "log_service", FakeLogService()),
                patch.object(conversation, "stream_image_outputs", fake_stream_image_outputs),
            ):
                outputs = conversation.stream_image_outputs_with_pool(
                    conversation.ConversationRequest(
                        prompt="draw",
                        model="gpt-image-2",
                        request_id="req-close",
                    )
                )
                first = next(outputs)
                self.assertEqual(first.kind, "result")
                outputs.close()

            account = service.export_accounts(["token-a"])["items"][0]
            self.assertEqual(account["inflight_count"], 0)
            self.assertIsNone(account["lease_owner"])
            self.assertEqual(account["quota"], 4)
            self.assertEqual(account["success"], 1)
            storage.close()

    def test_responses_image_stream_closes_after_first_result(self) -> None:
        closed = False

        def outputs():
            nonlocal closed
            try:
                yield conversation.ImageOutput(
                    kind="result",
                    model="gpt-image-2",
                    index=1,
                    total=1,
                    data=[{"b64_json": "aW1hZ2U="}],
                )
            finally:
                closed = True

        events = list(openai_v1_response.stream_image_response(outputs(), "draw", "gpt-image-2"))

        self.assertTrue(closed)
        self.assertEqual(events[-1]["type"], "response.completed")


if __name__ == "__main__":
    unittest.main()
