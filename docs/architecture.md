# Architecture

```text
Chrome page
   │ Ctrl+Shift+Y
   ▼
Manifest V3 extension
   │ normalized URL + local token
   ▼
127.0.0.1 HTTP API
   │ durable SQLite transaction
   ▼
Scheduler ───────────────► yt-dlp ─► YouTube / Bilibili
   │
   └─────────────────────► DouK   ─► Douyin
```

## Extension

扩展识别当前标签页是否为支持的视频或作品页面，生成规范链接并提交给本机 API。它不持有下载进程，也不读取平台 Cookie。

## Listener and scheduler

监听器默认只绑定回环地址。任务先写入 SQLite 并提交，API 才返回已接收。调度器负责去重、并发限制、进度、自动重试和重启恢复。

## Download backends

YouTube 和 Bilibili 共享 yt-dlp 后端，但可以分别设置代理与浏览器 Cookie 来源。Douyin 使用独立的 DouK 后端，并保持单实例以避免竞争它的配置和记录文件。

## Companion

Windows 托盘程序管理监听器、设置、下载目录、依赖版本检测以及受控更新。外置依赖不会被自动覆盖；用户明确选择纳管后，候选版本仍需通过来源、摘要和运行自检，配置才会切换。

## Runtime data

默认状态目录是 `%LOCALAPPDATA%\YouTubeYtDlpBridge`。配置、令牌、SQLite 数据库、日志和更新事务不属于源码仓库，也不能进入 Issue 或测试样例。
