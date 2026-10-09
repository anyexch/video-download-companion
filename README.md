# Video Download Companion

Windows 本地视频下载工具：在 Chrome 中捕获当前 YouTube、Bilibili 或 Douyin 作品，将规范化链接提交给本机持久化队列，再分别交给 yt-dlp 或 DouK 下载。

当前版本：Companion `0.1.15`、Chrome 扩展 `0.6.0`。Windows 安装包已作为公开候选版发布；它尚未进行 Authenticode 签名，也尚未完成全新 Windows 虚拟机验收，请先阅读下面的安装说明和已知限制。

## 功能

- Chrome Manifest V3 扩展，默认快捷键 `Ctrl+Shift+Y`。
- 支持 YouTube、Bilibili、Douyin 视频或作品链接识别与规范化。
- SQLite 持久化任务队列、并发限制、失败重试和重启恢复。
- YouTube／Bilibili 使用 yt-dlp，Douyin 使用 DouK。
- 扩展弹窗显示等待、运行、进度、完成和失败状态。
- 完成任务可用系统默认程序打开，或在资源管理器中定位。
- Companion 托盘程序提供设置、启动／停止、依赖检测、更新、纳管和回退。
- 自定义依赖路径仅检测，不会被静默覆盖；显式纳管时保留外置原件并支持失败恢复。

## 结构

```text
companion/   Windows 托盘程序、依赖更新器和安装器构建脚本
extension/   Chrome Manifest V3 扩展
listener/    本机 HTTP API、SQLite 队列和下载后端路由
tests/       Python、Node.js 与 PowerShell 测试
docs/        架构和维护说明
```

浏览器扩展只负责捕获链接和显示任务状态。下载进程、Cookie 读取、队列和文件操作全部留在本机监听器中。完整数据流见 [架构说明](docs/architecture.md)。

## 下载、安装与使用（推荐）

### 1. 下载 Windows 安装包

前往 [`v0.1.15` Release](https://github.com/anyexch/video-download-companion/releases/tag/v0.1.15)，下载：

- `Video-Download-Companion-Setup-0.1.15.exe`：Windows 联网安装器。
- `video-download-companion-extension-0.6.0.zip`：Chrome 扩展。
- `SHA256SUMS.txt`：文件完整性校验值。

安装器会联网下载并校验 yt-dlp、FFmpeg、Deno 和 DouK，因此首次安装需要能够访问这些项目的 GitHub Release。安装器目前没有代码签名；如果 Windows SmartScreen 显示“未知发布者”，请先确认下载地址属于本仓库并核对 SHA-256，再选择“更多信息”→“仍要运行”。

可在 PowerShell 中校验安装器：

```powershell
Get-FileHash .\Video-Download-Companion-Setup-0.1.15.exe -Algorithm SHA256
```

结果应与同一 Release 中 `SHA256SUMS.txt` 的对应记录一致。

### 2. 运行安装器

1. 双击安装器，选择视频保存目录；默认会建立 `YouTube-Bilibili` 和 `Douyin` 两个子目录。
2. 根据需要保留“登录 Windows 后自动启动 Companion”选项。
3. 等待依赖下载和哈希校验完成，不要关闭安装过程中出现的 PowerShell 窗口。
4. 首次安装会打开 DouK。请自行选择语言、阅读并接受其免责声明；需要抖音登录状态时，可按窗口提示从已登录的浏览器读取 Cookie。
5. 安装结束后，系统托盘应出现 Video Download Companion 图标，菜单第一行应显示“调度器：在线”。

程序默认安装到 `%LOCALAPPDATA%\Programs\VideoDownloadCompanion`，配置、日志和任务数据库位于 `%LOCALAPPDATA%\YouTubeYtDlpBridge`。升级或卸载不会删除已经下载的视频。

### 3. 安装 Chrome 扩展

1. 解压 `video-download-companion-extension-0.6.0.zip` 到一个长期保留的目录；不要直接从 ZIP 加载。
2. 打开 `chrome://extensions/`，启用右上角“开发者模式”。
3. 选择“加载已解压的扩展程序”，选中解压后包含 `manifest.json` 的目录。
4. 打开扩展的“详情”→“扩展程序选项”。
5. 在 PowerShell 中读取安装器生成的本地接口令牌：

   ```powershell
   (Get-Content "$env:LOCALAPPDATA\YouTubeYtDlpBridge\config.json" -Raw | ConvertFrom-Json).server.auth_token
   ```

6. 将令牌粘贴到扩展选项的“本地接口令牌”，保存并点击“测试连接”。令牌只用于浏览器扩展连接本机 `127.0.0.1:17392`，不要公开分享。

### 4. 下载视频

1. 打开支持的 YouTube、Bilibili 或 Douyin 视频／作品页面。
2. 按 `Ctrl+Shift+Y`，或点击扩展图标后提交当前页面。
3. 扩展弹窗会显示排队、下载进度、完成或失败状态；完成后可以直接打开文件或在资源管理器中定位。
4. 下载目录、代理、Cookie 浏览器和依赖更新可从系统托盘图标的“打开设置”或相关菜单调整。

常见问题：

- 扩展提示连接失败：确认托盘中的调度器在线，并重新核对令牌。
- YouTube／Bilibili 登录内容失败：先在设置中选择已登录的浏览器；Chrome Cookie 数据库被占用时可关闭 Chrome 后重试，或使用 Firefox 回退。
- 抖音失败：从托盘菜单执行 DouK Cookie 刷新，并按窗口提示完成浏览器 Cookie 读取。
- 安装失败：查看 `%LOCALAPPDATA%\YouTubeYtDlpBridge\dependency-install.log`。
- 下载失败：从托盘菜单打开日志，或查看 `%LOCALAPPDATA%\YouTubeYtDlpBridge\listener.log`。

## 从源码运行

要求：

- Windows 10/11
- Python 3.11 或更新版本
- Chrome 或 Chromium 浏览器
- yt-dlp 与 FFmpeg
- Node.js 或 Deno，用于 yt-dlp 的 JavaScript challenge
- DouK，仅在下载 Douyin 内容时需要

在 PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./listener/setup.ps1
```

该脚本会在 `%LOCALAPPDATA%\YouTubeYtDlpBridge\config.json` 创建配置，并生成随机本地接口令牌。随后检查并修改配置中的 yt-dlp、FFmpeg 和 DouK 路径，再启动监听器：

```powershell
./listener/start-listener.ps1
```

加载扩展：

1. 打开 `chrome://extensions/` 并启用开发者模式。
2. 选择“加载已解压的扩展程序”，指向本仓库的 `extension` 目录。
3. 打开扩展选项，将配置文件中的 `server.auth_token` 粘贴到“本地接口令牌”。
4. 测试连接后，在支持的视频页面按 `Ctrl+Shift+Y`。

默认仅监听 `127.0.0.1:17392`，不应把该服务暴露到局域网或互联网。

## 测试

```powershell
python -m unittest discover -s tests -p "test_*.py"
node tests/test-background.mjs
node tests/test-popup.mjs
python -m compileall -q companion listener tests
```

当前基线为 54 项 Python 测试，以及后台脚本和弹窗的 Node.js 测试。

## 构建 Windows 安装包

要求 Python 3.11+ 与 Inno Setup 6.7+：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\companion\installer\build-installer.ps1
```

生成内容位于 `dist/installer/`，该目录由 Git 忽略。EXE、ZIP 和历史构建不进入源码提交；公开二进制应通过 GitHub Releases 发布，并附 SHA-256、版本说明和验收结果。

生成完整 Release 资产：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-release.ps1
```

该脚本会生成安装器、Chrome 扩展 ZIP、第三方声明和 `SHA256SUMS.txt`。

正式发布前应在一次性 Windows 虚拟机中执行[全新安装、升级与卸载验收](docs/testing/windows-clean-install.md)。

首次公开安装包发布前请完整执行 [发布检查清单](RELEASE_CHECKLIST.md)。当前安装器仍会联网下载并校验第三方依赖，依赖版本与哈希必须在发布时重新核对。

## 安全与隐私

- 扩展不读取、导出或上传平台 Cookie。
- Cookie 由本机 yt-dlp 或 DouK 直接读取。
- 本机 API 要求扩展来源与随机共享令牌。
- 监听器只接受支持平台的视频／作品 URL。
- 配置、Cookie、任务数据库、日志和下载文件不应提交到 Git。
- 完成任务的打开／定位接口只处理队列中已经记录的文件路径。

漏洞报告方式见 [SECURITY.md](SECURITY.md)。

## 第三方组件

本项目调用 yt-dlp、Deno、FFmpeg 和 DouK 等独立程序。分发安装包时必须遵守它们各自的许可证和源代码提供义务，详见 [第三方声明](companion/installer/THIRD_PARTY_NOTICES.md)。

## 许可证

本项目自有源码采用 [MIT License](LICENSE)。yt-dlp、Deno、FFmpeg 和 DouK 等第三方组件继续适用各自的许可证；发布包含或自动获取这些组件的安装包前，仍需核对对应版本的声明、源代码提供和再分发义务。
