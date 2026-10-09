# Release checklist

- [ ] 确认 `companion/companion.py`、安装器和 User-Agent 使用同一版本号。
- [ ] 运行全部 Python、Node.js 和编译检查。
- [ ] 在全新 Windows 用户环境测试首次安装、扩展加载和三平台各一条任务。
- [ ] 验证更新检查、显式纳管、失败恢复和回退。
- [ ] 重新核对第三方下载 URL、资产名、版本、许可证和 SHA-256。
- [ ] 扫描令牌、Cookie、私钥、个人路径、私网地址、日志和数据库。
- [ ] 检查 Git 中没有 EXE、ZIP、虚拟环境或 PyInstaller 构建目录。
- [ ] 生成安装包并记录 SHA-256。
- [ ] 决定是否进行 Authenticode 签名；未签名时在 Release 中明确说明 SmartScreen 提示。
- [ ] 更新 `CHANGELOG.md` 和 README 中的版本状态。
- [x] 项目自有源码采用 MIT License。
- [ ] 确认第三方组件的声明、源代码提供和再分发义务。
- [ ] 只把二进制上传到 GitHub Releases，不提交到源码分支。
