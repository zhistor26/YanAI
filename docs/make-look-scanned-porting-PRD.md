# PDF 伪扫描懒猫微服移植 PRD

**上游项目**：`make-look-scanned`  
**上游地址**：https://github.com/overflowy/make-look-scanned  
**建议应用名**：PDF 伪扫描  
**建议包名**：`cloud.lazycat.app.make-look-scanned`  
**文档版本**：0.1.0  
**目标版本**：1.0.0  
**文档日期**：2026-06-23

## 1. 背景

`make-look-scanned` 是一个将普通 PDF 转换为“类似实体扫描件”效果的开源工具。上游同时提供 CLI 和浏览器 WebAssembly 版本。浏览器版本通过 PDF.js 将 PDF 页面渲染为 Canvas 图像，再调用 Go WASM 暴露的 `mls.reset()`、`mls.addPage()`、`mls.build()` 生成最终 PDF。

本次移植目标不是重写算法，而是把上游 Web/WASM 能力改造成一个符合懒猫微服使用习惯与上架要求的 LPK 应用。重点包括中文化、PDF 文件应用关联、懒猫网盘打开与保存、本地 LPK 构建验证和提审。

## 2. 产品定位

PDF 伪扫描是一个本地浏览器处理的 PDF 工具，用户可将普通 PDF 一键转换为具有扫描件质感的图片型 PDF。处理过程在浏览器内完成，文件不上传到外部服务器。

核心使用场景：

- 将电子 PDF 转换为看起来像纸质扫描件的 PDF。
- 从懒猫网盘右键 PDF，直接用本应用打开并生成伪扫描文件。
- 生成后的 PDF 保存到本机或懒猫网盘。
- 在包含二维码、条形码、回执码等内容的 PDF 中，默认尽量保持扫码可读性。

## 3. 目标

### 3.1 业务目标

- 完成 `make-look-scanned` 的懒猫微服 LPK 移植。
- 争取满足“移植高质量自托管应用”激励条件。
- 通过 `file_handler` 对接 PDF 应用关联，争取额外应用关联激励。
- 提供中文化、可上架、可本地安装验证的完整应用包。

### 3.2 产品目标

- 用户可从本机选择 PDF 并生成伪扫描 PDF。
- 用户可从懒猫网盘右键 PDF 并调起本应用。
- 用户可将生成结果下载到本机或保存到懒猫网盘。
- 默认参数兼顾扫描质感与内容可读性，尤其避免二维码/条码在默认模式下明显失真。
- 页面核心文案全部中文化。

### 3.3 非目标

第一版不做：

- 服务端 PDF 转换 API。
- OCR 识别。
- 保留原 PDF 文字层。
- 批量队列处理。
- 用户账号、历史记录、云端任务管理。
- 二维码内容解析或“扫码识别”功能。

## 4. 用户画像

### 4.1 普通用户

用户已有 PDF 文件，希望快速生成“像扫描件”的版本，用于提交材料、归档或分享。

### 4.2 懒猫网盘用户

用户的 PDF 文件存放在懒猫网盘中，希望通过右键打开方式直接进入处理流程，不需要先下载到本机。

### 4.3 含码文档用户

用户的 PDF 中包含二维码、条形码、报名码、付款码、回执码等内容，希望转换后仍然尽量能被手机或扫码设备识别。

## 5. 用户流程

### 5.1 本机 PDF 流程

1. 用户打开 PDF 伪扫描应用。
2. 用户点击选择 PDF。
3. 用户从本机选择 PDF 文件。
4. 应用显示文件名和默认参数。
5. 用户可调整参数或选择预设。
6. 用户点击“生成扫描版 PDF”。
7. 应用逐页渲染并显示处理进度。
8. 应用生成 PDF 并显示预览。
9. 用户下载到本机或保存到懒猫网盘。

### 5.2 懒猫网盘应用关联流程

1. 用户在懒猫网盘中选中 PDF 文件。
2. 用户通过右键菜单或打开方式选择“PDF 伪扫描”。
3. 懒猫平台按 `file_handler.actions.open` 打开 `/?file=%u`。
4. 应用读取 URL 中的 `file` 参数。
5. 应用通过 `/_lzc/files/home` 读取对应 PDF Blob。
6. 应用将 Blob 构造成浏览器 `File` 对象。
7. 应用进入与本机 PDF 相同的生成流程。
8. 用户将结果保存到本机或懒猫网盘。

### 5.3 扫码友好流程

1. 用户选择含二维码或条码的 PDF。
2. 应用默认使用“推荐/清晰”参数，避免过高噪点、过低 JPEG 质量或过强模糊。
3. 用户如选择“重度扫描”预设，应用提示该模式可能影响二维码、条码识别。
4. 用户生成后可在预览中检查码区清晰度。
5. 验收测试中使用含二维码的样例 PDF 做人工扫码验证。

## 6. 功能范围

### 6.1 Web 中文化

必须完成：

- 应用标题中文化。
- 页面说明中文化。
- 上传、生成、下载、保存、预览、状态提示中文化。
- 参数名称中文化。
- 错误提示中文化。
- 保留上游地址和许可证说明入口。

推荐文案：

- 标题：`PDF 伪扫描`
- 副标题：`把普通 PDF 转成类似纸质扫描件的图片 PDF，文件在浏览器本地处理。`
- 主按钮：`生成扫描版 PDF`
- 下载按钮：`下载扫描版 PDF`
- 网盘保存按钮：`保存到懒猫网盘`

### 6.2 扫描效果参数

保留上游参数：

- 倾斜角度 `skew`
- 纸张色调 `paperTone`
- 噪点 `noise`
- 模糊 `blur`
- 边缘阴影 `edgeShadow`
- JPEG 质量 `jpegQuality`
- 渲染 DPI `dpi`
- 灰度 `grayscale`
- 随机种子 `seed`

### 6.3 参数预设

第一版提供三个预设：

- 推荐：默认选中，兼顾扫描质感、文字可读性和扫码可读性。
- 清晰：弱化噪点、模糊和压缩，适合含二维码/条码或小字的 PDF。
- 重度扫描：增强扫描感，提示可能影响二维码、条码识别。

默认预设应偏保守，不能为了视觉效果牺牲二维码、条码和小字号文字的可读性。

### 6.4 PDF 应用关联

必须在 `lzc-manifest.yml` 声明 PDF 文件关联：

```yaml
file_handler:
  mime:
    - application/pdf
    - x-lzc-extension/pdf
  actions:
    open: /?file=%u
```

应用必须支持：

- 解析 `file` 参数。
- 归一化懒猫网盘路径。
- 从 `/_lzc/files/home` 读取 PDF。
- 显示网盘文件名。
- 读取失败时给出明确中文错误。

### 6.5 懒猫网盘打开

应用需要支持两种打开方式：

- 传统 `<input type="file">` 选择本地 PDF。
- 通过懒猫文件选择器或 `file_handler` 读取网盘 PDF。

读取规则：

- 仅接受 PDF 文件。
- 文件类型不明确时，以 `.pdf` 后缀兜底判断。
- 读取失败不能白屏。

### 6.6 懒猫网盘保存

应用生成 PDF Blob 后，需要支持：

- 浏览器下载。
- 保存到懒猫网盘。

保存方式：

- 优先复用懒猫文件选择器 inject。
- 生成文件名默认为：`原文件名.scanned.pdf`。
- 保存路径由用户选择。
- 保存成功后提示保存路径。
- 保存失败时显示 HTTP 状态或可理解的错误信息。

### 6.7 预览

应用保留 PDF 预览区：

- 未生成时显示空状态。
- 生成后通过 Blob URL 在 iframe 中预览。
- 新选择文件时清空旧预览并释放旧 Blob URL。

### 6.8 性能与大文件提示

第一版不做复杂后台队列，但需要提示：

- 页数多时处理较慢。
- DPI 越高内存占用越大。
- 移动端或低性能设备建议使用默认 DPI。
- 大文件处理失败时提示用户降低 DPI 或减少页数。

## 7. 技术方案

### 7.1 上游实现事实

上游浏览器版本由以下能力组成：

- PDF.js 负责浏览器端 PDF 栅格化。
- Go WASM 负责扫描效果管线和 PDF 重新组装。
- JavaScript 调用 `mls.reset(params)` 初始化参数。
- JavaScript 逐页调用 `mls.addPage(rgba, width, height, widthPt, heightPt)`。
- JavaScript 调用 `mls.build()` 获得输出 PDF 字节。

### 7.2 改造原则

- 不重写 Go 效果管线。
- 不新增服务端转码。
- 不上传 PDF 到第三方服务。
- 不依赖 CDN，PDF.js、worker、WASM 均本地打包。
- Web 改造保持简单，优先完成懒猫生态能力。

### 7.3 静态资源部署

建议使用 nginx 静态服务承载：

```text
app/
├── index.html
├── main.wasm
├── wasm_exec.js
├── pdf.mjs
├── pdf.worker.mjs
└── lazycat-picker.html（如保存桥接需要）
```

`index.html` 中的 PDF.js 地址从 CDN 改为本地文件：

```js
window.MLS = {
  pdfjsModuleUrl: "/pdf.mjs",
  workerSrc: "/pdf.worker.mjs",
  wasmBytes: () => fetch("/main.wasm").then((r) => r.arrayBuffer()),
};
```

### 7.4 LPK 服务结构

建议结构：

```text
make-look-scanned-lpk/
├── lzc-build.yml
├── lzc-manifest.yml
├── package.yml
├── icon.png
├── Dockerfile
├── nginx.conf
├── content/
│   └── lazycat-injects/
│       └── lzc-file-chooser-inject.js
└── app/
    ├── index.html
    ├── main.wasm
    ├── wasm_exec.js
    ├── pdf.mjs
    └── pdf.worker.mjs
```

应用路由：

```yaml
application:
  routes:
    - /=http://web:8080
```

服务镜像：

```yaml
services:
  web:
    image: registry.lazycat.cloud/<developer>/make-look-scanned-web:<version>
```

### 7.5 镜像与 LPK 流程

本项目按用户指定的移植流程执行：

1. Web 改造。
2. 构建静态资源。
3. 构建 nginx 镜像。
4. 推送或通过 `lzc-cli appstore copy-image` 同步镜像。
5. 更新 `lzc-manifest.yml` 镜像地址。
6. 执行 `lzc-cli project build` 构建正式 LPK。
7. 执行 `lzc-cli lpk install` 本地安装正式 LPK。
8. 本地部署验证通过后上传提审。

### 7.6 权限

`package.yml` 至少需要：

```yaml
permissions:
  required:
    - document.read
    - document.write
```

如果实际保存或打开链路需要媒体权限，再补：

```yaml
    - media.read
    - media.write
```

### 7.7 许可证与来源

必须在应用资料和 package 元信息中标注：

- 上游作者：`overflowy`
- 上游地址：https://github.com/overflowy/make-look-scanned
- 许可证：AGPL-3.0

应用说明中应明确：浏览器版使用 PDF.js 栅格化 PDF，并调用 WASM 生成扫描效果。

## 8. LPK 构建与验证

### 8.1 构建资源

```bash
./web/build.sh
```

或：

```bash
task build:web
```

最终发布形态不建议只提交单文件 HTML，应拆为静态资源并纳入 nginx 镜像，突出懒猫应用集成能力。

### 8.2 构建镜像

```bash
docker build -t make-look-scanned-web:1.0.0 .
```

### 8.3 推送/同步镜像

```bash
lzc-cli appstore copy-image <公网镜像地址>
```

将输出的 `registry.lazycat.cloud/...` 镜像地址写入 `lzc-manifest.yml`。

### 8.4 构建 LPK

```bash
lzc-cli project build
```

### 8.5 本地安装

```bash
lzc-cli lpk install ./cloud.lazycat.app.make-look-scanned-v1.0.0.lpk
```

### 8.6 上传提审

```bash
lzc-cli appstore publish ./cloud.lazycat.app.make-look-scanned-v1.0.0.lpk
```

## 9. 验收标准

### 9.1 基础功能验收

- 应用可以正常打开。
- WASM 加载成功。
- PDF.js 加载成功。
- 本机 PDF 可以生成扫描版 PDF。
- 页面显示逐页处理进度。
- 生成结果可以预览。
- 生成结果可以下载。

### 9.2 汉化验收

- 页面主标题、说明、按钮、参数、状态、错误提示均为中文。
- 核心流程中不出现英文操作文案。
- 上游链接和许可证说明可保留英文项目名。

### 9.3 应用关联验收

- `application/pdf` 可触发本应用打开方式。
- `x-lzc-extension/pdf` 可触发本应用打开方式。
- 从懒猫网盘右键 PDF 能打开本应用。
- 应用能自动载入传入的网盘 PDF。
- 网盘读取失败时有中文错误提示。

### 9.4 网盘保存验收

- 生成结果可保存到懒猫网盘。
- 保存成功后网盘中能看到 PDF 文件。
- 保存后的 PDF 可再次打开查看。
- 保存失败时有明确提示。

### 9.5 扫码验收

- 使用含二维码的测试 PDF，默认“推荐”预设生成后，二维码可被常见手机扫码识别。
- 使用“清晰”预设生成后，二维码和条码应保持更高可读性。
- 使用“重度扫描”预设时，页面提示可能影响二维码/条码识别。
- 不承诺所有极小码、低分辨率码或复杂背景码在强效果下仍可识别。

### 9.6 LPK 验收

- `lzc-cli project build` 成功。
- `lzc-cli lpk install` 成功。
- 正式 LPK 安装后应用可访问。
- 镜像地址可拉取。
- package 元信息完整。
- 上游作者、上游地址、许可证信息完整。

## 10. 风险

### 10.1 审核风险

上游 Web 版是浏览器本地 WASM 工具，若只是简单打包单文件 HTML，可能被认为是“网页离线应用”或“功能过于简易”。因此必须强化：

- 中文化。
- PDF 文件应用关联。
- 懒猫网盘打开。
- 懒猫网盘保存。
- 使用说明和截图。

### 10.2 性能风险

大 PDF 或高 DPI 会增加浏览器内存和 CPU 压力。第一版通过默认参数、提示文案和失败提示降低风险，不做服务端队列。

### 10.3 扫码风险

噪点、模糊、低 JPEG 质量和倾斜会影响二维码、条码识别。第一版通过“推荐/清晰”预设和重度模式警告来控制风险，但不承诺所有码都可识别。

### 10.4 许可证风险

上游为 AGPL-3.0，分发改造版需要标注来源和许可证，并按许可证要求提供对应源码。

## 11. 里程碑

### M1：Web 改造

- 中文化页面。
- 本地化 PDF.js 资源。
- 保留 WASM 生成流程。
- 增加推荐、清晰、重度扫描预设。
- 增加扫码友好提示。

### M2：懒猫应用关联

- 配置 `file_handler`。
- 支持 `/?file=%u`。
- 支持从 `/_lzc/files/home` 读取 PDF。
- 验证网盘 PDF 右键打开。

### M3：网盘保存

- 接入懒猫文件选择器 inject。
- 支持保存生成 PDF 到网盘。
- 验证保存后可打开。

### M4：LPK 与本地验证

- 构建镜像。
- 推送或同步镜像。
- 构建正式 LPK。
- 本地 `lpk install`。
- 完成基础、汉化、应用关联、网盘保存、扫码验收。

### M5：提审

- 准备图标、截图、中文描述。
- 标注上游作者、源码地址和许可证。
- 上传 LPK 提审。

## 12. 待确认事项

- 最终应用名使用 `PDF 伪扫描`、`PDF扫描件生成器`，还是其他名称。
- 是否必须增加独立“保存到懒猫网盘”按钮，还是仅依赖文件选择器 inject 拦截下载。
- 是否使用外部推送镜像作为唯一方案，或允许后续改为内嵌镜像以简化提审。
- 是否需要准备一份含二维码的测试 PDF 样例用于扫码验收。
