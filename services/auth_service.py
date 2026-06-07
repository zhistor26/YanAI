from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Literal

from services.config import config
from services.registration_security import validate_registration_email, verify_registration_code
from services.repositories.base import RepositoryProvider
from services.repositories.storage_adapter import RepositoryStorageAdapter
from services.storage.base import StorageBackend

AuthRole = Literal["admin", "user"]

_SESSION_DAYS = 30
_PASSWORD_ITERATIONS = 210_000
IMAGE_CHANNEL_CONFIG_KEY = "image_channel_config"
DEFAULT_USER_IMAGE_CHANNEL_MODELS = ["gpt-image-2", "codex-gpt-image-2", "gpt-5-5"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_password(password: str, salt: str | None = None) -> str:
    real_salt = salt or secrets.token_urlsafe(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        real_salt.encode("utf-8"),
        _PASSWORD_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${_PASSWORD_ITERATIONS}${real_salt}${digest}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, digest = str(encoded or "").split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    try:
        rounds = int(iterations)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        rounds,
    ).hex()
    return hmac.compare_digest(candidate, digest)


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _normalize_channel_models(value: object) -> list[str]:
    if isinstance(value, str):
        candidates = value.replace(";", ",").replace("\n", ",").split(",")
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = DEFAULT_USER_IMAGE_CHANNEL_MODELS
    seen: set[str] = set()
    models: list[str] = []
    for item in candidates:
        model = _clean_text(item)
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models or list(DEFAULT_USER_IMAGE_CHANNEL_MODELS)


def _normalize_user_image_channel_config(
    raw: object,
    current: object = None,
    *,
    include_api_key: bool = False,
) -> dict[str, object]:
    raw_config = raw if isinstance(raw, dict) else {}
    current_config = current if isinstance(current, dict) else {}

    def pick(key: str, default: object = "") -> object:
        return raw_config[key] if key in raw_config else current_config.get(key, default)

    api_key = _clean_text(raw_config.get("api_key")) if "api_key" in raw_config else ""
    if not api_key:
        api_key = _clean_text(current_config.get("api_key"))

    try:
        timeout = max(5, int(pick("timeout", 60) or 60))
    except (TypeError, ValueError):
        timeout = 60

    normalized = {
        "enabled": _bool(pick("enabled", False), False),
        "name": _clean_text(pick("name", "")) or "个人生图渠道",
        "base_url": _clean_text(pick("base_url", "")).rstrip("/"),
        "models": _normalize_channel_models(pick("models", DEFAULT_USER_IMAGE_CHANNEL_MODELS)),
        "timeout": timeout,
    }
    if include_api_key:
        normalized["api_key"] = api_key
    else:
        normalized["has_api_key"] = bool(api_key)
    return normalized


class AuthService:
    def __init__(self, storage: StorageBackend | RepositoryProvider):
        self.repositories = storage if isinstance(storage, RepositoryProvider) else None
        self.storage = RepositoryStorageAdapter(storage) if isinstance(storage, RepositoryProvider) else storage
        self._lock = RLock()
        self._items = self._load_keys()
        self._users = self._load_users()
        self._sessions = self._load_sessions()
        self._redeem_codes = self._load_redeem_codes()
        self._quota_reservations: dict[str, dict[str, object]] = {}
        self._last_used_flush_at: dict[str, datetime] = {}
        self.expire_quota_reservations()

    @staticmethod
    def _clean(value: object) -> str:
        return str(value or "").strip()

    @staticmethod
    def _clean_email(value: object) -> str:
        return str(value or "").strip().lower()

    def _normalize_item(self, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        role = self._clean(raw.get("role")).lower()
        if role not in {"admin", "user"}:
            return None
        key_hash = self._clean(raw.get("key_hash"))
        if not key_hash:
            return None
        item_id = self._clean(raw.get("id")) or uuid.uuid4().hex[:12]
        name = self._clean(raw.get("name")) or ("管理员密钥" if role == "admin" else "普通用户")
        created_at = self._clean(raw.get("created_at")) or _now_iso()
        last_used_at = self._clean(raw.get("last_used_at")) or None
        return {
            "id": item_id,
            "name": name,
            "role": role,
            "key_hash": key_hash,
            "enabled": bool(raw.get("enabled", True)),
            "created_at": created_at,
            "last_used_at": last_used_at,
        }

    def _normalize_user(self, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        email = self._clean_email(raw.get("email"))
        role = self._clean(raw.get("role")).lower() or "user"
        if role not in {"admin", "user"} or not email:
            return None
        item_id = self._clean(raw.get("id")) or uuid.uuid4().hex[:12]
        status = self._clean(raw.get("status")).lower() or "active"
        if status not in {"active", "disabled"}:
            status = "active"
        try:
            quota = max(0, int(raw.get("quota") or 0))
        except (TypeError, ValueError):
            quota = 0
        try:
            quota_used = max(0, int(raw.get("quota_used") or 0))
        except (TypeError, ValueError):
            quota_used = 0
        name = self._clean(raw.get("name")) or email.split("@")[0]
        return {
            "id": item_id,
            "email": email,
            "name": name,
            "role": role,
            "status": status,
            "password_hash": self._clean(raw.get("password_hash")),
            "quota": quota,
            "quota_used": quota_used,
            "auth_provider": self._clean(raw.get("auth_provider")) or "password",
            "linuxdo_id": self._clean(raw.get("linuxdo_id")) or None,
            "linuxdo_username": self._clean(raw.get("linuxdo_username")) or None,
            "linuxdo_trust_level": int(raw.get("linuxdo_trust_level") or 0),
            "webdav_config": raw.get("webdav_config") if isinstance(raw.get("webdav_config"), dict) else {},
            IMAGE_CHANNEL_CONFIG_KEY: _normalize_user_image_channel_config(
                raw.get(IMAGE_CHANNEL_CONFIG_KEY),
                include_api_key=True,
            ),
            "created_at": self._clean(raw.get("created_at")) or _now_iso(),
            "updated_at": self._clean(raw.get("updated_at")) or _now_iso(),
            "last_login_at": self._clean(raw.get("last_login_at")) or None,
        }

    def _normalize_session(self, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        session_id = self._clean(raw.get("id")) or uuid.uuid4().hex[:16]
        token_hash = self._clean(raw.get("token_hash"))
        user_id = self._clean(raw.get("user_id"))
        if not token_hash or not user_id:
            return None
        expires_at = self._clean(raw.get("expires_at")) or (_now() + timedelta(days=_SESSION_DAYS)).isoformat()
        return {
            "id": session_id,
            "token_hash": token_hash,
            "user_id": user_id,
            "created_at": self._clean(raw.get("created_at")) or _now_iso(),
            "last_used_at": self._clean(raw.get("last_used_at")) or None,
            "expires_at": expires_at,
        }

    def _normalize_redeem_code(self, raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        code = self._clean(raw.get("code")).upper()
        if not code:
            return None
        try:
            quota = max(1, int(raw.get("quota") or raw.get("amount") or 1))
        except (TypeError, ValueError):
            quota = 1
        try:
            max_uses = max(1, int(raw.get("max_uses") or 1))
        except (TypeError, ValueError):
            max_uses = 1
        used_by = raw.get("used_by") if isinstance(raw.get("used_by"), list) else []
        status = self._clean(raw.get("status")).lower() or "enabled"
        if status not in {"enabled", "disabled"}:
            status = "enabled"
        return {
            "id": self._clean(raw.get("id")) or uuid.uuid4().hex[:12],
            "code": code,
            "quota": quota,
            "status": status,
            "max_uses": max_uses,
            "used_count": min(max_uses, int(raw.get("used_count") or len(used_by))),
            "used_by": used_by,
            "expires_at": self._clean(raw.get("expires_at")) or None,
            "created_at": self._clean(raw.get("created_at")) or _now_iso(),
            "created_by": self._clean(raw.get("created_by")),
            "note": self._clean(raw.get("note")),
        }

    def _load_keys(self) -> list[dict[str, object]]:
        try:
            items = self.storage.load_auth_keys()
        except Exception:
            return []
        if not isinstance(items, list):
            return []
        return [normalized for item in items if (normalized := self._normalize_item(item)) is not None]

    def _load_users(self) -> list[dict[str, object]]:
        try:
            items = self.storage.load_users()
        except Exception:
            return []
        if not isinstance(items, list):
            return []
        return [normalized for item in items if (normalized := self._normalize_user(item)) is not None]

    def _load_sessions(self) -> list[dict[str, object]]:
        try:
            items = self.storage.load_sessions()
        except Exception:
            return []
        if not isinstance(items, list):
            return []
        now = _now()
        return [
            normalized
            for item in items
            if (normalized := self._normalize_session(item)) is not None
            and ((_parse_time(normalized.get("expires_at")) or now) >= now)
        ]

    def _load_redeem_codes(self) -> list[dict[str, object]]:
        try:
            items = self.storage.load_redeem_codes()
        except Exception:
            return []
        if not isinstance(items, list):
            return []
        return [normalized for item in items if (normalized := self._normalize_redeem_code(item)) is not None]

    def _save_keys(self) -> None:
        self.storage.save_auth_keys(self._items)

    def _save_users(self) -> None:
        self.storage.save_users(self._users)

    def _save_sessions(self) -> None:
        self.storage.save_sessions(self._sessions)

    def _save_redeem_codes(self) -> None:
        self.storage.save_redeem_codes(self._redeem_codes)

    @staticmethod
    def _public_item(item: dict[str, object]) -> dict[str, object]:
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "role": item.get("role"),
            "enabled": bool(item.get("enabled", True)),
            "created_at": item.get("created_at"),
            "last_used_at": item.get("last_used_at"),
        }

    @staticmethod
    def _public_user(user: dict[str, object], stats: dict[str, object] | None = None) -> dict[str, object]:
        return {
            "id": user.get("id"),
            "email": user.get("email"),
            "name": user.get("name"),
            "role": user.get("role"),
            "status": user.get("status"),
            "quota": int(user.get("quota") or 0),
            "quota_used": int(user.get("quota_used") or 0),
            "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at"),
            "last_login_at": user.get("last_login_at"),
            "auth_provider": user.get("auth_provider"),
            "linuxdo_id": user.get("linuxdo_id"),
            "linuxdo_username": user.get("linuxdo_username"),
            "linuxdo_trust_level": int(user.get("linuxdo_trust_level") or 0),
            "image_channel": _normalize_user_image_channel_config(
                user.get(IMAGE_CHANNEL_CONFIG_KEY),
                include_api_key=False,
            ),
            "image_count": int((stats or {}).get("image_count") or 0),
            "spent_quota": int((stats or {}).get("spent_quota") or int(user.get("quota_used") or 0)),
        }

    def _find_user_index_by_id(self, user_id: str) -> int:
        for index, user in enumerate(self._users):
            if self._clean(user.get("id")) == user_id:
                return index
        return -1

    def _find_user_index_by_email(self, email: str) -> int:
        normalized_email = self._clean_email(email)
        for index, user in enumerate(self._users):
            if self._clean_email(user.get("email")) == normalized_email:
                return index
        return -1

    def _find_user_index_by_linuxdo_id(self, linuxdo_id: str) -> int:
        normalized_id = self._clean(linuxdo_id)
        if not normalized_id:
            return -1
        for index, user in enumerate(self._users):
            if self._clean(user.get("linuxdo_id")) == normalized_id:
                return index
        return -1

    def _image_stats_by_user(self) -> dict[str, dict[str, int]]:
        stats: dict[str, dict[str, int]] = {}
        try:
            records = self.storage.load_image_records()
        except Exception:
            records = []
        for record in records:
            if not isinstance(record, dict):
                continue
            user_id = self._clean(record.get("owner_user_id"))
            if not user_id:
                continue
            item = stats.setdefault(user_id, {"image_count": 0, "spent_quota": 0})
            item["image_count"] += 1
            item["spent_quota"] += max(0, int(record.get("quota_cost") or 0))
        return stats

    def list_keys(self, role: AuthRole | None = None) -> list[dict[str, object]]:
        with self._lock:
            items = [item for item in self._items if role is None or item.get("role") == role]
            return [self._public_item(item) for item in items]

    def create_key(self, *, role: AuthRole, name: str = "") -> tuple[dict[str, object], str]:
        normalized_name = self._clean(name) or ("管理员密钥" if role == "admin" else "普通用户")
        raw_key = f"sk-{secrets.token_urlsafe(24)}"
        item = {
            "id": uuid.uuid4().hex[:12],
            "name": normalized_name,
            "role": role,
            "key_hash": _hash_key(raw_key),
            "enabled": True,
            "created_at": _now_iso(),
            "last_used_at": None,
        }
        with self._lock:
            self._items.append(item)
            self._save_keys()
            return self._public_item(item), raw_key

    def update_key(
        self,
        key_id: str,
        updates: dict[str, object],
        *,
        role: AuthRole | None = None,
    ) -> dict[str, object] | None:
        normalized_id = self._clean(key_id)
        if not normalized_id:
            return None
        with self._lock:
            for index, item in enumerate(self._items):
                if item.get("id") != normalized_id:
                    continue
                if role is not None and item.get("role") != role:
                    return None
                next_item = dict(item)
                if "name" in updates and updates.get("name") is not None:
                    next_item["name"] = self._clean(updates.get("name")) or next_item.get("name") or "普通用户"
                if "enabled" in updates and updates.get("enabled") is not None:
                    next_item["enabled"] = bool(updates.get("enabled"))
                self._items[index] = next_item
                self._save_keys()
                return self._public_item(next_item)
        return None

    def delete_key(self, key_id: str, *, role: AuthRole | None = None) -> bool:
        normalized_id = self._clean(key_id)
        if not normalized_id:
            return False
        with self._lock:
            before = len(self._items)
            self._items = [
                item
                for item in self._items
                if not (item.get("id") == normalized_id and (role is None or item.get("role") == role))
            ]
            if len(self._items) == before:
                return False
            self._save_keys()
            return True

    def email_exists(self, email: str) -> bool:
        with self._lock:
            return self._find_user_index_by_email(email) >= 0

    def register_user(
        self,
        *,
        email: str,
        password: str,
        name: str = "",
        verification_code: str = "",
    ) -> tuple[dict[str, object], str]:
        if not config.allow_user_registration:
            raise ValueError("registration is disabled")
        normalized_email = validate_registration_email(email)
        if self.email_exists(normalized_email):
            raise ValueError("email already exists")
        if config.email_verification_enabled:
            verify_registration_code(normalized_email, verification_code)
        return self.create_user(
            email=normalized_email,
            password=password,
            name=name,
            quota=config.new_user_initial_quota,
            role="user",
        )

    def create_user(
        self,
        *,
        email: str,
        password: str,
        name: str = "",
        quota: int = 0,
        role: AuthRole = "user",
        status: str = "active",
    ) -> tuple[dict[str, object], str]:
        normalized_email = self._clean_email(email)
        if "@" not in normalized_email:
            raise ValueError("email is invalid")
        if len(str(password or "")) < 6:
            raise ValueError("password must be at least 6 characters")
        with self._lock:
            if self._find_user_index_by_email(normalized_email) >= 0:
                raise ValueError("email already exists")
            now = _now_iso()
            user = self._normalize_user({
                "id": uuid.uuid4().hex[:12],
                "email": normalized_email,
                "name": name or normalized_email.split("@")[0],
                "role": role,
                "status": status,
                "password_hash": _hash_password(password),
                "quota": quota,
                "quota_used": 0,
                "created_at": now,
                "updated_at": now,
                "last_login_at": None,
            })
            if user is None:
                raise ValueError("user payload is invalid")
            self._users.append(user)
            token = self._create_session_locked(str(user["id"]))
            self._save_users()
            self._save_sessions()
            return self._public_user(user), token

    def login_user(self, *, email: str, password: str) -> tuple[dict[str, object], str]:
        normalized_email = self._clean_email(email)
        with self._lock:
            index = self._find_user_index_by_email(normalized_email)
            if index < 0:
                raise ValueError("email or password is invalid")
            user = self._users[index]
            if user.get("status") != "active":
                raise ValueError("user is disabled")
            if not _verify_password(str(password or ""), self._clean(user.get("password_hash"))):
                raise ValueError("email or password is invalid")
            next_user = dict(user)
            next_user["last_login_at"] = _now_iso()
            next_user["updated_at"] = next_user["last_login_at"]
            self._users[index] = next_user
            token = self._create_session_locked(str(next_user["id"]))
            self._save_users()
            self._save_sessions()
            return self._public_user(next_user), token

    def _create_session_locked(self, user_id: str) -> str:
        token = f"yai-{secrets.token_urlsafe(32)}"
        session = {
            "id": uuid.uuid4().hex[:16],
            "token_hash": _hash_key(token),
            "user_id": user_id,
            "created_at": _now_iso(),
            "last_used_at": None,
            "expires_at": (_now() + timedelta(days=_SESSION_DAYS)).isoformat(),
        }
        self._sessions.append(session)
        return token

    def get_user(self, user_id: str) -> dict[str, object] | None:
        with self._lock:
            index = self._find_user_index_by_id(self._clean(user_id))
            if index < 0:
                return None
            stats = self._image_stats_by_user().get(user_id)
            return self._public_user(self._users[index], stats)

    def list_users(self, query: str = "", status: str = "", role: str = "") -> list[dict[str, object]]:
        query_text = self._clean(query).lower()
        status_text = self._clean(status).lower()
        role_text = self._clean(role).lower()
        with self._lock:
            stats = self._image_stats_by_user()
            users = []
            for user in self._users:
                if status_text and user.get("status") != status_text:
                    continue
                if role_text and user.get("role") != role_text:
                    continue
                searchable = f"{user.get('email')} {user.get('name')}".lower()
                if query_text and query_text not in searchable:
                    continue
                users.append(self._public_user(user, stats.get(str(user.get("id")))))
            users.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            return users

    def update_user(self, user_id: str, updates: dict[str, object]) -> dict[str, object] | None:
        normalized_id = self._clean(user_id)
        with self._lock:
            index = self._find_user_index_by_id(normalized_id)
            if index < 0:
                return None
            current = dict(self._users[index])
            if "email" in updates and updates.get("email") is not None:
                email = self._clean_email(updates.get("email"))
                if "@" not in email:
                    raise ValueError("email is invalid")
                existing = self._find_user_index_by_email(email)
                if existing >= 0 and existing != index:
                    raise ValueError("email already exists")
                current["email"] = email
            if "name" in updates and updates.get("name") is not None:
                current["name"] = self._clean(updates.get("name")) or current.get("name")
            if "status" in updates and updates.get("status") is not None:
                status = self._clean(updates.get("status")).lower()
                if status not in {"active", "disabled"}:
                    raise ValueError("status is invalid")
                current["status"] = status
            if "quota" in updates and updates.get("quota") is not None:
                current["quota"] = max(0, int(updates.get("quota") or 0))
            if IMAGE_CHANNEL_CONFIG_KEY in updates and isinstance(updates.get(IMAGE_CHANNEL_CONFIG_KEY), dict):
                current[IMAGE_CHANNEL_CONFIG_KEY] = _normalize_user_image_channel_config(
                    updates.get(IMAGE_CHANNEL_CONFIG_KEY),
                    current.get(IMAGE_CHANNEL_CONFIG_KEY),
                    include_api_key=True,
                )
            current["updated_at"] = _now_iso()
            user = self._normalize_user(current)
            if user is None:
                return None
            self._users[index] = user
            self._save_users()
            return self._public_user(user)

    def get_user_image_channel_config(self, user_id: str, *, include_api_key: bool = False) -> dict[str, object]:
        with self._lock:
            index = self._find_user_index_by_id(self._clean(user_id))
            if index < 0:
                raise ValueError("user not found")
            return _normalize_user_image_channel_config(
                self._users[index].get(IMAGE_CHANNEL_CONFIG_KEY),
                include_api_key=include_api_key,
            )

    def merge_user_image_channel_config(
        self,
        user_id: str,
        updates: dict[str, object],
        *,
        include_api_key: bool = False,
    ) -> dict[str, object]:
        with self._lock:
            index = self._find_user_index_by_id(self._clean(user_id))
            if index < 0:
                raise ValueError("user not found")
            normalized = _normalize_user_image_channel_config(
                updates,
                self._users[index].get(IMAGE_CHANNEL_CONFIG_KEY),
                include_api_key=True,
            )
            if include_api_key:
                return normalized
            return _normalize_user_image_channel_config(normalized, include_api_key=False)

    def save_user_image_channel_config(self, user_id: str, updates: dict[str, object]) -> dict[str, object]:
        normalized_id = self._clean(user_id)
        with self._lock:
            index = self._find_user_index_by_id(normalized_id)
            if index < 0:
                raise ValueError("user not found")
            normalized = _normalize_user_image_channel_config(
                updates,
                self._users[index].get(IMAGE_CHANNEL_CONFIG_KEY),
                include_api_key=True,
            )
            if _bool(normalized.get("enabled")):
                if not _clean_text(normalized.get("base_url")):
                    raise ValueError("base_url is required when personal image channel is enabled")
                if not _clean_text(normalized.get("api_key")):
                    raise ValueError("api_key is required when personal image channel is enabled")
            current = dict(self._users[index])
            current[IMAGE_CHANNEL_CONFIG_KEY] = normalized
            current["updated_at"] = _now_iso()
            user = self._normalize_user(current)
            if user is None:
                raise ValueError("user payload is invalid")
            self._users[index] = user
            self._save_users()
            return _normalize_user_image_channel_config(user.get(IMAGE_CHANNEL_CONFIG_KEY), include_api_key=False)

    def delete_user(self, user_id: str) -> bool:
        normalized_id = self._clean(user_id)
        if not normalized_id:
            return False
        with self._lock:
            index = self._find_user_index_by_id(normalized_id)
            if index < 0:
                return False
            self._users.pop(index)
            self._sessions = [
                session
                for session in self._sessions
                if self._clean(session.get("user_id")) != normalized_id
            ]
            self._save_users()
            self._save_sessions()
            return True

    def delete_users(self, user_ids: list[str]) -> int:
        normalized_ids = {self._clean(user_id) for user_id in user_ids}
        normalized_ids.discard("")
        if not normalized_ids:
            return 0
        with self._lock:
            before = len(self._users)
            self._users = [
                user
                for user in self._users
                if self._clean(user.get("id")) not in normalized_ids
            ]
            removed = before - len(self._users)
            if removed <= 0:
                return 0
            self._sessions = [
                session
                for session in self._sessions
                if self._clean(session.get("user_id")) not in normalized_ids
            ]
            self._save_users()
            self._save_sessions()
            return removed

    def reset_password(self, user_id: str, password: str | None = None) -> tuple[dict[str, object], str] | None:
        next_password = str(password or "").strip() or secrets.token_urlsafe(10)
        if len(next_password) < 6:
            raise ValueError("password must be at least 6 characters")
        normalized_id = self._clean(user_id)
        with self._lock:
            index = self._find_user_index_by_id(normalized_id)
            if index < 0:
                return None
            user = dict(self._users[index])
            user["password_hash"] = _hash_password(next_password)
            user["updated_at"] = _now_iso()
            self._users[index] = user
            self._save_users()
            return self._public_user(user), next_password

    def adjust_user_quota(self, user_id: str, amount: int, mode: str = "add") -> dict[str, object] | None:
        normalized_id = self._clean(user_id)
        with self._lock:
            index = self._find_user_index_by_id(normalized_id)
            if index < 0:
                return None
            user = dict(self._users[index])
            current_quota = int(user.get("quota") or 0)
            next_quota = amount if mode == "set" else current_quota + amount
            user["quota"] = max(0, int(next_quota))
            user["updated_at"] = _now_iso()
            self._users[index] = self._normalize_user(user) or user
            self._save_users()
            return self._public_user(self._users[index])

    def login_or_register_linuxdo(self, profile: dict[str, object]) -> tuple[dict[str, object], str, bool]:
        linuxdo_id = self._clean(profile.get("provider_user_id"))
        if not linuxdo_id:
            raise ValueError("Linux DO user id is missing")
        username = self._clean(profile.get("username")) or f"linuxdo-{linuxdo_id}"
        display_name = self._clean(profile.get("display_name")) or username
        trust_level = max(0, int(profile.get("trust_level") or 0))
        with self._lock:
            index = self._find_user_index_by_linuxdo_id(linuxdo_id)
            now = _now_iso()
            if index >= 0:
                user = dict(self._users[index])
                if user.get("status") != "active":
                    raise ValueError("user is disabled")
                user.update(
                    {
                        "name": display_name,
                        "auth_provider": user.get("auth_provider") or "linuxdo",
                        "linuxdo_username": username,
                        "linuxdo_trust_level": trust_level,
                        "last_login_at": now,
                        "updated_at": now,
                    }
                )
                self._users[index] = self._normalize_user(user) or user
                token = self._create_session_locked(str(self._users[index]["id"]))
                self._save_users()
                self._save_sessions()
                return self._public_user(self._users[index]), token, False

            if not config.allow_user_registration:
                raise ValueError("registration is disabled")

            email = f"linuxdo-{linuxdo_id}@linuxdo.local"
            if self._find_user_index_by_email(email) >= 0:
                raise ValueError("Linux DO account is already linked")
            user = self._normalize_user(
                {
                    "id": uuid.uuid4().hex[:12],
                    "email": email,
                    "name": display_name,
                    "role": "user",
                    "status": "active",
                    "password_hash": "",
                    "quota": config.new_user_initial_quota,
                    "quota_used": 0,
                    "auth_provider": "linuxdo",
                    "linuxdo_id": linuxdo_id,
                    "linuxdo_username": username,
                    "linuxdo_trust_level": trust_level,
                    "created_at": now,
                    "updated_at": now,
                    "last_login_at": now,
                }
            )
            if user is None:
                raise ValueError("user payload is invalid")
            self._users.append(user)
            token = self._create_session_locked(str(user["id"]))
            self._save_users()
            self._save_sessions()
            return self._public_user(user), token, True

    def ensure_quota(self, user_id: str, amount: int) -> None:
        if amount <= 0:
            return
        with self._lock:
            index = self._find_user_index_by_id(self._clean(user_id))
            if index < 0:
                raise ValueError("user not found")
            user = self._users[index]
            if user.get("role") == "admin":
                return
            if int(user.get("quota") or 0) < amount:
                raise ValueError("insufficient image quota")

    def deduct_quota(self, user_id: str, amount: int) -> dict[str, object] | None:
        if amount <= 0:
            return self.get_user(user_id)
        request_id = f"legacy-deduct-{uuid.uuid4().hex}"
        self.reserve_quota(user_id, amount, request_id)
        self.confirm_quota(request_id)
        return self.get_user(user_id)

    def reserve_quota(
        self,
        user_id: str,
        amount: int,
        request_id: str,
        *,
        ttl_seconds: int = 900,
    ) -> dict[str, object] | None:
        normalized_user_id = self._clean(user_id)
        normalized_request_id = self._clean(request_id)
        normalized_amount = int(amount or 0)
        if normalized_amount <= 0:
            return None
        if not normalized_request_id:
            raise ValueError("request id is required")
        with self._lock:
            index = self._find_user_index_by_id(normalized_user_id)
            if index < 0:
                raise ValueError("user not found")
            user = self._users[index]
            if user.get("role") == "admin":
                return None
            if self.repositories is not None:
                reservation = self.repositories.quota_reservations.reserve(
                    normalized_user_id,
                    normalized_amount,
                    normalized_request_id,
                    ttl_seconds=ttl_seconds,
                )
                self._users = self._load_users()
                return reservation
            self._expire_quota_reservations_locked()
            existing = self._quota_reservations.get(normalized_request_id)
            if existing is not None:
                return dict(existing)
            user = dict(self._users[index])
            if int(user.get("quota") or 0) < normalized_amount:
                raise ValueError("insufficient image quota")
            now = _now()
            expires_at = now + timedelta(seconds=max(1, int(ttl_seconds or 900)))
            user["quota"] = max(0, int(user.get("quota") or 0) - normalized_amount)
            user["updated_at"] = now.isoformat()
            self._users[index] = self._normalize_user(user) or user
            reservation = {
                "id": uuid.uuid4().hex,
                "user_id": normalized_user_id,
                "request_id": normalized_request_id,
                "amount": normalized_amount,
                "status": "reserved",
                "created_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            }
            self._quota_reservations[normalized_request_id] = reservation
            self._save_users()
            return dict(reservation)

    def confirm_quota(self, request_id: str, amount: int | None = None) -> dict[str, object] | None:
        normalized_request_id = self._clean(request_id)
        if not normalized_request_id:
            return None
        with self._lock:
            if self.repositories is not None:
                reservation = self.repositories.quota_reservations.confirm(normalized_request_id, amount=amount)
                self._users = self._load_users()
                return reservation
            reservation = self._quota_reservations.get(normalized_request_id)
            if reservation is None:
                return None
            if reservation.get("status") != "reserved":
                return dict(reservation)
            reserved_amount = max(0, int(reservation.get("amount") or 0))
            confirmed_amount = reserved_amount if amount is None else max(0, min(int(amount or 0), reserved_amount))
            refund_amount = max(0, reserved_amount - confirmed_amount)
            index = self._find_user_index_by_id(self._clean(reservation.get("user_id")))
            if index >= 0:
                user = dict(self._users[index])
                user["quota"] = max(0, int(user.get("quota") or 0) + refund_amount)
                user["quota_used"] = int(user.get("quota_used") or 0) + confirmed_amount
                user["updated_at"] = _now_iso()
                self._users[index] = self._normalize_user(user) or user
                self._save_users()
            reservation.update(
                {
                    "status": "confirmed",
                    "confirmed_at": _now_iso(),
                    "confirmed_amount": confirmed_amount,
                    "released_amount": refund_amount,
                }
            )
            return dict(reservation)

    def release_quota(self, request_id: str) -> dict[str, object] | None:
        normalized_request_id = self._clean(request_id)
        if not normalized_request_id:
            return None
        with self._lock:
            if self.repositories is not None:
                reservation = self.repositories.quota_reservations.release(normalized_request_id)
                self._users = self._load_users()
                return reservation
            reservation = self._quota_reservations.get(normalized_request_id)
            if reservation is None:
                return None
            if reservation.get("status") != "reserved":
                return dict(reservation)
            index = self._find_user_index_by_id(self._clean(reservation.get("user_id")))
            if index >= 0:
                user = dict(self._users[index])
                user["quota"] = int(user.get("quota") or 0) + max(0, int(reservation.get("amount") or 0))
                user["updated_at"] = _now_iso()
                self._users[index] = self._normalize_user(user) or user
                self._save_users()
            reservation.update({"status": "released", "released_at": _now_iso()})
            return dict(reservation)

    def expire_quota_reservations(self) -> int:
        with self._lock:
            if self.repositories is not None:
                expired = self.repositories.quota_reservations.expire()
                if expired:
                    self._users = self._load_users()
                return expired
            return self._expire_quota_reservations_locked()

    def _expire_quota_reservations_locked(self) -> int:
        now = _now()
        expired = 0
        for reservation in list(self._quota_reservations.values()):
            if reservation.get("status") != "reserved":
                continue
            expires_at = _parse_time(reservation.get("expires_at"))
            if expires_at is None or expires_at > now:
                continue
            index = self._find_user_index_by_id(self._clean(reservation.get("user_id")))
            if index >= 0:
                user = dict(self._users[index])
                user["quota"] = int(user.get("quota") or 0) + max(0, int(reservation.get("amount") or 0))
                user["updated_at"] = now.isoformat()
                self._users[index] = self._normalize_user(user) or user
            reservation.update({"status": "expired", "expired_at": now.isoformat()})
            expired += 1
        if expired:
            self._save_users()
        return expired

    def list_redeem_codes(self, query: str = "", status: str = "") -> list[dict[str, object]]:
        query_text = self._clean(query).upper()
        status_text = self._clean(status).lower()
        with self._lock:
            if self.repositories is not None:
                self._redeem_codes = self._load_redeem_codes()
            items = []
            for item in self._redeem_codes:
                if query_text and query_text not in str(item.get("code") or ""):
                    continue
                if status_text and item.get("status") != status_text:
                    continue
                items.append(dict(item))
            items.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
            return items

    def create_redeem_codes(
        self,
        *,
        quota: int,
        count: int = 1,
        max_uses: int = 1,
        expires_at: str | None = None,
        created_by: str = "",
        note: str = "",
    ) -> list[dict[str, object]]:
        total = max(1, min(500, int(count or 1)))
        amount = max(1, int(quota or 1))
        uses = max(1, int(max_uses or 1))
        with self._lock:
            if self.repositories is not None:
                self._redeem_codes = self._load_redeem_codes()
            existing_codes = {str(item.get("code") or "") for item in self._redeem_codes}
            created: list[dict[str, object]] = []
            while len(created) < total:
                code = f"YAI-{secrets.token_urlsafe(9).replace('-', '').replace('_', '').upper()[:12]}"
                if code in existing_codes:
                    continue
                item = self._normalize_redeem_code({
                    "id": uuid.uuid4().hex[:12],
                    "code": code,
                    "quota": amount,
                    "status": "enabled",
                    "max_uses": uses,
                    "used_count": 0,
                    "used_by": [],
                    "expires_at": expires_at,
                    "created_at": _now_iso(),
                    "created_by": created_by,
                    "note": note,
                })
                if item is None:
                    continue
                existing_codes.add(code)
                created.append(item)
            if self.repositories is not None:
                for item in reversed(created):
                    self.repositories.redeem_codes.upsert(dict(item))
                self._redeem_codes = self._load_redeem_codes()
            else:
                self._redeem_codes = [*created, *self._redeem_codes]
                self._save_redeem_codes()
            return [dict(item) for item in created]

    def update_redeem_code(self, code_id: str, updates: dict[str, object]) -> dict[str, object] | None:
        normalized_id = self._clean(code_id)
        with self._lock:
            if self.repositories is not None:
                self._redeem_codes = self._load_redeem_codes()
            for index, item in enumerate(self._redeem_codes):
                if item.get("id") != normalized_id:
                    continue
                next_item = dict(item)
                for key in ("status", "expires_at", "note", "max_uses", "quota"):
                    if key in updates and updates.get(key) is not None:
                        next_item[key] = updates.get(key)
                normalized = self._normalize_redeem_code(next_item)
                if normalized is None:
                    return None
                if self.repositories is not None:
                    self.repositories.redeem_codes.upsert(dict(normalized))
                    self._redeem_codes = self._load_redeem_codes()
                else:
                    self._redeem_codes[index] = normalized
                    self._save_redeem_codes()
                return dict(normalized)
        return None

    def delete_redeem_codes(self, code_ids: list[str]) -> int:
        normalized_ids = {self._clean(code_id) for code_id in code_ids}
        normalized_ids.discard("")
        if not normalized_ids:
            return 0
        with self._lock:
            if self.repositories is not None:
                removed = 0
                for code_id in normalized_ids:
                    if self.repositories.redeem_codes.delete(code_id):
                        removed += 1
                if removed:
                    self._redeem_codes = self._load_redeem_codes()
                return removed
            before = len(self._redeem_codes)
            self._redeem_codes = [
                item
                for item in self._redeem_codes
                if self._clean(item.get("id")) not in normalized_ids
            ]
            removed = before - len(self._redeem_codes)
            if removed <= 0:
                return 0
            self._save_redeem_codes()
            return removed

    def redeem_code(self, user_id: str, raw_code: str) -> tuple[dict[str, object], dict[str, object]]:
        code = self._clean(raw_code).upper()
        if not code:
            raise ValueError("redeem code is required")
        with self._lock:
            if self.repositories is not None:
                user, item = self.repositories.redeem_codes.redeem(self._clean(user_id), code)
                self._users = self._load_users()
                self._redeem_codes = self._load_redeem_codes()
                return self._public_user(self._normalize_user(user) or user), dict(item)
            user_index = self._find_user_index_by_id(self._clean(user_id))
            if user_index < 0:
                raise ValueError("user not found")
            user = self._users[user_index]
            code_index = next((index for index, item in enumerate(self._redeem_codes) if item.get("code") == code), -1)
            if code_index < 0:
                raise ValueError("redeem code is invalid")
            item = dict(self._redeem_codes[code_index])
            if item.get("status") != "enabled":
                raise ValueError("redeem code is disabled")
            expires_at = _parse_time(item.get("expires_at"))
            if expires_at and expires_at < _now():
                raise ValueError("redeem code is expired")
            max_uses = int(item.get("max_uses") or 1)
            used_by = list(item.get("used_by") or [])
            if int(item.get("used_count") or len(used_by)) >= max_uses:
                raise ValueError("redeem code has been used")
            if any(entry.get("user_id") == user_id for entry in used_by if isinstance(entry, dict)):
                raise ValueError("redeem code has already been used by this user")
            quota = int(item.get("quota") or 0)
            used_by.append({
                "user_id": user.get("id"),
                "email": user.get("email"),
                "quota": quota,
                "used_at": _now_iso(),
            })
            item["used_by"] = used_by
            item["used_count"] = len(used_by)
            if item["used_count"] >= max_uses:
                item["status"] = "disabled"
            next_user = dict(user)
            next_user["quota"] = int(next_user.get("quota") or 0) + quota
            next_user["updated_at"] = _now_iso()
            self._users[user_index] = self._normalize_user(next_user) or next_user
            self._redeem_codes[code_index] = self._normalize_redeem_code(item) or item
            self._save_users()
            self._save_redeem_codes()
            return self._public_user(self._users[user_index]), dict(self._redeem_codes[code_index])

    def authenticate(self, raw_key: str) -> dict[str, object] | None:
        candidate = self._clean(raw_key)
        if not candidate:
            return None
        candidate_hash = _hash_key(candidate)
        now = _now()
        with self._lock:
            for index, item in enumerate(self._items):
                if not bool(item.get("enabled", True)):
                    continue
                stored_hash = self._clean(item.get("key_hash"))
                if not stored_hash or not hmac.compare_digest(stored_hash, candidate_hash):
                    continue
                next_item = dict(item)
                next_item["last_used_at"] = now.isoformat()
                self._items[index] = next_item
                item_id = self._clean(next_item.get("id"))
                last_flush_at = self._last_used_flush_at.get(item_id)
                if last_flush_at is None or (now - last_flush_at).total_seconds() >= 60:
                    try:
                        self._save_keys()
                        self._last_used_flush_at[item_id] = now
                    except Exception:
                        pass
                return self._public_item(next_item)

            for index, session in enumerate(self._sessions):
                if not hmac.compare_digest(self._clean(session.get("token_hash")), candidate_hash):
                    continue
                expires_at = _parse_time(session.get("expires_at"))
                if expires_at and expires_at < now:
                    self._sessions.pop(index)
                    self._save_sessions()
                    return None
                user_index = self._find_user_index_by_id(self._clean(session.get("user_id")))
                if user_index < 0:
                    return None
                user = self._users[user_index]
                if user.get("status") != "active":
                    return None
                next_session = dict(session)
                next_session["last_used_at"] = now.isoformat()
                self._sessions[index] = next_session
                session_id = self._clean(next_session.get("id"))
                last_flush_at = self._last_used_flush_at.get(session_id)
                if last_flush_at is None or (now - last_flush_at).total_seconds() >= 60:
                    try:
                        self._save_sessions()
                        self._last_used_flush_at[session_id] = now
                    except Exception:
                        pass
                return self._public_user(user)
        return None


auth_service = AuthService(config.get_repository_provider() or config.get_storage_backend())
