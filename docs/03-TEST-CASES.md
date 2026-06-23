# 生图失败核查与修复 TEST CASES

## 单元测试

1. `conversation.normalize_messages` 保留多模态 `input_image` 和文本块。
2. `conversation.apply_text_patch` 支持 `/message/content` 下的 add、append、replace patch。
3. `openai_v1_response.messages_from_input` 遇到 Responses 多模态内容时不丢弃图片块。
4. `OpenAIBackendAPI._conversation_content_from_api_content` 将 `input_image` 转成 `image_asset_pointer`，并补充 attachments。
5. `OpenAIBackendAPI._extract_image_tool_records` 可以从 assistant content 中递归提取 `file-service://` 和 `sediment://`。
6. `OpenAIBackendAPI.resolve_conversation_image_urls` 在没有初始 ID 时调用轮询。
7. `OpenAIBackendAPI.resolve_conversation_image_urls` 在已有初始 ID 且开启二次确认时仍调用轮询并合并结果。
8. `conversation.stream_image_outputs` 遇到图片请求返回工具参数 JSON 文本时，不立即输出失败消息；如果轮询拿到 URL，应返回图片结果。
9. `image_service.list_images` 跳过记录中指向缺失本地文件的图片。
10. `image_service.record_image_result` 使用 repository 时只 insert，不全量读取和覆盖。
11. `conversation.save_image_bytes` 同内容图片生成不同文件名。

## 集成测试

1. 外部渠道返回 `b64_json`：
   - 请求 `/v1/images/generations`。
   - 后端保存本地图片。
   - 返回 `url`。
   - 写入图片记录。
2. 外部渠道返回 `url`：
   - 请求 `/v1/images/generations`。
   - 后端保持 URL 项。
   - 前端可以使用 `resolveApiAssetUrl` 展示。
3. 外部渠道失败且内置池关闭：
   - 请求 `/v1/images/generations`。
   - 返回 503。
   - 错误信息包含 `internal account pool is disabled` 和外部渠道错误。
4. 个人渠道失败：
   - 普通用户启用个人渠道。
   - 渠道模型不匹配或上游失败。
   - 返回 `personal image channel failed`，不回落内置池。
5. 内置池图片生成：
   - 配置有效 ChatGPT 账号。
   - 请求 `/v1/images/generations`。
   - SSE 没有直接图片 ID 时轮询 conversation。
   - 返回至少一条图片结果。
6. 内置池异步文本回复：
   - 上游 SSE 先返回包含工具参数 JSON 或 `referenced_image_ids` 的 assistant 文本。
   - 后端继续轮询 conversation。
   - conversation 后续出现 `file-service://` 或 `sediment://` 后返回图片。

## 异常用例

1. 上游返回普通 assistant 文本且无图片工具结果，应返回明确错误，而不是空 `data`。
2. 输入图片是 HTTP URL 时，后端下载后上传给 ChatGPT Web。
3. 输入图片是 `file://` URL 时，后端读取本地文件并上传。
4. 输入图片是 `file-service://` 时，后端复用已有文件 ID。
5. 本地图片记录指向不存在文件时，列表接口跳过该记录，避免前端展示坏图。
6. conversation 轮询超过 `image_poll_timeout_secs` 后，应返回明确超时错误。
7. conversation 文本包含内容政策拒绝时，应返回内容政策错误，不继续无意义下载。

## 手工验证点

1. LazyCat 安装包启动后，打开 `/image`。
2. 使用默认提示词生成 1 张图，检查前端是否出现具体错误或成功图片。
3. 管理后台渠道页测试默认外部渠道模型。
4. 查看系统日志 `/api/logs?type=call&status=failed`，确认失败记录包含 `request_id` 和上游错误。
5. 图片成功后进入图片管理页，确认新图片记录、文件大小、创建时间和 WebDAV 状态展示正常。

## 构建与测试命令

本仓库没有 `.bat` 编译脚本和 CMake target。验证使用项目现有 Python、Next.js、LazyCat 构建入口：

- Python 语法检查：`python -m py_compile <modified python files>`
- Python 单元测试：`python -m unittest test.test_conversation_text_parsing test.test_responses_multimodal test.test_image_service test.test_image_polling`
- Web 构建：`cd web; npm run build`
- LazyCat 打包入口：`lzc-cli project build`
