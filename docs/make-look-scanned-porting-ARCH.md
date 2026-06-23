# PDF 伪扫描懒猫微服移植 ARCH

**对应 PRD**：`docs/make-look-scanned-porting-PRD.md`  
**上游项目**：https://github.com/overflowy/make-look-scanned  
**目标包名**：`cloud.lazycat.app.make-look-scanned`  
**文档版本**：0.1.0  
**日期**：2026-06-23

## 1. 技术路线总览

本项目按“先程序改造，后 LPK”的顺序实施：

1. 拉取并固定上游 `make-look-scanned` 代码。
2. 基于上游 Web/WASM 版本做程序改造。
3. 完成汉化、扫码友好预设、本地资源化、网盘打开、网盘保存和 PDF 应用关联。
4. 用浏览器本地静态服务验证核心功能。
5. 功能核对通过后，再构建 nginx 镜像。
6. 推送或同步镜像到懒猫可拉取仓库。
7. 生成正式 LPK。
8. 使用 `lzc-cli lpk install` 本地安装验证。
9. 验证通过后上传提审。

本路线不做服务端 PDF 转换，不改写 Go 效果算法，不把文件上传到外部服务。

## 2. 上游架构事实

上游浏览器版由三段组成：

- `web/index.html`：页面、参数表单、PDF.js 渲染、WASM 调用、Blob 下载和预览。
- `web/build.sh`：构建 `web/main.wasm` 并复制 Go 的 `wasm_exec.js`。
- `cmd/wasm/main_js.go`：在浏览器全局注册 `mls` 对象，提供 `reset`、`addPage`、`build` 三个 JS API。

核心调用链：

```text
用户选择 PDF
  -> PDF.js getDocument(data)
  -> 逐页 page.render(canvas)
  -> ctx.getImageData()
  -> mls.reset(params)
  -> mls.addPage(rgba, width, height, widthPt, heightPt)
  -> mls.build()
  -> Blob(application/pdf)
  -> 预览 / 下载 / 保存到网盘
```

Go WASM 暴露的接口：

```text
mls.reset(params) -> { error }
mls.addPage(rgba, w, h, widthPt, heightPt) -> { error }
mls.build() -> { error, pdf: Uint8Array }
```

第一版必须保留这个调用链，只在 Web 壳、懒猫集成和打包方式上改造。

## 3. 目标模块划分

### 3.1 Web 应用壳

职责：

- 承载页面布局。
- 提供中文 UI。
- 管理文件选择、参数、预设、进度、预览和错误提示。
- 调用 PDF.js 和 WASM。
- 生成 PDF Blob。

建议文件：

```text
app/index.html
app/main.js
app/styles.css
```

如果为了保持简单，也可以继续使用单个 `index.html`，但逻辑需要按职责分区并保留注释。

### 3.2 WASM 适配层

职责：

- 加载 `wasm_exec.js`。
- 加载 `main.wasm`。
- 等待全局 `mls` 可用。
- 将 UI 参数转换为上游 WASM 所需字段。

参数字段必须与上游保持一致：

```js
{
  dpi,
  skew,
  grayscale,
  paperTone,
  noise,
  blur,
  edgeShadow,
  jpegQuality,
  seed
}
```

### 3.3 PDF 渲染层

职责：

- 使用 PDF.js 打开 PDF。
- 按 DPI 逐页渲染到 Canvas。
- 提取 RGBA 像素。
- 传给 WASM。

约束：

- PDF.js 和 worker 必须本地化，不能依赖 CDN。
- 处理过程中展示页码进度。
- 大文件或高 DPI 失败时提示用户降低 DPI。

### 3.4 懒猫网盘打开层

职责：

- 解析 URL 参数 `file`。
- 归一化懒猫网盘路径。
- 通过 `/_lzc/files/home` 读取 PDF Blob。
- 构造浏览器 `File` 对象并进入统一处理流程。

路径归一化规则：

```js
function normalizeLazyCatPath(path) {
  let normalized = String(path || "").trim().replace(/\.$/, "");
  normalized = normalized.replace(/^\/_lzc\/files\/home(?=\/|$)/, "");
  if (normalized && !normalized.startsWith("/")) normalized = "/" + normalized;
  return normalized;
}
```

统一入口建议：

```text
loadFile(file, source)
```

`source` 可取：

- `local`
- `lazycat-file-handler`
- `lazycat-picker`

### 3.5 懒猫网盘保存层

职责：

- 接收生成后的 PDF Blob。
- 支持浏览器下载。
- 支持保存到懒猫网盘。
- 保存成功后显示路径。
- 保存失败时显示可理解的错误。

第一版优先复用懒猫文件选择器 inject。若普通页面直接保存不稳定，再引入非 COEP 桥接页：

```text
index.html
  -> IndexedDB 暂存 PDF Blob
  -> 打开 lazycat-picker.html?mode=save&saveId=...
  -> picker 选择保存路径
  -> bridge PUT /_lzc/files/home/...
  -> GET 校验保存结果
  -> 通知主页面
```

### 3.6 懒猫应用关联层

职责：

- 在 `lzc-manifest.yml` 声明 PDF 文件处理能力。
- 让懒猫网盘可将 PDF 打开到本应用。

目标配置：

```yaml
file_handler:
  mime:
    - application/pdf
    - x-lzc-extension/pdf
  actions:
    open: /?file=%u
```

### 3.7 LPK 运行层

职责：

- 使用 nginx 提供静态资源。
- 通过 `application.routes` 暴露 Web 入口。
- 通过 `application.injects` 注入懒猫文件选择器。
- 通过 `package.yml` 声明权限、来源、许可证和元数据。

目标路由：

```yaml
application:
  routes:
    - /=http://web:8080
```

## 4. 实施顺序

### 阶段 A：程序改造

目标：先让应用在普通浏览器静态服务下好用。

任务：

- 固定上游代码版本。
- 构建 `main.wasm` 和 `wasm_exec.js`。
- 将 PDF.js 从 CDN 改为本地资源。
- 完成页面汉化。
- 增加“推荐、清晰、重度扫描”预设。
- 增加扫码友好提示。
- 抽出统一 `loadFile(file, source)`。
- 保留本机 PDF 选择和 Blob 下载。

阶段出口：

- 本地静态服务打开页面无白屏。
- 本机 PDF 可生成。
- 默认/清晰预设下含二维码 PDF 生成后仍可扫码。
- 页面核心流程无英文文案。

### 阶段 B：应用关联与网盘打开

目标：先完成 PDF 文件打开链路。

任务：

- 实现 `/?file=%u` 参数解析。
- 实现懒猫路径归一化。
- 实现 `fetch("/_lzc/files/home" + path)` 读取 PDF。
- 读取后构造 `File` 并交给 `loadFile`。
- 准备 `lzc-manifest.yml` 的 `file_handler` 配置。

阶段出口：

- 通过 URL 模拟 `?file=/xxx.pdf` 时能进入读取流程。
- 在懒猫环境中右键 PDF 能调起本应用。
- 读取失败有中文提示。

### 阶段 C：网盘保存

目标：生成结果能保存回懒猫网盘。

任务：

- 引入或复制懒猫文件选择器 inject。
- 增加“保存到懒猫网盘”按钮。
- 将生成的 PDF Blob 交给保存层。
- 优先实现前端 `PUT /_lzc/files/home...`。
- 如主页面受隔离策略影响，再使用 `lazycat-picker.html` 桥接页。

阶段出口：

- 生成 PDF 可下载到本机。
- 生成 PDF 可保存到懒猫网盘。
- 保存后文件能在网盘中打开。

### 阶段 D：LPK 与镜像

目标：完成正式包。

任务：

- 编写 Dockerfile 和 nginx 配置。
- 构建静态 nginx 镜像。
- 推送或用 `lzc-cli appstore copy-image` 同步镜像。
- 更新 `lzc-manifest.yml` 镜像地址。
- 补齐 `package.yml` 元数据、权限、上游地址、许可证。
- 执行 `lzc-cli project build`。

阶段出口：

- 构建产出正式 `.lpk`。
- `lzc-cli lpk info` 可读取包信息。

### 阶段 E：本地 LPK 安装验证

目标：按用户安装路径验证，而不是只用开发部署。

任务：

- 执行 `lzc-cli lpk install ./cloud.lazycat.app.make-look-scanned-v1.0.0.lpk`。
- 打开安装后的正式入口。
- 执行完整验收。
- 修复问题后重新 build/install。

阶段出口：

- 正式 LPK 安装后功能可用。
- 应用关联、网盘保存和扫码验收通过。
- 可上传提审。

## 5. 关键配置设计

### 5.1 `package.yml`

必须包含：

- `package: cloud.lazycat.app.make-look-scanned`
- `name`
- `version`
- `description`
- `author`
- `homepage`
- 中文 locales
- 上游地址
- AGPL-3.0 许可证说明
- 文件读写权限

建议权限：

```yaml
permissions:
  required:
    - document.read
    - document.write
```

如测试发现文件选择器或保存场景需要媒体权限，再增加：

```yaml
    - media.read
    - media.write
```

### 5.2 `lzc-manifest.yml`

必须包含：

- HTTP route。
- `web` 服务。
- PDF `file_handler`。
- 文件选择器 inject。

示意：

```yaml
application:
  routes:
    - /=http://web:8080
  injects:
    - id: open-save-chooser
      on: browser
      when:
        - /*
      do:
        - src: file:///lzcapp/pkg/content/lazycat-injects/lzc-file-chooser-inject.js
          params:
            diskRoot: /_lzc/files/home
            fallbackMime: application/pdf
            locale: auto
            hooks:
              fileInput: true
              fileSystemAccess: true

file_handler:
  mime:
    - application/pdf
    - x-lzc-extension/pdf
  actions:
    open: /?file=%u

services:
  web:
    image: registry.lazycat.cloud/<developer>/make-look-scanned-web:<version>
```

### 5.3 `lzc-build.yml`

必须包含：

- 图标。
- `contentdir`，用于打入 inject。

示意：

```yaml
icon: ./icon.png
contentdir: ./content
```

如最终改为内嵌镜像，再补 `images` 字段；当前路线按“先推送镜像再 LPK”执行。

## 6. 数据流

### 6.1 本机文件数据流

```text
input[type=file]
  -> File
  -> loadFile(file, "local")
  -> Uint8Array(file.arrayBuffer())
  -> pdfjsLib.getDocument({ data })
  -> canvas RGBA
  -> mls.addPage()
  -> mls.build()
  -> Blob PDF
  -> iframe preview / download / saveToLazyCatDrive
```

### 6.2 网盘关联数据流

```text
懒猫网盘 PDF
  -> file_handler open: /?file=%u
  -> URLSearchParams.get("file")
  -> normalizeLazyCatPath()
  -> fetch("/_lzc/files/home" + path)
  -> Blob
  -> File
  -> loadFile(file, "lazycat-file-handler")
  -> 统一扫描流程
```

### 6.3 网盘保存数据流

```text
mls.build()
  -> Uint8Array
  -> Blob(application/pdf)
  -> saveToLazyCatDrive(blob, filename)
  -> 选择路径
  -> PUT /_lzc/files/home/<target>
  -> GET 校验
  -> UI 提示保存成功
```

## 7. 扫码友好设计

扫码友好不是二维码识别功能，而是输出质量约束。

### 7.1 默认参数原则

默认参数应满足：

- 噪点不过强。
- 模糊较低。
- JPEG 质量不低于 70。
- DPI 默认不低于 150。
- 倾斜角度较小。

### 7.2 预设建议

推荐：

```text
skew: 0.6
paperTone: 0.5
noise: 0.06
blur: 0.25
edgeShadow: 0.12
jpegQuality: 80
dpi: 150
grayscale: true
```

清晰：

```text
skew: 0.2
paperTone: 0.25
noise: 0.02
blur: 0
edgeShadow: 0.05
jpegQuality: 92
dpi: 180
grayscale: true
```

重度扫描：

```text
skew: 1.5
paperTone: 0.7
noise: 0.18
blur: 0.55
edgeShadow: 0.3
jpegQuality: 55
dpi: 150
grayscale: true
```

重度扫描预设必须展示提示：该模式可能影响二维码、条码和小字识别。

## 8. 错误处理

必须中文提示以下错误：

- WASM 加载失败。
- PDF.js 加载失败。
- 未选择 PDF。
- 文件不是 PDF。
- 网盘文件路径无效。
- 网盘文件读取失败。
- PDF 渲染失败。
- WASM 生成失败。
- 保存到懒猫网盘失败。
- 文件过大或内存不足时建议降低 DPI。

错误提示要保留底层错误摘要，方便排查。

## 9. 代码核对清单

程序改造完成后按以下代码点核对：

### 9.1 上游调用链

- `mls.reset()` 调用前参数字段没有改名。
- `mls.addPage()` 传入的是 RGBA、像素宽高和原始 pt 宽高。
- `mls.build()` 返回的 `pdf` 被包装为 `application/pdf` Blob。
- 新增预设只改变参数，不改 Go WASM 内部逻辑。

### 9.2 本地资源化

- `pdfjsModuleUrl` 指向本地 `pdf.mjs`。
- `workerSrc` 指向本地 `pdf.worker.mjs`。
- `wasmBytes()` 指向本地 `main.wasm`。
- 页面不依赖 CDN 才能完成主流程。

### 9.3 汉化

- 页面可见主流程文案均为中文。
- 错误提示为中文。
- 处理进度为中文。
- 保留上游项目名和许可证链接不算未汉化。

### 9.4 应用关联

- `lzc-manifest.yml` 包含 `application/pdf`。
- `lzc-manifest.yml` 包含 `x-lzc-extension/pdf`。
- `actions.open` 为 `/?file=%u`。
- 前端启动时会检查 `file` 参数。
- 路径归一化不会重复拼接 `/_lzc/files/home`。

### 9.5 网盘保存

- 生成 PDF Blob 后才允许保存。
- 默认文件名以 `.scanned.pdf` 结尾。
- `PUT /_lzc/files/home...` 路径经过归一化。
- 保存成功后执行读取或大小校验。
- 保存失败不清空已生成结果。

### 9.6 LPK

- `package.yml` 包名与开发者中心包标识符一致。
- `lzc-manifest.yml` 服务名与 route 一致。
- 镜像地址是懒猫目标环境可拉取地址。
- `contentdir` 能把 inject 打进 LPK。
- `package.yml` 标注上游作者、源码地址和 AGPL-3.0。

## 10. 构建与部署命令

程序阶段：

```bash
./web/build.sh
```

镜像阶段：

```bash
docker build -t make-look-scanned-web:1.0.0 .
lzc-cli appstore copy-image <公网镜像地址>
```

LPK 阶段：

```bash
lzc-cli project build
lzc-cli lpk info ./cloud.lazycat.app.make-look-scanned-v1.0.0.lpk
lzc-cli lpk install ./cloud.lazycat.app.make-look-scanned-v1.0.0.lpk
```

提审阶段：

```bash
lzc-cli appstore publish ./cloud.lazycat.app.make-look-scanned-v1.0.0.lpk
```

## 11. 决策记录

- 先做程序改造，后做 LPK，避免打包后再反复修基础交互。
- 第一版走浏览器本地处理，不新增服务端转换。
- 应用关联是必须项，不作为后续增强。
- 汉化是必须项，不接受只翻译标题。
- 扫码友好通过默认参数、清晰预设和验收样例保障，不做二维码识别。
- 镜像路线按推送/同步镜像执行；只有在提审或拉取受阻时再考虑内嵌镜像。
