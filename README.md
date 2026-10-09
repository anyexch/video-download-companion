# Video Download Companion

Windows 本地视频下载工具：在 Chrome 中捕获当前 YouTube、Bilibili 或 Douyin 作品，将规范化链接提交给本机持久化队列，再分别交给 yt-dlp 或 DouK 下载。

当前源码版本：`0.1.15`。这是准备公开维护的首个源码仓库版本；正式安装包发布前仍需完成全新 Windows 环境安装验收、第三方分发复核和代码签名决策。

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
