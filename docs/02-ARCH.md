# 生图失败核查与修复 ARCH

## 模块职责

### 前端画图工作台

`web/src/app/image/page.tsx` 只负责收集提示词、参考图、尺寸和数量，调用 `web/src/lib/api.ts` 的 `generateImage` 或 `editImage`，并根据 API 结果更新本地队列状态。

本次不把上游协议判断放进前端。失败原因由后端接口返回，前端继续展示后端错误消息。

### API 路由

`api/ai.py` 是图片接口入口：

- 校验登录身份。
- 为普通用户处理额度预扣、确认、释放。
- 调用 `channel_service` 优先尝试个人渠道和外部渠道。
- 外部渠道无结果时根据配置决定是否进入内置账号池。
- 成功后调用 `image_service.record_image_result` 记录图片。

### 外部渠道服务

`services/channel_service.py` 是外部 OpenAI 兼容渠道的统一调用入口。它负责模型映射、尺寸规范化、`/v1/images/generations`、`/v1/images/edits` 调用和结果规范化。本次不改变该模块的职责。

### ChatGPT Web 后端访问层

`services/openai_backend_api.py` 是内置 ChatGPT Web 图片链路的访问层，负责：

- 构造 conversation payload。
- 上传参考图或下载输入图片 URL。
- 发起图片生成 SSE。
- 查询完整 conversation。
- 从 conversation 递归提取图片文件 ID。
- 轮询异步图片结果。
- 把文件 ID、附件 ID 解析成下载 URL。
- 下载图片 bytes。

本次从 `basketikun/chatgpt2api` 回迁的核心逻辑放在该层，不把 HTTP 重试、conversation 解析和文件下载逻辑塞到 UI 或 API 路由。

### Conversation 协议解析层

`services/protocol/conversation.py` 是上游 conversation 事件解析层，负责：

- 规范 OpenAI/Responses 消息内容。
- 解析 assistant 文本 patch。
- 识别图片工具事件。
- 判断图片请求是否需要继续轮询。
- 把下载后的图片 bytes 格式化为 OpenAI 兼容图片响应。

当 SSE 最后一条状态只有文本但看起来是图片工具参数或 `referenced_image_ids` 时，该层应继续调用 backend 的轮询接口，而不是直接返回失败文本。

### 配置层

`services/config.py` 是轮询参数的唯一来源。本次新增以下有效配置：

- `image_poll_timeout_secs`：默认图片轮询超时。
- `image_poll_initial_wait_secs`：SSE 结束后首次查询 conversation 前等待时间。
- `image_poll_interval_secs`：后续轮询间隔。
- `image_check_before_hit_enabled`：发现图片 ID 后是否二次确认。
- `image_settle_enabled`：发现图片 ID 后是否短暂等待稳定。
- `image_settle_secs`：稳定等待时间。

业务代码通过 `config` 读取这些值，不在多个模块各自维护一份默认值。

### 图片记录

`services/image_service.py` 是图片记录唯一权威入口，负责列出、删除、写入和同步图片记录。本次不新增图片记录表或 JSON 文件。

## 数据流

1. 前端调用 `generateImage` 或 `editImage`。
2. `api/ai.py` 生成 `request_id`，附加 `base_url`。
3. `channel_service.call_generation` 或 `channel_service.call_edit` 尝试外部渠道。
4. 外部渠道成功则返回统一图片响应并记录图片。
5. 外部渠道失败且允许内置池时，`openai_v1_image_generations.handle` 或 `openai_v1_image_edit.handle` 进入 `stream_image_outputs_with_pool`。
6. `stream_image_outputs_with_pool` 租用账号，`OpenAIBackendAPI` 发起 ChatGPT Web 图片请求。
7. `conversation.py` 解析 SSE。若 SSE 直接给出图片 ID，则进入 URL 解析；若只给出疑似工具文本，则继续轮询 conversation。
8. `openai_backend_api.py` 轮询 conversation，递归提取 `file-service://` 和 `sediment://`，解析下载 URL，下载图片 bytes。
9. `conversation.py` 保存图片并格式化为 OpenAI 兼容响应。
10. `image_service.record_image_result` 写入图片记录。
11. 前端根据 `data[0].url` 或 `data[0].b64_json` 标记图片成功。

## 接口连接

- 前端文生图：`POST /v1/images/generations`
- 前端图生图：`POST /v1/images/edits`
- Responses：`POST /v1/responses`
- 外部渠道模型列表：`GET <channel_base>/v1/models`
- 外部渠道文生图：`POST <channel_base>/v1/images/generations`
- 外部渠道图生图：`POST <channel_base>/v1/images/edits`
- 内置上游：`https://chatgpt.com/backend-api/...`

## 不改内容

- 不新增前端状态字段。
- 不新增图片记录表或 JSON 文件。
- 不改变 LazyCat 自动登录逻辑。
- 不改变 LazyCat 网盘打开/保存 inject 流程。
- 不改变已有渠道管理 API 结构。
- 不完整引入上游 image task 后台任务系统。
