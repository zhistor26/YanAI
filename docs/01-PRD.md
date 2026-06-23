# 生图失败核查与修复 PRD

## 功能目标

修复 LazyCat 移植环境中画图工作台显示“生图失败”的问题。修复必须对照乔木相关上游源码中实际维护 ChatGPT 图片协议的 `basketikun/chatgpt2api`，回迁与图片结果解析、异步轮询和错误区分直接相关的最小实现。

本次不完整搬迁上游异步任务系统、文件任务系统或 UI 改版，只处理现有 `/v1/images/generations`、`/v1/images/edits` 和内置 ChatGPT Web 图片链路拿不到结果的问题。

## 输入

- 前端画图工作台提交的 `POST /v1/images/generations` 文生图请求。
- 前端画图工作台提交的 `POST /v1/images/edits` 图生图请求。
- 外部 OpenAI 兼容图片渠道返回的 `b64_json` 或 `url` 图片结果。
- 内置 ChatGPT Web 图片链路返回的 SSE 事件、conversation 详情、`file-service://`、`sediment://`、文件下载地址。
- 上游先返回普通 assistant 文本、工具参数 JSON 或 `referenced_image_ids`，图片结果稍后才写入 conversation 的异步场景。

## 输出

- 成功时返回 OpenAI 兼容图片响应，`data` 中包含可访问的 `url`，前端图片状态为成功。
- 上游内容政策拒绝、真正超时、渠道不可用或无结果时，后端返回明确错误信息，前端展示具体失败原因。
- 图片记录继续通过唯一入口 `image_service.record_image_result` 写入，不新增第二套图片记录数据源。

## 边界

- 不改动画、队列、历史记录、灯箱、继续编辑等前端交互。
- 不改用户额度扣减、个人渠道优先级、账号池租约规则。
- 不新增新的外部渠道类型。
- 不引入新的图片存储后端，图片文件仍写入 `config.images_dir`。
- 不完整引入上游 `/backend-api/tasks` 异步任务 API，只在图片轮询中用于辅助识别结构化错误。
- 不覆盖已有工作区改动，不使用 `git reset`、`git checkout` 或 `git commit`。

## 异常情况

- 外部渠道全部失败且内置账号池关闭时，返回 503，并带上外部渠道错误摘要。
- 个人生图渠道启用但配置缺失、模型不匹配或调用失败时，返回个人渠道错误，不回落到内置账号池免费生图。
- ChatGPT Web SSE 只返回文本，但该文本包含工具参数或 `referenced_image_ids` 时，不立刻当成最终失败，必须继续轮询 conversation。
- SSE 太短导致 `conversation_id` 丢失时，允许通过最近 conversation 列表按 prompt 和时间窗口尝试恢复。
- conversation 中图片结果可能出现在 `tool` 或 `assistant` 消息，也可能嵌在 content、metadata、字符串、数组、对象中的 `file-service://` 或 `sediment://`。
- 轮询 conversation 遇到 429、5xx 或网络抖动时应退避重试；真正超过超时预算时返回明确超时错误。
- 内容政策错误必须与异步工具参数 JSON 区分，不能把可能仍在后台生成的图片误判为内容政策失败。

## 验收标准

- `services/openai_backend_api.py` 能递归提取 `file-service://`、`sediment://` 和真实图片文件 ID，并接受 `tool`/`assistant` 两类结果消息。
- `services/openai_backend_api.py` 的图片轮询具备初始等待、轮询间隔、超时、429/5xx/网络退避和结果去重。
- `services/protocol/conversation.py` 对图片请求中“文本回复但疑似工具调用”的场景继续轮询，不直接返回文本失败。
- 图片下载 URL 解析同时保留 file 与 sediment 两类结果，下载内容去重。
- 新增配置项可控制图片轮询默认值，未配置时使用安全默认值。
- 单元测试覆盖上游协议差异：assistant asset pointer 提取、空 ID 轮询、初始 ID 仍轮询、文本工具参数回复继续取图。
