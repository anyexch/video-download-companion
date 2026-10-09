#define MyAppName "Video Download Companion"
#define MyAppVersion "0.1.15"
#define MyAppExeName "VideoDownloadCompanion.exe"

[Setup]
AppId={{5F8A9B9F-8DF0-4DB0-A155-95F102673822}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\VideoDownloadCompanion
DefaultGroupName=Video Download Companion
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=Video-Download-Companion-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern dynamic
SetupLogging=yes
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "登录 Windows 后自动启动 Companion"; Flags: checkedonce

[Files]
Source: "staging\VideoDownloadCompanion.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "staging\scripts\download_douyin.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "staging\scripts\refresh_douk_cookie.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "staging\installer\install-dependencies.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "staging\installer\stop-existing.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "languages\ChineseSimplified.LICENSE"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{group}\Video Download Companion"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\第三方组件声明"; Filename: "{app}\THIRD_PARTY_NOTICES.md"
Name: "{group}\卸载 Video Download Companion"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "VideoDownloadCompanion"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\stop-existing.ps1"""; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "启动 Video Download Companion"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#MyAppExeName} /T /F"; Flags: runhidden waituntilterminated; RunOnceId: "StopCompanion"

[Code]
var
  MediaPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  MediaPage := CreateInputDirPage(wpSelectDir,
    '选择视频保存目录',
    '选择最终视频保存根目录。',
    '安装器会在其中创建 YouTube-Bilibili 和 Douyin 子目录。程序升级或卸载不会删除视频。',
    False, '');
  MediaPage.Add('视频目录：');
  if GetEnv('USERPROFILE') <> '' then
    MediaPage.Values[0] := AddBackslash(GetEnv('USERPROFILE')) + 'Downloads\Video Downloads'
  else
    MediaPage.Values[0] := ExpandConstant('{userdocs}\Video Downloads');
end;

function GetMediaRoot(Param: String): String;
begin
  Result := MediaPage.Values[0];
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = MediaPage.ID) and (Trim(MediaPage.Values[0]) = '') then
  begin
    MsgBox('请选择视频保存目录。', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Parameters: String;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := '正在下载并校验 yt-dlp、FFmpeg、Deno 和 DouK；详细进度显示在 PowerShell 窗口中…';
    Parameters := '-NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{app}\installer\install-dependencies.ps1') + '" -InstallDir "' +
      ExpandConstant('{app}') + '" -MediaRoot "' + GetMediaRoot('') + '"';
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Parameters,
      ExpandConstant('{app}'), SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      RaiseException('无法启动依赖安装程序。')
    else if ResultCode <> 0 then
      RaiseException('依赖下载或校验失败，退出代码：' + IntToStr(ResultCode) +
        '。详细日志：' + ExpandConstant('{localappdata}\YouTubeYtDlpBridge\dependency-install.log'));
  end;
end;
