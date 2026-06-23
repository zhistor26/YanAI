"""LazyCat 首次启动：初始化 config、默认 GPT 中转渠道、用户额度。"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("/app/data")
CONFIG_FILE = Path(os.getenv("YANAI_CONFIG_FILE", "/app/config/config.json"))
DEFAULT_CHANNEL_URL = os.getenv("YANAI_DEFAULT_CHANNEL_URL", "https://otuapi.com").strip().rstrip("/")
DEFAULT_CHANNEL_NAME = os.getenv("YANAI_DEFAULT_CHANNEL_NAME", "otuapi").strip() or "otuapi"
DEFAULT_CHANNEL_KEY = os.getenv("YANAI_DEFAULT_CHANNEL_API_KEY", "").strip()
DEFAULT_CHANNEL_TYPE = os.getenv("YANAI_DEFAULT_CHANNEL_TYPE", "async_videos").strip() or "async_videos"
DEFAULT_CHANNEL_MODELS = [
    item.strip()
    for item in os.getenv("YANAI_DEFAULT_CHANNEL_MODELS", "gpt-image-2").replace(";", ",").split(",")
    if item.strip()
] or ["gpt-image-2"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path, default):
    if not path.exists() or path.is_dir():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_config() -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.is_dir():
        return
    data = _load_json(CONFIG_FILE, {})
    if not isinstance(data, dict):
        data = {}
    changed = False
    defaults = {
        "internal_pool_enabled": False,
        "new_user_initial_quota": 100,
        "allow_user_registration": True,
    }
    for key, value in defaults.items():
        if key not in data:
            data[key] = value
            changed = True
    if changed or not CONFIG_FILE.exists() or CONFIG_FILE.stat().st_size == 0:
        example = Path("/app/config.example.json")
        if not data and example.is_file():
            data = _load_json(example, {})
            for key, value in defaults.items():
                data[key] = value
        _save_json(CONFIG_FILE, data)


def ensure_default_channel() -> None:
    if not DEFAULT_CHANNEL_KEY:
        return
    channels_path = DATA_DIR / "channels.json"
    channels = _load_json(channels_path, [])
    if not isinstance(channels, list):
        channels = []

    channels = [
        item
        for item in channels
        if isinstance(item, dict) and "zzshu.cc" not in str(item.get("base_url") or "").lower()
    ]

    channel_payload = {
        "name": DEFAULT_CHANNEL_NAME,
        "type": DEFAULT_CHANNEL_TYPE,
        "base_url": DEFAULT_CHANNEL_URL,
        "api_key": DEFAULT_CHANNEL_KEY,
        "models": DEFAULT_CHANNEL_MODELS,
        "weight": 1,
        "priority": 10,
        "timeout": 180,
        "enabled": True,
        "updated_at": _now_iso(),
    }

    for item in channels:
        if not isinstance(item, dict):
            continue
        base_url = str(item.get("base_url") or "").strip().rstrip("/")
        if base_url == DEFAULT_CHANNEL_URL:
            item.update(channel_payload)
            if not _clean(item.get("id")):
                item["id"] = uuid.uuid4().hex[:12]
            if not _clean(item.get("created_at")):
                item["created_at"] = _now_iso()
            _save_json(channels_path, channels)
            return

    channels.append(
        {
            "id": uuid.uuid4().hex[:12],
            "created_at": _now_iso(),
            **channel_payload,
        }
    )
    _save_json(channels_path, channels)


def _clean(value: object) -> str:
    return str(value or "").strip()


def ensure_user_quota() -> None:
    users_path = DATA_DIR / "users.json"
    users = _load_json(users_path, [])
    if not isinstance(users, list):
        return
    changed = False
    for user in users:
        if not isinstance(user, dict):
            continue
        if int(user.get("quota") or 0) > 0:
            continue
        user["quota"] = max(100, int(os.getenv("YANAI_DEFAULT_USER_QUOTA", "100") or 100))
        user["updated_at"] = _now_iso()
        changed = True
    if changed:
        _save_json(users_path, users)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_config()
    ensure_default_channel()
    ensure_user_quota()


if __name__ == "__main__":
    main()
