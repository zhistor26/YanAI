import base64
import os
import random
import re
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

from curl_cffi import requests
from PIL import Image

from services.account_service import account_service
from services.config import config
from services.proxy_service import proxy_settings
from utils.helper import UpstreamHTTPError, ensure_ok, iter_sse_payloads, new_uuid
from utils.log import logger
from utils.pow import build_legacy_requirements_token, build_proof_token, parse_pow_resources
from utils.turnstile import solve_turnstile_token


@dataclass
class ChatRequirements:
    """保存一次对话请求所需的 sentinel token。"""
    token: str
    proof_token: str = ""
    turnstile_token: str = ""
    so_token: str = ""
    raw_finalize: Optional[Dict[str, Any]] = None


DEFAULT_CLIENT_VERSION = "prod-be885abbfcfe7b1f511e88b3003d9ee44757fbad"
DEFAULT_CLIENT_BUILD_NUMBER = "5955942"
DEFAULT_POW_SCRIPT = "https://chatgpt.com/backend-api/sentinel/sdk.js"
CODEX_IMAGE_MODEL = "codex-gpt-image-2"
FILE_SERVICE_ID_RE = re.compile(r"file-service://([A-Za-z0-9_-]+)")
REAL_IMAGE_FILE_ID_RE = re.compile(r"\bfile_00000000[a-f0-9]{24}\b")
SEDIMENT_ID_RE = re.compile(r"sediment://([A-Za-z0-9_-]+)")
_CONTENT_POLICY_KEYWORDS = (
    "内容政策",
    "防护限制",
    "违反",
    "moderation",
    "policy",
    "blocked",
    "不能生成",
    "无法生成",
    "不能帮助",
    "无法帮助",
    "裸体",
    "裸露",
    "色情",
    "性内容",
    "未成年",
    "抱歉，我不能",
)


class ImagePollTimeoutError(RuntimeError):
    pass


class ImageContentPolicyError(RuntimeError):
    pass


def _is_content_policy_error(error_msg: str) -> bool:
    if not error_msg:
        return False
    lowered = error_msg.lower()
    return any(keyword in lowered for keyword in _CONTENT_POLICY_KEYWORDS)


class OpenAIBackendAPI:
    """ChatGPT Web 后端封装。

    说明：
    - 传入 `access_token` 时，聊天和模型列表都会走已登录链路
      例如 `/backend-api/sentinel/chat-requirements`、`/backend-api/conversation`
    - 不传 `access_token` 时，会走未登录链路
      例如 `/backend-anon/sentinel/chat-requirements`、`/backend-anon/conversation`
    - `stream_conversation()` 是底层统一流式入口
    - 协议兼容转换放在 `services.protocol`
    """

    def __init__(self, access_token: str = "") -> None:
        """初始化后端客户端。

        参数：
        - `access_token`：可选。传入后表示使用已登录链路；不传则使用未登录链路。
        """
        self.base_url = "https://chatgpt.com"
        self.client_version = DEFAULT_CLIENT_VERSION
        self.client_build_number = DEFAULT_CLIENT_BUILD_NUMBER
        self.access_token = access_token
        self.fp = self._build_fp()
        self.user_agent = self.fp["user-agent"]
        self.device_id = self.fp["oai-device-id"]
        self.session_id = self.fp["oai-session-id"]
        self.pow_script_sources: list[str] = []
        self.pow_data_build = ""
        self.session = requests.Session(**proxy_settings.build_session_kwargs(
            impersonate=self.fp["impersonate"],
            verify=True,
        ))
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Priority": "u=1, i",
            "Sec-Ch-Ua": self.fp["sec-ch-ua"],
            "Sec-Ch-Ua-Arch": '"x86"',
            "Sec-Ch-Ua-Bitness": '"64"',
            "Sec-Ch-Ua-Full-Version": '"143.0.3650.96"',
            "Sec-Ch-Ua-Full-Version-List": '"Microsoft Edge";v="143.0.3650.96", "Chromium";v="143.0.7499.147", "Not A(Brand";v="24.0.0.0"',
            "Sec-Ch-Ua-Mobile": self.fp["sec-ch-ua-mobile"],
            "Sec-Ch-Ua-Model": '""',
            "Sec-Ch-Ua-Platform": self.fp["sec-ch-ua-platform"],
            "Sec-Ch-Ua-Platform-Version": '"19.0.0"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "OAI-Device-Id": self.device_id,
            "OAI-Session-Id": self.session_id,
            "OAI-Language": "zh-CN",
            "OAI-Client-Version": self.client_version,
            "OAI-Client-Build-Number": self.client_build_number,
        })
        if self.access_token:
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"

    def _build_fp(self) -> Dict[str, str]:
        account = account_service.get_account(self.access_token) if self.access_token else {}
        account = account if isinstance(account, dict) else {}
        raw_fp = account.get("fp")
        fp = {str(k).lower(): str(v) for k, v in raw_fp.items()} if isinstance(raw_fp, dict) else {}
        for key in (
                "user-agent",
                "impersonate",
                "oai-device-id",
                "oai-session-id",
                "sec-ch-ua",
                "sec-ch-ua-mobile",
                "sec-ch-ua-platform",
        ):
            value = str(account.get(key) or "").strip()
            if value:
                fp[key] = value
        fp.setdefault(
            "user-agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0",
        )
        fp.setdefault("impersonate", "edge101")
        fp.setdefault("oai-device-id", new_uuid())
        fp.setdefault("oai-session-id", new_uuid())
        fp.setdefault("sec-ch-ua", '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"')
        fp.setdefault("sec-ch-ua-mobile", "?0")
        fp.setdefault("sec-ch-ua-platform", '"Windows"')
        return fp

    def _headers(self, path: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """构造请求头，并补上 web 端要求的 target path/route。"""
        headers = dict(self.session.headers)
        headers["X-OpenAI-Target-Path"] = path
        headers["X-OpenAI-Target-Route"] = path
        if extra:
            headers.update(extra)
        return headers

    def _bootstrap_headers(self) -> Dict[str, str]:
        """构造首页预热请求头。"""
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Sec-Ch-Ua": self.session.headers["Sec-Ch-Ua"],
            "Sec-Ch-Ua-Mobile": self.session.headers["Sec-Ch-Ua-Mobile"],
            "Sec-Ch-Ua-Platform": self.session.headers["Sec-Ch-Ua-Platform"],
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def _build_requirements(self, data: Dict[str, Any], source_p: str = "") -> ChatRequirements:
        """把 sentinel 响应整理成后续对话需要的 token 集合。"""
        if (data.get("arkose") or {}).get("required"):
            raise RuntimeError("chat requirements requires arkose token, which is not implemented")

        proof_token = ""
        proof_info = data.get("proofofwork") or {}
        if proof_info.get("required"):
            proof_token = build_proof_token(
                proof_info.get("seed", ""),
                proof_info.get("difficulty", ""),
                self.user_agent,
                script_sources=self.pow_script_sources,
                data_build=self.pow_data_build,
            )

        turnstile_token = ""
        turnstile_info = data.get("turnstile") or {}
        if turnstile_info.get("required") and turnstile_info.get("dx"):
            turnstile_token = solve_turnstile_token(turnstile_info["dx"], source_p) or ""

        return ChatRequirements(
            token=data.get("token", ""),
            proof_token=proof_token,
            turnstile_token=turnstile_token,
            so_token=data.get("so_token", ""),
            raw_finalize=data,
        )

    def _conversation_headers(self, path: str, requirements: ChatRequirements) -> Dict[str, str]:
        """根据当前 requirements 构造对话 SSE 请求头。"""
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "OpenAI-Sentinel-Chat-Requirements-Token": requirements.token,
        }
        if requirements.proof_token:
            headers["OpenAI-Sentinel-Proof-Token"] = requirements.proof_token
        if requirements.turnstile_token:
            headers["OpenAI-Sentinel-Turnstile-Token"] = requirements.turnstile_token
        if requirements.so_token:
            headers["OpenAI-Sentinel-SO-Token"] = requirements.so_token
        return self._headers(path, headers)

    @staticmethod
    def _content_block_text(block: Any) -> str:
        if isinstance(block, str):
            return block
        if not isinstance(block, dict):
            return ""
        if str(block.get("type") or "").strip() not in {"text", "input_text", "output_text"}:
            return ""
        return str(block.get("text") or block.get("input_text") or "")

    @staticmethod
    def _content_block_image_ref(block: Any) -> str:
        if not isinstance(block, dict):
            return ""
        if str(block.get("type") or "").strip() not in {"image_url", "input_image"}:
            return ""
        image_url = block.get("image_url") or block.get("url")
        if isinstance(image_url, dict):
            image_url = image_url.get("url") or image_url.get("image_url")
        image_ref = str(image_url or "").strip()
        if image_ref:
            return image_ref
        file_id = str(block.get("file_id") or "").strip()
        return f"file-service://{file_id}" if file_id else ""

    @staticmethod
    def _content_block_file_name(block: Any, index: int) -> str:
        if isinstance(block, dict):
            for key in ("file_name", "filename", "name"):
                value = str(block.get(key) or "").strip()
                if value:
                    return value
        return f"image_{index}.png"

    @staticmethod
    def _image_asset_part(item: Dict[str, Any]) -> Dict[str, Any]:
        part: Dict[str, Any] = {
            "content_type": "image_asset_pointer",
            "asset_pointer": f"file-service://{item['file_id']}",
        }
        for source_key, target_key in (("width", "width"), ("height", "height"), ("file_size", "size_bytes")):
            try:
                value = int(item.get(source_key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                part[target_key] = value
        return part

    @staticmethod
    def _attachment_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
        attachment: Dict[str, Any] = {
            "id": item["file_id"],
            "mimeType": item.get("mime_type") or "image/png",
            "name": item.get("file_name") or "image.png",
        }
        for key in ("file_size", "width", "height"):
            try:
                value = int(item.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                attachment["size" if key == "file_size" else key] = value
        return attachment

    def _uploaded_image_from_ref(self, image_ref: str, block: Any, index: int) -> Dict[str, Any]:
        if image_ref.startswith("file-service://"):
            block_data = block if isinstance(block, dict) else {}
            return {
                "file_id": image_ref.removeprefix("file-service://"),
                "file_name": self._content_block_file_name(block, index),
                "file_size": (
                    block_data.get("file_size")
                    or block_data.get("size")
                    or block_data.get("size_bytes")
                    or 0
                ),
                "mime_type": block_data.get("mime_type") or block_data.get("mimeType") or "image/png",
                "width": block_data.get("width") or 0,
                "height": block_data.get("height") or 0,
            }
        return self._upload_image(image_ref, self._content_block_file_name(block, index))

    def _conversation_content_from_api_content(self, content: Any) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if isinstance(content, str):
            return {"content_type": "text", "parts": [content]}, {}
        if not isinstance(content, list):
            return {"content_type": "text", "parts": [str(content or "")]}, {}

        parts: list[Any] = []
        attachments: list[Dict[str, Any]] = []
        image_index = 1
        for block in content:
            image_ref = self._content_block_image_ref(block)
            if image_ref:
                uploaded = self._uploaded_image_from_ref(image_ref, block, image_index)
                parts.append(self._image_asset_part(uploaded))
                attachments.append(self._attachment_metadata(uploaded))
                image_index += 1
                continue
            text = self._content_block_text(block)
            if text:
                parts.append(text)

        if attachments:
            return {"content_type": "multimodal_text", "parts": parts}, {"attachments": attachments}
        return {"content_type": "text", "parts": ["".join(str(part) for part in parts)]}, {}

    def _api_messages_to_conversation_messages(self, messages: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        """把标准 chat messages 转成 web conversation 所需的 messages。"""
        conversation_messages = []
        for item in messages:
            content, metadata = self._conversation_content_from_api_content(item.get("content", ""))
            message = {
                "id": new_uuid(),
                "author": {"role": item.get("role", "user")},
                "content": content,
            }
            if metadata:
                message["metadata"] = metadata
            conversation_messages.append(message)
        return conversation_messages

    def _conversation_payload(self, messages: list[Dict[str, Any]], model: str, timezone: str) -> Dict[str, Any]:
        """把标准 messages 构造成 web 对话请求体。"""
        return {
            "action": "next",
            "messages": self._api_messages_to_conversation_messages(messages),
            "model": model,
            "parent_message_id": new_uuid(),
            "conversation_mode": {"kind": "primary_assistant"},
            "conversation_origin": None,
            "force_paragen": False,
            "force_paragen_model_slug": "",
            "force_rate_limit": False,
            "force_use_sse": True,
            "history_and_training_disabled": True,
            "reset_rate_limits": False,
            "suggestions": [],
            "supported_encodings": [],
            "system_hints": [],
            "timezone": timezone,
            "timezone_offset_min": -480,
            "variant_purpose": "comparison_implicit",
            "websocket_request_id": new_uuid(),
            "client_contextual_info": {
                "is_dark_mode": False,
                "time_since_loaded": 120,
                "page_height": 900,
                "page_width": 1400,
                "pixel_ratio": 2,
                "screen_height": 1440,
                "screen_width": 2560,
            },
        }

    def _image_model_slug(self, model: str) -> str:
        """把标准图片模型名映射到底层 model slug。"""
        model = str(model or "").strip()
        if not model:
            return "auto"
        return config.image_model_mappings.get(model, "auto")

    def _image_headers(self, path: str, requirements: ChatRequirements, conduit_token: str = "", accept: str = "*/*") -> \
            Dict[str, str]:
        """构造图片链路请求头。"""
        headers = {
            "Content-Type": "application/json",
            "Accept": accept,
            "OpenAI-Sentinel-Chat-Requirements-Token": requirements.token,
        }
        if requirements.proof_token:
            headers["OpenAI-Sentinel-Proof-Token"] = requirements.proof_token
        if conduit_token:
            headers["X-Conduit-Token"] = conduit_token
        if accept == "text/event-stream":
            headers["X-Oai-Turn-Trace-Id"] = new_uuid()
        return self._headers(path, headers)

    def _prepare_image_conversation(self, prompt: str, requirements: ChatRequirements, model: str) -> str:
        """为图片生成准备 conduit token。"""
        path = "/backend-api/f/conversation/prepare"
        payload = {
            "action": "next",
            "fork_from_shared_post": False,
            "parent_message_id": new_uuid(),
            "model": self._image_model_slug(model),
            "client_prepare_state": "success",
            "timezone_offset_min": -480,
            "timezone": "Asia/Shanghai",
            "conversation_mode": {"kind": "primary_assistant"},
            "system_hints": ["picture_v2"],
            "partial_query": {
                "id": new_uuid(),
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": [prompt]},
            },
            "supports_buffering": True,
            "supported_encodings": ["v1"],
            "client_contextual_info": {"app_name": "chatgpt.com"},
        }
        response = self.session.post(
            self.base_url + path,
            headers=self._image_headers(path, requirements),
            json=payload,
            timeout=60,
        )
        ensure_ok(response, path)
        return response.json().get("conduit_token", "")

    def _decode_image_base64(self, image: str) -> bytes:
        """把 base64 图片字符串或本地路径解码成二进制。"""
        if self._is_http_url(image):
            return self._download_input_image_url(image)
        if self._is_file_url(image):
            file_path = self._path_from_file_url(image)
            if file_path.exists() and file_path.is_file():
                return file_path.read_bytes()
        if (
                image
                and len(image) < 512
                and not image.startswith("data:")
                and "\n" not in image
                and "\r" not in image
        ):
            file_path = Path(os.path.expanduser(image))
            if file_path.exists() and file_path.is_file():
                return file_path.read_bytes()
        payload = image.split(",", 1)[1] if image.startswith("data:") and "," in image else image
        return base64.b64decode(payload)

    @staticmethod
    def _is_http_url(value: str) -> bool:
        parsed = urlparse(str(value or "").strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _is_file_url(value: str) -> bool:
        return urlparse(str(value or "").strip()).scheme == "file"

    @staticmethod
    def _path_from_file_url(value: str) -> Path:
        parsed = urlparse(str(value or "").strip())
        path = url2pathname(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        return Path(path)

    def _download_input_image_url(self, url: str) -> bytes:
        response = requests.get(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "image/*,*/*;q=0.8"},
            timeout=120,
            **proxy_settings.build_session_kwargs(impersonate=self.fp["impersonate"], verify=True),
        )
        ensure_ok(response, "input_image_url_download")
        return response.content

    def _upload_image(self, image: str, file_name: str = "image.png") -> Dict[str, Any]:
        """上传一张 base64 图片，返回底层文件元数据。"""
        data = self._decode_image_base64(image)
        if self._is_http_url(image):
            url_name = Path(urlparse(image).path).name
            if url_name:
                file_name = url_name
        if self._is_file_url(image):
            file_path = self._path_from_file_url(image)
            if file_path.exists() and file_path.is_file():
                file_name = file_path.name
        if (
                image
                and len(image) < 512
                and not image.startswith("data:")
                and "\n" not in image
                and "\r" not in image
        ):
            candidate_path = Path(os.path.expanduser(image))
            if candidate_path.exists() and candidate_path.is_file():
                file_name = candidate_path.name
        image = Image.open(BytesIO(data))
        width, height = image.size
        mime_type = Image.MIME.get(image.format, "image/png")
        path = "/backend-api/files"
        response = self.session.post(
            self.base_url + path,
            headers=self._headers(path, {"Content-Type": "application/json", "Accept": "application/json"}),
            json={"file_name": file_name, "file_size": len(data), "use_case": "multimodal", "width": width,
                  "height": height},
            timeout=60,
        )
        ensure_ok(response, path)
        upload_meta = response.json()
        time.sleep(0.5)
        response = self.session.put(
            upload_meta["upload_url"],
            headers={
                "Content-Type": mime_type,
                "x-ms-blob-type": "BlockBlob",
                "x-ms-version": "2020-04-08",
                "Origin": self.base_url,
                "Referer": self.base_url + "/",
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.8",
            },
            data=data,
            timeout=120,
        )
        ensure_ok(response, "image_upload")
        path = f"/backend-api/files/{upload_meta['file_id']}/uploaded"
        response = self.session.post(
            self.base_url + path,
            headers=self._headers(path, {"Content-Type": "application/json", "Accept": "application/json"}),
            data="{}",
            timeout=60,
        )
        ensure_ok(response, path)
        return {
            "file_id": upload_meta["file_id"],
            "file_name": file_name,
            "file_size": len(data),
            "mime_type": mime_type,
            "width": width,
            "height": height,
        }

    def _start_image_generation(self, prompt: str, requirements: ChatRequirements, conduit_token: str, model: str,
                                references: Optional[list[Dict[str, Any]]] = None) -> requests.Response:
        """启动图片生成或编辑的 SSE 请求。"""
        references = references or []
        parts = [{
            "content_type": "image_asset_pointer",
            "asset_pointer": f"file-service://{item['file_id']}",
            "width": item["width"],
            "height": item["height"],
            "size_bytes": item["file_size"],
        } for item in references]
        parts.append(prompt)
        content = {"content_type": "multimodal_text", "parts": parts} if references else {"content_type": "text",
                                                                                          "parts": [prompt]}
        metadata = {
            "developer_mode_connector_ids": [],
            "selected_github_repos": [],
            "selected_all_github_repos": False,
            "system_hints": ["picture_v2"],
            "serialization_metadata": {"custom_symbol_offsets": []},
        }
        if references:
            metadata["attachments"] = [{
                "id": item["file_id"],
                "mimeType": item["mime_type"],
                "name": item["file_name"],
                "size": item["file_size"],
                "width": item["width"],
                "height": item["height"],
            } for item in references]
        payload = {
            "action": "next",
            "messages": [{
                "id": new_uuid(),
                "author": {"role": "user"},
                "create_time": time.time(),
                "content": content,
                "metadata": metadata,
            }],
            "parent_message_id": new_uuid(),
            "model": self._image_model_slug(model),
            "client_prepare_state": "sent",
            "timezone_offset_min": -480,
            "timezone": "Asia/Shanghai",
            "conversation_mode": {"kind": "primary_assistant"},
            "enable_message_followups": True,
            "system_hints": ["picture_v2"],
            "supports_buffering": True,
            "supported_encodings": ["v1"],
            "client_contextual_info": {
                "is_dark_mode": False,
                "time_since_loaded": 1200,
                "page_height": 1072,
                "page_width": 1724,
                "pixel_ratio": 1.2,
                "screen_height": 1440,
                "screen_width": 2560,
                "app_name": "chatgpt.com",
            },
            "paragen_cot_summary_display_override": "allow",
            "force_parallel_switch": "auto",
        }
        path = "/backend-api/f/conversation"
        response = self.session.post(
            self.base_url + path,
            headers=self._image_headers(path, requirements, conduit_token, "text/event-stream"),
            json=payload,
            timeout=300,
            stream=True,
        )
        ensure_ok(response, path)
        return response

    def _get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        """获取完整 conversation 详情。"""
        path = f"/backend-api/conversation/{conversation_id}"
        response = self.session.get(self.base_url + path, headers=self._headers(path, {"Accept": "application/json"}),
                                    timeout=60)
        ensure_ok(response, path)
        return response.json()

    def _list_recent_conversations(self, limit: int = 5, timeout_secs: float = 10.0) -> list[Dict[str, Any]]:
        """列出最近 conversation，用于 SSE 太短时恢复 conversation_id。"""
        path = f"/backend-api/conversations?offset=0&limit={limit}&order=updated&conversation_filter=all"
        try:
            response = self.session.get(
                self.base_url + path,
                headers=self._headers(path, {"Accept": "application/json"}),
                timeout=timeout_secs,
            )
            ensure_ok(response, path)
            data = response.json()
            items = data.get("items") or data.get("conversations") or []
            return items if isinstance(items, list) else []
        except Exception as exc:
            logger.debug({"event": "list_conversations_failed", "error": str(exc)})
            return []

    def find_conversation_by_prompt(self, prompt: str, started_at: float, timeout_secs: float = 10.0) -> str:
        """根据 prompt 与请求时间，从最近 conversation 中恢复 conversation_id。"""
        items = self._list_recent_conversations(limit=10, timeout_secs=timeout_secs)
        if not items:
            return ""
        prompt_words = set(str(prompt or "").lower().strip().split())
        best_match = ""
        best_score = 0.0
        fallback = ""
        for item in items:
            if not isinstance(item, dict):
                continue
            conv_id = str(item.get("id") or item.get("conversation_id") or "")
            if not conv_id:
                continue
            updated_at = float(item.get("update_time") or item.get("updated_at") or 0)
            if updated_at and started_at and (updated_at < started_at - 30 or updated_at > started_at + 600):
                continue
            if not fallback:
                fallback = conv_id
            title_words = set(str(item.get("title") or "").lower().split())
            score = 0.0
            if prompt_words and title_words:
                score = len(prompt_words & title_words) / max(len(prompt_words), 1)
            if str(item.get("title") or "").lower().startswith("image"):
                score += 0.3
            if score > best_score:
                best_score = score
                best_match = conv_id
        if best_match and best_score > 0.1:
            logger.info({"event": "conversation_id_recovered_by_prompt", "conversation_id": best_match, "score": best_score})
            return best_match
        if fallback:
            logger.info({"event": "conversation_id_recovered_by_recent", "conversation_id": fallback})
            return fallback
        return ""

    @staticmethod
    def _add_unique(values: list[str], candidates: list[str]) -> None:
        for candidate in candidates:
            if candidate and candidate not in values:
                values.append(candidate)

    @classmethod
    def _extract_image_reference_ids(cls, payload: Any) -> tuple[list[str], list[str]]:
        file_ids: list[str] = []
        sediment_ids: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str):
                cls._add_unique(file_ids, FILE_SERVICE_ID_RE.findall(value))
                cls._add_unique(file_ids, REAL_IMAGE_FILE_ID_RE.findall(value))
                cls._add_unique(sediment_ids, SEDIMENT_ID_RE.findall(value))
                return
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)
                return
            if isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)
        return file_ids, sediment_ids

    @classmethod
    def _has_image_asset_pointer(cls, payload: Any) -> bool:
        if isinstance(payload, dict):
            if str(payload.get("content_type") or "") == "image_asset_pointer":
                return True
            asset_pointer = str(payload.get("asset_pointer") or "")
            if asset_pointer.startswith(("file-service://", "sediment://")):
                return True
            return any(cls._has_image_asset_pointer(item) for item in payload.values())
        if isinstance(payload, list):
            return any(cls._has_image_asset_pointer(item) for item in payload)
        return False

    def _extract_image_tool_records(self, data: Dict[str, Any]) -> list[Dict[str, Any]]:
        """从 conversation 明细里提取图片工具输出记录。"""
        mapping = data.get("mapping") or {}
        records = []
        for message_id, node in mapping.items():
            message = (node or {}).get("message") or {}
            author = message.get("author") or {}
            metadata = message.get("metadata") or {}
            content = message.get("content") or {}
            role = str(author.get("role") or "").strip().lower()
            if role not in {"tool", "assistant"}:
                continue
            is_image_gen = metadata.get("async_task_type") == "image_gen"
            has_asset_pointer = self._has_image_asset_pointer(content) or self._has_image_asset_pointer(metadata)
            if role == "assistant" and not (is_image_gen or has_asset_pointer):
                continue
            file_ids, sediment_ids = self._extract_image_reference_ids({"content": content, "metadata": metadata})
            if not is_image_gen and not has_asset_pointer and not file_ids and not sediment_ids:
                continue
            records.append(
                {"message_id": message_id, "create_time": message.get("create_time") or 0, "file_ids": file_ids,
                 "sediment_ids": sediment_ids})
        return sorted(records, key=lambda item: item["create_time"])

    @staticmethod
    def _find_content_policy_error_in_conversation(data: Dict[str, Any]) -> str:
        """从 conversation 文本中查找内容政策拒绝。"""
        mapping = data.get("mapping") or {}
        for node in mapping.values():
            message = (node or {}).get("message") or {}
            author = message.get("author") or {}
            role = str(author.get("role") or "").strip().lower()
            if role not in {"assistant", "tool"}:
                continue
            content = message.get("content") or {}
            parts: list[str] = []
            if isinstance(content, dict):
                for part in content.get("parts") or []:
                    if isinstance(part, str) and part.strip():
                        parts.append(part.strip())
                text = str(content.get("text") or "").strip()
                if text:
                    parts.append(text)
            elif isinstance(content, str) and content.strip():
                parts.append(content.strip())
            message_text = "\n".join(parts)
            if message_text and _is_content_policy_error(message_text):
                return message_text[:500]
        return ""

    def _poll_image_results(
            self,
            conversation_id: str,
            timeout_secs: float = 120.0,
            initial_file_ids: list[str] | None = None,
            initial_sediment_ids: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """轮询 conversation，直到拿到图片文件 id 或超时。"""
        start = time.time()
        attempt = 0
        interval = float(config.image_poll_interval_secs)
        initial_wait = float(config.image_poll_initial_wait_secs)
        file_ids: list[str] = []
        sediment_ids: list[str] = []
        self._add_unique(file_ids, initial_file_ids or [])
        self._add_unique(sediment_ids, initial_sediment_ids or [])
        has_initial_ids = bool(file_ids or sediment_ids)
        last_hit_key: tuple[tuple[str, ...], tuple[str, ...]] | None = (
            (tuple(file_ids), tuple(sediment_ids)) if has_initial_ids else None
        )
        logger.info({
            "event": "image_poll_start",
            "conversation_id": conversation_id,
            "timeout_secs": timeout_secs,
            "initial_wait_secs": initial_wait,
            "interval_secs": interval,
            "initial_file_ids": file_ids,
            "initial_sediment_ids": sediment_ids,
        })

        def remaining() -> float:
            return timeout_secs - (time.time() - start)

        if has_initial_ids and config.image_settle_enabled:
            settle_for = min(config.image_settle_secs, max(0.0, remaining()))
            if settle_for > 0:
                time.sleep(settle_for)
        elif initial_wait > 0:
            jitter = random.uniform(0, min(2.0, initial_wait * 0.2))
            sleep_for = min(initial_wait + jitter, max(0.0, remaining()))
            if sleep_for > 0:
                time.sleep(sleep_for)

        def retry_sleep(reason: str, status_code: int | None, error: str | None, retry_after: int | None) -> bool:
            base = retry_after if retry_after is not None else min(2 ** min(attempt, 4), 16)
            sleep_for = min(base + random.uniform(0, 0.5), max(0.0, remaining()))
            if sleep_for <= 0:
                return False
            payload: Dict[str, Any] = {
                "event": "image_poll_retry",
                "conversation_id": conversation_id,
                "attempt": attempt,
                "reason": reason,
                "sleep_secs": round(sleep_for, 2),
            }
            if status_code is not None:
                payload["status_code"] = status_code
            if error is not None:
                payload["error"] = error
            logger.warning(payload)
            time.sleep(sleep_for)
            return True

        last_task_error = ""
        while remaining() > 0:
            attempt += 1
            last_task_error = ""
            try:
                tasks = self._query_backend_tasks(conversation_id=conversation_id, timeout_secs=5.0)
                for task in tasks:
                    is_error, error_msg, metadata = self.check_task_error(task)
                    if is_error and error_msg:
                        last_task_error = error_msg
                        logger.info({
                            "event": "image_poll_task_error_not_blocking",
                            "conversation_id": conversation_id,
                            "attempt": attempt,
                            "error_msg": error_msg,
                            "metadata": metadata,
                        })
            except Exception as exc:
                logger.debug({
                    "event": "image_poll_task_check_failed",
                    "conversation_id": conversation_id,
                    "attempt": attempt,
                    "error": str(exc),
                })
            try:
                conversation = self._get_conversation(conversation_id)
            except UpstreamHTTPError as exc:
                if exc.status_code in (429, 500, 502, 503, 504):
                    if retry_sleep("upstream_status", exc.status_code, None, exc.retry_after):
                        continue
                    break
                raise
            except requests.exceptions.RequestException as exc:
                if retry_sleep("network", None, str(exc), None):
                    continue
                break

            for record in self._extract_image_tool_records(conversation):
                for file_id in record["file_ids"]:
                    if file_id not in file_ids:
                        file_ids.append(file_id)
                for sediment_id in record["sediment_ids"]:
                    if sediment_id not in sediment_ids:
                        sediment_ids.append(sediment_id)

            if not file_ids and not sediment_ids:
                policy_msg = self._find_content_policy_error_in_conversation(conversation)
                if policy_msg:
                    logger.warning({
                        "event": "image_poll_conversation_text_policy_violation",
                        "conversation_id": conversation_id,
                        "attempt": attempt,
                        "error_msg": policy_msg[:200],
                    })
                    raise ImageContentPolicyError(policy_msg)

            logger.debug({"event": "image_poll_check", "conversation_id": conversation_id, "attempt": attempt, "file_ids": file_ids, "sediment_ids": sediment_ids})
            if file_ids or sediment_ids:
                if not config.image_check_before_hit_enabled:
                    logger.info({"event": "image_poll_hit_no_settle", "conversation_id": conversation_id, "file_ids": file_ids, "sediment_ids": sediment_ids})
                    return file_ids, sediment_ids
                hit_key = (tuple(file_ids), tuple(sediment_ids))
                if last_hit_key == hit_key:
                    logger.info({"event": "image_poll_hit", "conversation_id": conversation_id, "file_ids": file_ids, "sediment_ids": sediment_ids})
                    return file_ids, sediment_ids
                last_hit_key = hit_key
                if not config.image_settle_enabled:
                    logger.info({"event": "image_poll_hit_settle_disabled", "conversation_id": conversation_id, "file_ids": file_ids, "sediment_ids": sediment_ids})
                    return file_ids, sediment_ids
                logger.info({"event": "image_poll_hit_pending_settle", "conversation_id": conversation_id, "file_ids": file_ids, "sediment_ids": sediment_ids, "settle_secs": config.image_settle_secs})
                wait = min(config.image_settle_secs, max(0.0, remaining()))
                if wait > 0:
                    time.sleep(wait)
                    continue
                return file_ids, sediment_ids
            logger.debug({"event": "image_poll_wait", "conversation_id": conversation_id, "elapsed_secs": round(time.time() - start, 1)})
            wait = min(interval, max(0.0, remaining()))
            if wait > 0:
                time.sleep(wait)
        logger.info({
            "event": "image_poll_timeout",
            "conversation_id": conversation_id,
            "timeout_secs": timeout_secs,
            "attempts_made": attempt,
            "last_task_error": last_task_error or None,
        })
        exc = ImagePollTimeoutError(
            f"ChatGPT 生图超时（已等待 {timeout_secs} 秒）。"
            "可以在 config.json 中调大 image_poll_timeout_secs，"
            "也可能是账号被限流或上游生图队列拥堵。"
        )
        if last_task_error:
            setattr(exc, "task_error", last_task_error)
        setattr(exc, "conversation_id", conversation_id or "")
        raise exc

    def _get_file_download_url(self, file_id: str) -> str:
        """获取文件下载地址。"""
        path = f"/backend-api/files/{file_id}/download"
        response = self.session.get(self.base_url + path, headers=self._headers(path, {"Accept": "application/json"}),
                                    timeout=60)
        ensure_ok(response, path)
        data = response.json()
        return data.get("download_url") or data.get("url") or ""

    def _get_attachment_download_url(self, conversation_id: str, attachment_id: str) -> str:
        """通过 conversation 附件接口获取下载地址。"""
        path = f"/backend-api/conversation/{conversation_id}/attachment/{attachment_id}/download"
        response = self.session.get(self.base_url + path, headers=self._headers(path, {"Accept": "application/json"}),
                                    timeout=60)
        ensure_ok(response, path)
        data = response.json()
        return data.get("download_url") or data.get("url") or ""

    def _query_backend_tasks(
            self,
            conversation_id: str = "",
            task_id: str = "",
            timeout_secs: float = 30.0,
    ) -> list[Dict[str, Any]]:
        """查询 ChatGPT tasks，用于辅助识别结构化错误。"""
        path = "/backend-api/tasks"
        response = self.session.get(
            self.base_url + path,
            headers=self._headers(path, {"Accept": "application/json"}),
            timeout=timeout_secs,
        )
        ensure_ok(response, path)
        data = response.json()
        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            return []
        if conversation_id:
            tasks = [
                task for task in tasks
                if isinstance(task, dict)
                and (
                    task.get("conversation_id") == conversation_id
                    or task.get("original_conversation_id") == conversation_id
                )
            ]
        if task_id:
            tasks = [task for task in tasks if isinstance(task, dict) and task.get("task_id") == task_id]
        return tasks

    @staticmethod
    def check_task_error(task: Dict[str, Any]) -> tuple[bool, str, Dict[str, Any]]:
        """检查 task 中的结构化错误。"""
        image_message = task.get("image_gen_message") or {}
        if not isinstance(image_message, dict):
            return False, "", {}
        metadata = image_message.get("metadata") or {}
        content = image_message.get("content") or {}
        author = image_message.get("author") or {}
        is_error = metadata.get("is_error", False)
        is_text_only = isinstance(content, dict) and content.get("content_type") == "text"
        is_assistant_role = isinstance(author, dict) and author.get("role") == "assistant"
        error_msg = ""
        if is_error and is_text_only and is_assistant_role:
            parts = content.get("parts", [])
            if isinstance(parts, list):
                error_msg = "".join(part for part in parts if isinstance(part, str))
        return bool(is_error and is_text_only and is_assistant_role), error_msg, metadata if isinstance(metadata, dict) else {}

    def _resolve_image_urls(self, conversation_id: str, file_ids: list[str], sediment_ids: list[str]) -> list[str]:
        """把图片结果 id 解析成可下载 URL。"""
        urls = []
        skip_patterns = {"file_upload"}
        for file_id in file_ids:
            if file_id in skip_patterns:
                logger.debug({
                    "event": "image_file_id_skipped",
                    "source": "file",
                    "conversation_id": conversation_id,
                    "id": file_id,
                })
                continue
            try:
                url = self._get_file_download_url(file_id)
            except Exception as exc:
                logger.debug({
                    "event": "image_download_url_failed",
                    "source": "file",
                    "conversation_id": conversation_id,
                    "id": file_id,
                    "error": repr(exc),
                })
                continue
            if url and url not in urls:
                urls.append(url)
            else:
                logger.debug({
                    "event": "image_download_url_empty",
                    "source": "file",
                    "conversation_id": conversation_id,
                    "id": file_id,
                })
        if not conversation_id or not sediment_ids:
            logger.debug({
                "event": "image_urls_resolved",
                "conversation_id": conversation_id,
                "file_ids": file_ids,
                "sediment_ids": sediment_ids,
                "urls": urls,
            })
            return urls
        for sediment_id in sediment_ids:
            try:
                url = self._get_attachment_download_url(conversation_id, sediment_id)
            except Exception as exc:
                logger.debug({
                    "event": "image_download_url_failed",
                    "source": "sediment",
                    "conversation_id": conversation_id,
                    "id": sediment_id,
                    "error": repr(exc),
                })
                continue
            if url and url not in urls:
                urls.append(url)
            else:
                logger.debug({
                    "event": "image_download_url_empty",
                    "source": "sediment",
                    "conversation_id": conversation_id,
                    "id": sediment_id,
                })
        logger.debug({
            "event": "image_urls_resolved",
            "conversation_id": conversation_id,
            "file_ids": file_ids,
            "sediment_ids": sediment_ids,
            "urls": urls,
        })
        return urls

    def resolve_conversation_image_urls(
            self,
            conversation_id: str,
            file_ids: list[str],
            sediment_ids: list[str],
            poll: bool = True,
            poll_timeout_secs: float | None = None,
    ) -> list[str]:
        file_ids = [item for item in file_ids if item != "file_upload"]
        sediment_ids = list(sediment_ids)
        timeout = poll_timeout_secs if poll_timeout_secs is not None else config.image_poll_timeout_secs
        if poll and conversation_id and (file_ids or sediment_ids):
            if not config.image_check_before_hit_enabled and not config.image_settle_enabled:
                logger.info({
                    "event": "image_resolve_skip_poll_direct_resolve",
                    "conversation_id": conversation_id,
                    "file_ids": file_ids,
                    "sediment_ids": sediment_ids,
                })
                return self._resolve_image_urls(conversation_id, file_ids, sediment_ids)
        if poll and conversation_id:
            logger.info({
                "event": "image_resolve_poll_needed",
                "conversation_id": conversation_id,
                "initial_file_ids": file_ids,
                "initial_sediment_ids": sediment_ids,
                "poll_timeout_secs": timeout,
            })
            try:
                polled_file_ids, polled_sediment_ids = self._poll_image_results(
                    conversation_id,
                    timeout,
                    file_ids,
                    sediment_ids,
                )
            except ImagePollTimeoutError as exc:
                task_error = getattr(exc, "task_error", "")
                if not file_ids and not sediment_ids:
                    if task_error:
                        raise ImageContentPolicyError(task_error) from exc
                    raise
                logger.warning({
                    "event": "image_resolve_poll_partial_timeout",
                    "conversation_id": conversation_id,
                    "file_ids": file_ids,
                    "sediment_ids": sediment_ids,
                })
            except Exception as exc:
                if not file_ids and not sediment_ids:
                    raise
                logger.warning({
                    "event": "image_resolve_poll_partial_error",
                    "conversation_id": conversation_id,
                    "file_ids": file_ids,
                    "sediment_ids": sediment_ids,
                    "error": repr(exc),
                })
            else:
                file_ids.extend(item for item in polled_file_ids if item and item not in file_ids)
                sediment_ids.extend(item for item in polled_sediment_ids if item and item not in sediment_ids)
        return self._resolve_image_urls(conversation_id, file_ids, sediment_ids)

    def download_image_bytes(self, urls: list[str]) -> list[bytes]:
        images = []
        for url in urls:
            response = self.session.get(url, timeout=120)
            ensure_ok(response, "image_download")
            if response.content not in images:
                images.append(response.content)
        return images

    def stream_conversation(
            self,
            messages: Optional[list[Dict[str, Any]]] = None,
            model: str = "auto",
            prompt: str = "",
            images: Optional[list[str]] = None,
            system_hints: Optional[list[str]] = None,
    ) -> Iterator[str]:
        system_hints = system_hints or []
        if "picture_v2" in system_hints:
            yield from self._stream_picture_conversation(prompt, model, images or [])
            return

        normalized = messages or [{"role": "user", "content": prompt}]
        self._bootstrap()
        requirements = self._get_chat_requirements()
        path, timezone = self._chat_target()
        payload = self._conversation_payload(normalized, model, timezone)
        response = self.session.post(
            self.base_url + path,
            headers=self._conversation_headers(path, requirements),
            json=payload,
            timeout=300,
            stream=True,
        )
        ensure_ok(response, path)
        try:
            yield from iter_sse_payloads(response)
        finally:
            response.close()

    def _stream_picture_conversation(
            self,
            prompt: str,
            model: str,
            images: list[str],
    ) -> Iterator[str]:
        if not self.access_token:
            raise RuntimeError("access_token is required for image endpoints")
        references = [self._upload_image(image, f"image_{idx}.png") for idx, image in enumerate(images, start=1)]
        self._bootstrap()
        requirements = self._get_chat_requirements()
        conduit_token = self._prepare_image_conversation(prompt, requirements, model)
        response = self._start_image_generation(prompt, requirements, conduit_token, model, references)
        try:
            yield from iter_sse_payloads(response)
        finally:
            response.close()

    def _bootstrap(self) -> None:
        """预热首页，并提取 PoW 相关脚本引用。"""
        response = self.session.get(
            self.base_url + "/",
            headers=self._bootstrap_headers(),
            timeout=30,
        )
        ensure_ok(response, "bootstrap")
        self.pow_script_sources, self.pow_data_build = parse_pow_resources(response.text)
        if not self.pow_script_sources:
            self.pow_script_sources = [DEFAULT_POW_SCRIPT]

    def _get_chat_requirements(self) -> ChatRequirements:
        """获取当前模式对话所需的 sentinel token。"""
        path = "/backend-api/sentinel/chat-requirements" if self.access_token else "/backend-anon/sentinel/chat-requirements"
        context = "auth_chat_requirements" if self.access_token else "noauth_chat_requirements"
        body = {"p": build_legacy_requirements_token(self.user_agent, self.pow_script_sources, self.pow_data_build)}
        response = self.session.post(
            self.base_url + path,
            headers=self._headers(path, {"Content-Type": "application/json"}),
            json=body,
            timeout=30,
        )
        ensure_ok(response, context)
        requirements = self._build_requirements(response.json(), "" if self.access_token else body["p"])
        if not requirements.token:
            message = "missing auth chat requirements token" if self.access_token else "missing chat requirements token"
            raise RuntimeError(f"{message}: {requirements.raw_finalize}")
        return requirements

    def _chat_target(self) -> tuple[str, str]:
        if self.access_token:
            return "/backend-api/conversation", "Asia/Shanghai"
        return "/backend-anon/conversation", "America/Los_Angeles"

    def list_models(self) -> Dict[str, Any]:
        """返回当前模式下可用模型，格式对齐 OpenAI `/v1/models`。"""
        self._bootstrap()
        path = "/backend-api/models?history_and_training_disabled=false" if self.access_token else (
            "/backend-anon/models?iim=false&is_gizmo=false"
        )
        route = "/backend-api/models" if self.access_token else "/backend-anon/models"
        context = "auth_models" if self.access_token else "anon_models"
        response = self.session.get(
            self.base_url + path,
            headers=self._headers(route),
            timeout=30,
        )
        ensure_ok(response, context)
        data = []
        seen = set()
        for item in response.json().get("models", []):
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug", "")).strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            data.append({
                "id": slug,
                "object": "model",
                "created": int(item.get("created") or 0),
                "owned_by": str(item.get("owned_by") or "chatgpt"),
                "permission": [],
                "root": slug,
                "parent": None,
            })
        data.sort(key=lambda item: item["id"])
        return {"object": "list", "data": data}
