from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid
from typing import Any

from services.config import config
from services.storage.base import StorageBackend

BASE_DIR = Path(__file__).resolve().parents[1]
BOOTSTRAP_PROMPT_PATHS = (
    BASE_DIR / "data" / "prompt_library.seed.json",
    BASE_DIR / "web" / "public" / "banana-prompt-quicker" / "prompts.json",
    BASE_DIR / "web_dist" / "banana-prompt-quicker" / "prompts.json",
)

ALLOWED_IMAGE_EXTENSIONS = {
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".webp",
}
CONTENT_TYPE_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}
MAX_PROMPT_ASSET_BYTES = 10 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: object) -> str:
    return str(value or "").strip()


def _stable_id(raw: dict[str, Any]) -> str:
    seed = "\0".join(
        [
            _clean(raw.get("title")),
            _clean(raw.get("prompt")),
            _clean(raw.get("created")),
        ],
    )
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _normalize_mode(value: object) -> str:
    normalized = _clean(value).lower()
    if normalized in {"edit", "image", "image-to-image", "i2i", "图生图"}:
        return "edit"
    return "generate"


def _normalize_url_list(value: object) -> list[str]:
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, str):
        candidates = value.replace(",", "\n").splitlines()
    else:
        candidates = []
    return [url for item in candidates if (url := _clean(item))]


def _normalize_prompt(raw: object, *, generated_id: bool = True) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    title = _clean(raw.get("title"))
    prompt = _clean(raw.get("prompt"))
    if not title or not prompt:
        return None
    item_id = _clean(raw.get("id")) or (_stable_id(raw) if generated_id else uuid.uuid4().hex[:16])
    created = _clean(raw.get("created")) or _clean(raw.get("created_at")) or _now_iso()
    return {
        "id": item_id,
        "title": title,
        "preview": _clean(raw.get("preview")),
        "reference_image_urls": _normalize_url_list(raw.get("reference_image_urls")),
        "prompt": prompt,
        "author": _clean(raw.get("author")),
        "link": _clean(raw.get("link")),
        "mode": _normalize_mode(raw.get("mode")),
        "category": _clean(raw.get("category")),
        "sub_category": _clean(raw.get("sub_category")),
        "created": created,
        "updated_at": _clean(raw.get("updated_at")) or created,
    }


class PromptLibraryService:
    def __init__(
        self,
        storage: StorageBackend,
        *,
        bootstrap_paths: tuple[Path, ...] = BOOTSTRAP_PROMPT_PATHS,
        assets_dir: Path | None = None,
    ):
        self.storage = storage
        self.bootstrap_paths = bootstrap_paths
        self.assets_dir = assets_dir or config.prompt_assets_dir
        self._items = self._load_items()

    def _load_items(self) -> list[dict[str, Any]]:
        try:
            stored = self.storage.load_prompt_library()
        except Exception:
            stored = []
        items = [_normalize_prompt(item) for item in stored if isinstance(item, dict)]
        normalized = [item for item in items if item is not None]
        if normalized:
            return normalized
        return self._load_bootstrap_items()

    def _load_bootstrap_items(self) -> list[dict[str, Any]]:
        for path in self.bootstrap_paths:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if isinstance(data, dict):
                raw_items = data.get("prompts") or data.get("items")
            else:
                raw_items = data
            if not isinstance(raw_items, list):
                continue
            items = [_normalize_prompt(item) for item in raw_items if isinstance(item, dict)]
            normalized = [item for item in items if item is not None]
            if normalized:
                return normalized
        return []

    def _save(self) -> None:
        self.storage.save_prompt_library(self._items)

    def list_prompts(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._items]

    def create_prompt(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = _normalize_prompt({**payload, "id": uuid.uuid4().hex[:16]}, generated_id=False)
        if item is None:
            raise ValueError("title and prompt are required")
        now = _now_iso()
        item["created"] = item.get("created") or now
        item["updated_at"] = now
        self._items = [item, *self._items]
        self._save()
        return dict(item)

    def update_prompt(self, prompt_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        normalized_id = _clean(prompt_id)
        if not normalized_id:
            return None
        for index, current in enumerate(self._items):
            if current.get("id") != normalized_id:
                continue
            candidate = dict(current)
            for key in (
                "title",
                "preview",
                "reference_image_urls",
                "prompt",
                "author",
                "link",
                "mode",
                "category",
                "sub_category",
            ):
                if key in payload:
                    candidate[key] = payload.get(key)
            candidate["updated_at"] = _now_iso()
            item = _normalize_prompt(candidate)
            if item is None:
                raise ValueError("title and prompt are required")
            self._items[index] = item
            self._save()
            return dict(item)
        return None

    def delete_prompt(self, prompt_id: str) -> bool:
        normalized_id = _clean(prompt_id)
        if not normalized_id:
            return False
        before = len(self._items)
        self._items = [item for item in self._items if item.get("id") != normalized_id]
        if len(self._items) == before:
            return False
        self._save()
        return True

    def save_asset(self, data: bytes, *, filename: str = "", content_type: str = "") -> str:
        if not data:
            raise ValueError("image file is empty")
        if len(data) > MAX_PROMPT_ASSET_BYTES:
            raise ValueError("image file is too large")
        suffix = Path(filename or "").suffix.lower()
        content_suffix = CONTENT_TYPE_EXTENSIONS.get(_clean(content_type).lower(), "")
        if suffix not in ALLOWED_IMAGE_EXTENSIONS:
            suffix = content_suffix
        if suffix not in ALLOWED_IMAGE_EXTENSIONS:
            raise ValueError("unsupported image type")

        day = datetime.now().strftime("%Y/%m/%d")
        target_dir = self.assets_dir / day
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(data)
        return f"/prompt-assets/{day}/{target.name}"


prompt_library_service = PromptLibraryService(config.get_storage_backend())
