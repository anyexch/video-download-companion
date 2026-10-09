# Windows clean-install test

Use a disposable Windows 10/11 virtual machine or Windows Sandbox. Do not test a first-install package over the maintainer's working installation.

## Preparation

- Copy the complete `dist/release/v0.1.15` directory into the clean machine.
- Verify the installer and extension ZIP against `SHA256SUMS.txt`.
- Ensure Chrome or Chromium and a supported browser profile are available.
- Take a VM snapshot before installation.

## Installation

1. Run `Video-Download-Companion-Setup-0.1.15.exe` as the normal user.
2. Confirm the displayed version is 0.1.15 and choose a disposable media directory.
3. Allow the verified dependency downloads to finish.
4. When DouK opens, manually choose the language, read and accept its disclaimer, then exit. Do not automate this consent.
5. Confirm the installer completes and the tray application starts.

## Automated verification

Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\verify-installed.ps1
```

The result must report app version 0.1.15, a healthy listener, the expected dependency versions, a matching DouK hash and an existing DouK `Volume` directory. The script reports only whether a token exists; it does not print the token.

## Extension verification

1. Extract `video-download-companion-extension-0.6.0.zip`.
2. Load it from `chrome://extensions/` with developer mode enabled.
3. Copy the local token from `%LOCALAPPDATA%\YouTubeYtDlpBridge\config.json` into the extension options.
4. Test the connection.
5. Submit one permitted URL from each platform available to the tester and verify queue, progress, completion or an explainable authentication failure.

## Upgrade and uninstall

- Re-run the installer over the same VM installation and verify the token, queue database and DouK `Volume` data remain intact.
- Uninstall from Windows Settings.
- Confirm the application and autostart entry are removed.
- Confirm downloaded media and the state directory are not silently deleted.
- Restore the VM snapshot when finished.
