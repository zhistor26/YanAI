from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from api.support import require_admin, require_identity, resolve_image_base_url
from services.auth_service import DEFAULT_USER_IMAGE_CHANNEL_MODELS, auth_service
from services.channel_service import channel_service
from services.config import config
from services.image_service import collect_downloadable_images, delete_images, list_images
from services.log_service import audit_service
from services.model_service import model_service
from services import linuxdo_oauth_service
from services.registration_security import send_registration_verification_code, validate_registration_email
from services.webdav_service import get_webdav_config, save_webdav_config, sync_images_to_webdav
from utils.model_catalog import DEFAULT_INTERNAL_MODELS


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    verification_code: str = ""


class VerificationCodeRequest(BaseModel):
    email: str


class ProfileUpdateRequest(BaseModel):
    name: str | None = None


class UserImageChannelRequest(BaseModel):
    enabled: bool = False
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    models: list[str] | str = Field(default_factory=lambda: list(DEFAULT_USER_IMAGE_CHANNEL_MODELS))
    timeout: int = 60


class UserImageChannelTestRequest(UserImageChannelRequest):
    test_models: list[str] = Field(default_factory=list)


class WebDAVConfigRequest(BaseModel):
    enabled: bool = False
    url: str = ""
    username: str = ""
    password: str = ""
    root_path: str = ""


class ImageSelectionItem(BaseModel):
    id: str = ""
    record_id: str = ""
    url: str = ""


class ImageSelectionRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    items: list[ImageSelectionItem] = Field(default_factory=list)


class MyImagesWebDAVSyncRequest(BaseModel):
    start_date: str = ""
    end_date: str = ""
    ids: list[str] = Field(default_factory=list)


class RedeemRequest(BaseModel):
    code: str


class AdminUserCreateRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    quota: int = 0
    status: str = "active"


class AdminUserUpdateRequest(BaseModel):
    email: str | None = None
    name: str | None = None
    status: str | None = None
    quota: int | None = None


class AdminUserQuotaRequest(BaseModel):
    amount: int
    mode: str = "add"


class ResetPasswordRequest(BaseModel):
    password: str = ""


class IdsDeleteRequest(BaseModel):
    ids: list[str] = Field(default_factory=list)


class RedeemCodeCreateRequest(BaseModel):
    quota: int = Field(default=1, ge=1)
    count: int = Field(default=1, ge=1, le=500)
    max_uses: int = Field(default=1, ge=1)
    expires_at: str | None = None
    note: str = ""


class RedeemCodeUpdateRequest(BaseModel):
    status: str | None = None
    quota: int | None = None
    max_uses: int | None = None
    expires_at: str | None = None
    note: str | None = None


class ChannelRequest(BaseModel):
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    models: list[str] | str = Field(default_factory=lambda: list(DEFAULT_INTERNAL_MODELS))
    weight: int = 1
    priority: int = 0
    timeout: int = 60
    enabled: bool = True


class ChannelUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    models: list[str] | str | None = None
    weight: int | None = None
    priority: int | None = None
    timeout: int | None = None
    enabled: bool | None = None


class ChannelModelTestRequest(BaseModel):
    models: list[str] = Field(default_factory=list)


class ModelPricingRequest(BaseModel):
    model: str = ""
    enabled: bool | None = None
    billing_mode: str | None = None
    currency: str | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    model_ratio: float | None = None
    completion_ratio: float | None = None
    model_price: float | None = None
    note: str | None = None


def _selection_targets(body: ImageSelectionRequest) -> tuple[list[str], list[str]]:
    record_ids = [
        *body.ids,
        *[item.record_id or item.id for item in body.items if item.record_id or item.id],
    ]
    urls = [
        *body.urls,
        *[item.url for item in body.items if item.url],
    ]
    return record_ids, urls


def _build_image_download_zip(downloads: list[dict[str, object]]) -> bytes:
    archive = BytesIO()
    with ZipFile(archive, mode="w", compression=ZIP_DEFLATED) as zip_file:
        for item in downloads:
            path = item.get("path")
            if not isinstance(path, Path):
                continue
            zip_file.write(path, arcname=str(item.get("name") or path.name))
    return archive.getvalue()


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/auth/register/options")
    async def register_options(request: Request):
        linuxdo_ready = (
            config.linuxdo_oauth_enabled
            and bool(config.linuxdo_client_id)
            and bool(config.linuxdo_client_secret)
        )
        return {
            "allow_user_registration": config.allow_user_registration,
            "email_verification_enabled": config.email_verification_enabled,
            "email_domain_whitelist_enabled": config.email_domain_whitelist_enabled,
            "email_alias_restriction_enabled": config.email_alias_restriction_enabled,
            "email_domain_whitelist": config.email_domain_whitelist if config.email_domain_whitelist_enabled else [],
            "linuxdo_oauth_enabled": linuxdo_ready,
            "linuxdo_minimum_trust_level": config.linuxdo_minimum_trust_level,
            "linuxdo_start_url": "/auth/linuxdo/start",
            "linuxdo_callback_url": linuxdo_oauth_service.callback_url(request),
        }

    @router.post("/auth/register/email-code")
    async def send_register_email_code(body: VerificationCodeRequest):
        if not config.allow_user_registration:
            raise HTTPException(status_code=400, detail={"error": "registration is disabled"})
        try:
            email = validate_registration_email(body.email)
            if auth_service.email_exists(email):
                raise ValueError("email already exists")
            if config.email_verification_enabled:
                send_registration_verification_code(email)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail={"error": f"failed to send verification email: {exc}"}) from exc
        return {"ok": True, "required": config.email_verification_enabled}

    @router.post("/auth/register")
    async def register(body: RegisterRequest):
        try:
            user, token = auth_service.register_user(
                email=body.email,
                password=body.password,
                name=body.name,
                verification_code=body.verification_code,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"ok": True, "user": user, "token": token}

    @router.get("/auth/linuxdo/start")
    async def linuxdo_start(request: Request):
        try:
            url = linuxdo_oauth_service.authorization_url(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return RedirectResponse(url, status_code=303)

    @router.get("/oauth/linuxdo")
    async def linuxdo_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        def redirect_with(payload: dict[str, object]) -> RedirectResponse:
            return RedirectResponse(
                f"{linuxdo_oauth_service.frontend_callback_url(request)}#{urlencode(payload)}",
                status_code=303,
            )

        if error:
            return redirect_with({"error": error})
        try:
            profile = linuxdo_oauth_service.authenticate_callback(request, code, state)
            user, token, created = auth_service.login_or_register_linuxdo(profile)
        except ValueError as exc:
            return redirect_with({"error": str(exc)})
        return redirect_with(
            {
                "token": token,
                "role": user.get("role") or "user",
                "subject_id": user.get("id") or "",
                "name": user.get("name") or "",
                "email": user.get("email") or "",
                "quota": user.get("quota") or 0,
                "created": "1" if created else "0",
            }
        )

    @router.get("/api/me")
    async def get_me(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") == "user":
            user = auth_service.get_user(str(identity.get("id") or ""))
            if user is not None:
                return {"user": user}
        return {"user": identity}

    @router.post("/api/me/profile")
    async def update_profile(body: ProfileUpdateRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            return {"user": identity}
        try:
            user = auth_service.update_user(str(identity.get("id") or ""), body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if user is None:
            raise HTTPException(status_code=404, detail={"error": "user not found"})
        return {"user": user}

    @router.get("/api/me/image-channel")
    async def get_my_image_channel(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        try:
            channel = auth_service.get_user_image_channel_config(str(identity.get("id") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail={"error": str(exc)}) from exc
        return {"channel": channel}

    @router.post("/api/me/image-channel")
    async def save_my_image_channel(body: UserImageChannelRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        user_id = str(identity.get("id") or "")
        try:
            channel = auth_service.save_user_image_channel_config(user_id, body.model_dump(mode="python"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        audit_service.add(
            actor=identity,
            action="me.image_channel.update",
            resource="image_channel",
            target_id=user_id,
            detail={
                "enabled": channel.get("enabled"),
                "name": channel.get("name"),
                "base_url": channel.get("base_url"),
                "models": channel.get("models"),
                "timeout": channel.get("timeout"),
                "has_api_key": channel.get("has_api_key"),
            },
        )
        return {"channel": channel, "user": auth_service.get_user(user_id)}

    @router.post("/api/me/image-channel/models/test")
    async def test_my_image_channel_models(
            body: UserImageChannelTestRequest | None = None,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        user_id = str(identity.get("id") or "")
        updates = {} if body is None else body.model_dump(exclude={"test_models"}, mode="python")
        selected_models = [] if body is None else body.test_models
        try:
            channel_config = auth_service.merge_user_image_channel_config(
                user_id,
                updates,
                include_api_key=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        result = await run_in_threadpool(
            channel_service.test_personal_channel_models,
            channel_config,
            selected_models,
            owner_user_id=user_id,
        )
        return result

    @router.post("/api/me/redeem")
    async def redeem(body: RedeemRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        try:
            user, code = auth_service.redeem_code(str(identity.get("id") or ""), body.code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"user": user, "redeem_code": code}

    @router.get("/api/me/images")
    async def get_my_images(
            request: Request,
            start_date: str = "",
            end_date: str = "",
            page: int = 1,
            page_size: int = 48,
            authorization: str | None = Header(default=None),
    ):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        return list_images(
            resolve_image_base_url(request),
            start_date=start_date.strip(),
            end_date=end_date.strip(),
            owner_user_id=str(identity.get("id") or ""),
            page=page,
            page_size=page_size,
        )

    @router.delete("/api/me/images")
    async def delete_my_images(body: ImageSelectionRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        record_ids, urls = _selection_targets(body)
        if not any(str(value or "").strip() for value in [*record_ids, *urls]):
            raise HTTPException(status_code=400, detail={"error": "image ids or urls are required"})
        result = delete_images(
            record_ids=record_ids,
            urls=urls,
            owner_user_id=str(identity.get("id") or ""),
        )
        audit_service.add(
            actor=identity,
            action="me.images.delete",
            resource="image",
            target_id=",".join(str(value) for value in record_ids[:5] if str(value).strip()),
            detail={
                "requested": len(body.items) or len(record_ids) or len(urls),
                **result,
            },
        )
        return result

    @router.post("/api/me/images/download")
    async def download_my_images(body: ImageSelectionRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        record_ids, urls = _selection_targets(body)
        if not any(str(value or "").strip() for value in [*record_ids, *urls]):
            raise HTTPException(status_code=400, detail={"error": "image ids or urls are required"})
        downloads = await run_in_threadpool(
            collect_downloadable_images,
            record_ids=record_ids,
            urls=urls,
            owner_user_id=str(identity.get("id") or ""),
        )
        if not downloads:
            raise HTTPException(status_code=404, detail={"error": "no downloadable local image files found"})
        content = await run_in_threadpool(_build_image_download_zip, downloads)
        filename = f"my-images-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        return StreamingResponse(
            iter([content]),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/api/me/images/webdav")
    async def get_my_images_webdav(authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        return {"webdav": get_webdav_config("user", user_id=str(identity.get("id") or ""))}

    @router.post("/api/me/images/webdav")
    async def save_my_images_webdav(body: WebDAVConfigRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        try:
            webdav = save_webdav_config("user", body.model_dump(mode="python"), user_id=str(identity.get("id") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"webdav": webdav}

    @router.post("/api/me/images/webdav/sync")
    async def sync_my_images_webdav(body: MyImagesWebDAVSyncRequest, authorization: str | None = Header(default=None)):
        identity = require_identity(authorization)
        if identity.get("role") != "user":
            raise HTTPException(status_code=403, detail={"error": "user permission required"})
        try:
            result = await run_in_threadpool(
                sync_images_to_webdav,
                scope="user",
                identity=identity,
                filters={
                    "start_date": body.start_date.strip(),
                    "end_date": body.end_date.strip(),
                    "record_ids": [item.strip() for item in body.ids if item.strip()],
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"result": result}

    @router.get("/api/admin/users")
    async def admin_list_users(
            query: str = "",
            status: str = "",
            role: str = "",
            authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        return {"items": auth_service.list_users(query=query, status=status, role=role)}

    @router.post("/api/admin/users")
    async def admin_create_user(body: AdminUserCreateRequest, authorization: str | None = Header(default=None)):
        admin = require_admin(authorization)
        try:
            user, password_or_token = auth_service.create_user(
                email=body.email,
                password=body.password,
                name=body.name,
                quota=body.quota,
                status=body.status,
                role="user",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        audit_service.add(
            actor=admin,
            action="users.create",
            resource="user",
            target_id=str(user.get("id") or ""),
            detail={"email": user.get("email"), "quota": user.get("quota"), "status": user.get("status")},
        )
        return {"item": user, "password": body.password, "session_token": password_or_token, "items": auth_service.list_users()}

    @router.post("/api/admin/users/{user_id}")
    async def admin_update_user(user_id: str, body: AdminUserUpdateRequest, authorization: str | None = Header(default=None)):
        admin = require_admin(authorization)
        updates = body.model_dump(exclude_unset=True, exclude_none=True)
        before = auth_service.get_user(user_id)
        try:
            user = auth_service.update_user(user_id, updates)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if user is None:
            raise HTTPException(status_code=404, detail={"error": "user not found"})
        if updates:
            audit_service.add(
                actor=admin,
                action="users.update",
                resource="user",
                target_id=user_id,
                detail={
                    "updates": updates,
                    "previous_quota": (before or {}).get("quota"),
                    "current_quota": user.get("quota"),
                },
            )
        return {"item": user, "items": auth_service.list_users()}

    @router.delete("/api/admin/users/{user_id}")
    async def admin_delete_user(user_id: str, authorization: str | None = Header(default=None)):
        admin = require_admin(authorization)
        if not auth_service.delete_user(user_id):
            raise HTTPException(status_code=404, detail={"error": "user not found"})
        audit_service.add(actor=admin, action="users.delete", resource="user", target_id=user_id)
        return {"items": auth_service.list_users()}

    @router.delete("/api/admin/users")
    async def admin_delete_users(body: IdsDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if not body.ids:
            raise HTTPException(status_code=400, detail={"error": "ids are required"})
        removed = auth_service.delete_users(body.ids)
        if removed <= 0:
            raise HTTPException(status_code=404, detail={"error": "users not found"})
        return {"items": auth_service.list_users(), "removed": removed}

    @router.post("/api/admin/users/{user_id}/quota")
    async def admin_update_user_quota(user_id: str, body: AdminUserQuotaRequest, authorization: str | None = Header(default=None)):
        admin = require_admin(authorization)
        before = auth_service.get_user(user_id)
        user = auth_service.adjust_user_quota(user_id, body.amount, body.mode)
        if user is None:
            raise HTTPException(status_code=404, detail={"error": "user not found"})
        audit_service.add(
            actor=admin,
            action="users.quota.adjust",
            resource="user",
            target_id=user_id,
            detail={
                "mode": body.mode,
                "amount": body.amount,
                "previous_quota": (before or {}).get("quota"),
                "current_quota": user.get("quota"),
            },
        )
        return {"item": user, "items": auth_service.list_users()}

    @router.post("/api/admin/users/{user_id}/reset-password")
    async def admin_reset_password(user_id: str, body: ResetPasswordRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            result = auth_service.reset_password(user_id, body.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "user not found"})
        user, password = result
        return {"item": user, "password": password}

    @router.get("/api/admin/redeem-codes")
    async def admin_list_redeem_codes(query: str = "", status: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": auth_service.list_redeem_codes(query=query, status=status)}

    @router.post("/api/admin/redeem-codes")
    async def admin_create_redeem_code(body: RedeemCodeCreateRequest, authorization: str | None = Header(default=None)):
        admin = require_admin(authorization)
        items = auth_service.create_redeem_codes(
            quota=body.quota,
            count=1,
            max_uses=body.max_uses,
            expires_at=body.expires_at,
            created_by=str(admin.get("id") or ""),
            note=body.note,
        )
        return {"items": auth_service.list_redeem_codes(), "created": items}

    @router.post("/api/admin/redeem-codes/batch")
    async def admin_create_redeem_code_batch(body: RedeemCodeCreateRequest, authorization: str | None = Header(default=None)):
        admin = require_admin(authorization)
        created = auth_service.create_redeem_codes(
            quota=body.quota,
            count=body.count,
            max_uses=body.max_uses,
            expires_at=body.expires_at,
            created_by=str(admin.get("id") or ""),
            note=body.note,
        )
        return {"items": auth_service.list_redeem_codes(), "created": created}

    @router.post("/api/admin/redeem-codes/{code_id}")
    async def admin_update_redeem_code(code_id: str, body: RedeemCodeUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        item = auth_service.update_redeem_code(code_id, body.model_dump(exclude_none=True))
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "redeem code not found"})
        return {"item": item, "items": auth_service.list_redeem_codes()}

    @router.delete("/api/admin/redeem-codes")
    async def admin_delete_redeem_codes(body: IdsDeleteRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if not body.ids:
            raise HTTPException(status_code=400, detail={"error": "ids are required"})
        removed = auth_service.delete_redeem_codes(body.ids)
        if removed <= 0:
            raise HTTPException(status_code=404, detail={"error": "redeem codes not found"})
        return {"items": auth_service.list_redeem_codes(), "removed": removed}

    @router.get("/api/admin/channels")
    async def admin_list_channels(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": channel_service.list_channels()}

    @router.get("/api/admin/models")
    async def admin_list_models(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return model_service.list_catalog()

    @router.post("/api/admin/models/pricing")
    async def admin_update_model_pricing(body: ModelPricingRequest, authorization: str | None = Header(default=None)):
        admin = require_admin(authorization)
        try:
            item = model_service.update_pricing(
                body.model,
                body.model_dump(exclude={"model"}, exclude_none=True, mode="python"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        audit_service.add(
            actor=admin,
            action="models.pricing.update",
            resource="model",
            target_id=str(item.get("model") or ""),
            detail={"pricing": item},
        )
        return {"item": item, **model_service.list_catalog()}

    @router.post("/api/admin/channels")
    async def admin_create_channel(body: ChannelRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = channel_service.create_channel(body.model_dump(mode="python"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"item": item, "items": channel_service.list_channels()}

    @router.post("/api/admin/channels/{channel_id}")
    async def admin_update_channel(channel_id: str, body: ChannelUpdateRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            item = channel_service.update_channel(channel_id, body.model_dump(exclude_none=True, mode="python"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "channel not found"})
        return {"item": item, "items": channel_service.list_channels()}

    @router.post("/api/admin/channels/{channel_id}/models/refresh")
    async def admin_refresh_channel_models(channel_id: str, authorization: str | None = Header(default=None)):
        admin = require_admin(authorization)
        try:
            result = await run_in_threadpool(model_service.refresh_channel_models, channel_id)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "channel not found"})
        audit_service.add(
            actor=admin,
            action="channels.models.refresh",
            resource="channel",
            target_id=channel_id,
            detail={"models": result.get("models"), "channel": result.get("channel")},
        )
        return {**result, **model_service.list_catalog()}

    @router.post("/api/admin/channels/{channel_id}/models/test")
    async def admin_test_channel_models(
            channel_id: str,
            body: ChannelModelTestRequest | None = None,
            authorization: str | None = Header(default=None),
    ):
        admin = require_admin(authorization)
        selected_models = [] if body is None else body.models
        result = await run_in_threadpool(channel_service.test_channel_models, channel_id, selected_models)
        if result is None:
            raise HTTPException(status_code=404, detail={"error": "channel not found"})
        audit_service.add(
            actor=admin,
            action="channels.models.test",
            resource="channel",
            target_id=channel_id,
            detail={
                "ok": result.get("ok"),
                "model_count": result.get("model_count"),
                "tested_models": result.get("tested_models"),
                "missing_models": result.get("missing_models"),
                "latency_ms": result.get("latency_ms"),
                "error": result.get("error"),
            },
        )
        return result

    @router.delete("/api/admin/channels/{channel_id}")
    async def admin_delete_channel(channel_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if not channel_service.delete_channel(channel_id):
            raise HTTPException(status_code=404, detail={"error": "channel not found"})
        return {"items": channel_service.list_channels()}

    return router
