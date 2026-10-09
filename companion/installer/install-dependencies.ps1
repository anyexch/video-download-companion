[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$InstallDir,
    [Parameter(Mandatory = $true)] [string]$MediaRoot,
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge'),
    [switch]$ProgressTestOnly,
    [switch]$MetadataTestOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
$install = [System.IO.Path]::GetFullPath($InstallDir)
$media = [System.IO.Path]::GetFullPath($MediaRoot)
$tools = Join-Path $install 'tools'
$scripts = Join-Path $install 'scripts'
$state = [System.IO.Path]::GetFullPath($StateDir)
$stage = Join-Path $tools '.install-stage'
$installLog = Join-Path $state 'dependency-install.log'
$volumeBackup = $null
$installedVolume = $null

try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $Host.UI.RawUI.WindowTitle = 'Video Download Companion - 正在安装依赖'
} catch {}

try {
    [System.Net.ServicePointManager]::SecurityProtocol =
        [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls12
} catch {}

if (-not $install.StartsWith([System.IO.Path]::GetFullPath($env:LOCALAPPDATA), [System.StringComparison]::OrdinalIgnoreCase) -and
    -not $install.StartsWith([System.IO.Path]::GetFullPath($env:ProgramFiles), [System.StringComparison]::OrdinalIgnoreCase)) {
    Write-Output "使用自定义安装目录：$install"
}

function Format-ByteCount {
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ('{0:N1} KB' -f ($Bytes / 1KB)) }
    return "$Bytes B"
}

function Get-VerifiedFile {
    param(
        [Parameter(Mandatory = $true)] [string]$Name,
        [Parameter(Mandatory = $true)] [int]$Step,
        [Parameter(Mandatory = $true)] [string]$Url,
        [Parameter(Mandatory = $true)] [string]$Destination,
        [Parameter(Mandatory = $true)] [string]$Sha256
    )

    $partial = "$Destination.partial"
    Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
    $request = $null
    $response = $null
    $inputStream = $null
    $outputStream = $null
    $completed = $false
    $activity = "[$Step/4] 下载 $Name"
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $nextConsolePercent = 0
    $lastUnknownReport = [TimeSpan]::Zero

    Write-Host ''
    Write-Host $activity -ForegroundColor Cyan
    Write-Host "  来源：$Url" -ForegroundColor DarkGray

    try {
        $request = [System.Net.HttpWebRequest]::Create($Url)
        $request.AllowAutoRedirect = $true
        $request.UserAgent = 'VideoDownloadCompanion-Installer/0.1.15'
        $response = $request.GetResponse()
        $totalBytes = [long]$response.ContentLength
        $inputStream = $response.GetResponseStream()
        $outputStream = [System.IO.File]::Open($partial, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $buffer = New-Object byte[] (1MB)
        [long]$downloaded = 0

        while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $outputStream.Write($buffer, 0, $read)
            $downloaded += $read
            $elapsedSeconds = [Math]::Max($stopwatch.Elapsed.TotalSeconds, 0.1)
            $speed = Format-ByteCount ([long]($downloaded / $elapsedSeconds))

            if ($totalBytes -gt 0) {
                $percent = [Math]::Min(100, [int][Math]::Floor(($downloaded * 100.0) / $totalBytes))
                $status = '{0}%  {1} / {2}  {3}/s' -f $percent, (Format-ByteCount $downloaded), (Format-ByteCount $totalBytes), $speed
                Write-Progress -Id 1 -Activity $activity -Status $status -PercentComplete $percent
                if ($percent -ge $nextConsolePercent) {
                    Write-Host ('  {0,3}%  {1} / {2}  {3}/s' -f $percent, (Format-ByteCount $downloaded), (Format-ByteCount $totalBytes), $speed)
                    $nextConsolePercent = ([Math]::Floor($percent / 10) + 1) * 10
                }
            } else {
                $status = '{0}  {1}/s' -f (Format-ByteCount $downloaded), $speed
                Write-Progress -Id 1 -Activity $activity -Status $status -PercentComplete 0
                if (($stopwatch.Elapsed - $lastUnknownReport).TotalSeconds -ge 5) {
                    Write-Host ('  已下载 {0}  {1}/s' -f (Format-ByteCount $downloaded), $speed)
                    $lastUnknownReport = $stopwatch.Elapsed
                }
            }
        }

        $outputStream.Flush()
        $outputStream.Dispose()
        $outputStream = $null
        Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $partial -Destination $Destination
        $completed = $true
    }
    finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
        $stopwatch.Stop()
        Write-Progress -Id 1 -Activity $activity -Completed
        if (-not $completed) {
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host '  正在校验 SHA-256…' -ForegroundColor Yellow
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash
    if ($actual -ne $Sha256.ToUpperInvariant()) {
        Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
        throw "$Name 下载文件校验失败。`n期望：$Sha256`n实际：$actual"
    }
    Write-Host "  $Name 下载与校验完成。" -ForegroundColor Green
}

function Get-GitHubReleaseAssetMetadata {
    param(
        [Parameter(Mandatory = $true)] [string]$Owner,
        [Parameter(Mandatory = $true)] [string]$Repository,
        [Parameter(Mandatory = $true)] [string]$Tag,
        [Parameter(Mandatory = $true)] [string]$AssetName
    )

    $apiUrl = "https://api.github.com/repos/$Owner/$Repository/releases/tags/$Tag"
    $headers = @{
        Accept = 'application/vnd.github+json'
        'User-Agent' = 'VideoDownloadCompanion-Installer/0.1.15'
        'X-GitHub-Api-Version' = '2022-11-28'
    }

    Write-Host ''
    Write-Host "正在从 GitHub 官方 Release API 获取 $AssetName 的校验信息…" -ForegroundColor Cyan
    try {
        $release = Invoke-RestMethod -Method Get -Uri $apiUrl -Headers $headers
    }
    catch {
        throw "无法读取 GitHub Release 元数据：$apiUrl；$($_.Exception.Message)"
    }

    $matchingAssets = @($release.assets | Where-Object { $_.name -eq $AssetName })
    if ($matchingAssets.Count -ne 1) {
        throw "GitHub Release $Owner/$Repository@$Tag 中未找到唯一资产：$AssetName"
    }

    $asset = $matchingAssets[0]
    $digest = [string]$asset.digest
    if ($digest -notmatch '^sha256:([0-9a-fA-F]{64})$') {
        throw "GitHub 未提供 $AssetName 的有效 SHA-256 摘要，拒绝未经校验的下载。"
    }

    try {
        $downloadUri = [Uri][string]$asset.browser_download_url
    }
    catch {
        throw "GitHub 返回了无效的资产下载地址：$($asset.browser_download_url)"
    }
    $expectedPathPrefix = "/$Owner/$Repository/releases/download/"
    $actualAssetName = [Uri]::UnescapeDataString([System.IO.Path]::GetFileName($downloadUri.AbsolutePath))
    if ($downloadUri.Scheme -ne 'https' -or
        $downloadUri.Host -ne 'github.com' -or
        -not $downloadUri.AbsolutePath.StartsWith($expectedPathPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        $actualAssetName -ne $AssetName) {
        throw "GitHub 返回的资产地址不符合预期仓库或文件名：$($downloadUri.AbsoluteUri)"
    }

    $metadata = [pscustomobject]@{
        Url = $downloadUri.AbsoluteUri
        Sha256 = $Matches[1].ToUpperInvariant()
        Size = [long]$asset.size
        UpdatedAt = [string]$asset.updated_at
    }
    Write-Host "  资产：$AssetName" -ForegroundColor DarkGray
    Write-Host "  大小：$(Format-ByteCount $metadata.Size)" -ForegroundColor DarkGray
    Write-Host "  SHA-256：$($metadata.Sha256)" -ForegroundColor DarkGray
    return $metadata
}

if ($MetadataTestOnly) {
    Get-GitHubReleaseAssetMetadata `
        -Owner 'yt-dlp' `
        -Repository 'FFmpeg-Builds' `
        -Tag 'latest' `
        -AssetName 'ffmpeg-master-latest-win64-gpl.zip' | ConvertTo-Json
    return
}

function Reset-ChildDirectory {
    param([string]$Path)
    $resolvedParent = [System.IO.Path]::GetFullPath((Split-Path -Parent $Path))
    if (-not $resolvedParent.Equals([System.IO.Path]::GetFullPath($tools), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理 tools 之外的目录：$Path"
    }
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

New-Item -ItemType Directory -Path $tools, $scripts, $state, $media -Force | Out-Null
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$transcriptStarted = $false

try {
    Start-Transcript -LiteralPath $installLog -Force | Out-Null
    $transcriptStarted = $true
} catch {}

Write-Host 'Video Download Companion 依赖安装' -ForegroundColor White
Write-Host '将依次下载并校验 yt-dlp、Deno、FFmpeg 和 DouK。请保持此窗口打开。'
Write-Host "安装目录：$install" -ForegroundColor DarkGray
Write-Host "日志文件：$installLog" -ForegroundColor DarkGray

if ($ProgressTestOnly) {
    try {
        Get-VerifiedFile `
            -Name 'yt-dlp 进度测试' `
            -Step 1 `
            -Url 'https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.exe' `
            -Destination (Join-Path $stage 'yt-dlp-progress-test.exe') `
            -Sha256 '66674953FE251B89F4D08C5F0E35E0728679BD67AB3D7D05C0562AF101DD3E7A'
        Write-Host ''
        Write-Host '下载进度与文件校验测试通过。' -ForegroundColor Green
    }
    finally {
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        if ($transcriptStarted) {
            try { Stop-Transcript | Out-Null } catch {}
        }
    }
    return
}

try {
    $ytDlp = Join-Path $tools 'yt-dlp.exe'
    Get-VerifiedFile `
        -Name 'yt-dlp' `
        -Step 1 `
        -Url 'https://github.com/yt-dlp/yt-dlp/releases/download/2026.08.19/yt-dlp.exe' `
        -Destination $ytDlp `
        -Sha256 '66674953FE251B89F4D08C5F0E35E0728679BD67AB3D7D05C0562AF101DD3E7A'

    $denoZip = Join-Path $stage 'deno.zip'
    Get-VerifiedFile `
        -Name 'Deno' `
        -Step 2 `
        -Url 'https://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-pc-windows-msvc.zip' `
        -Destination $denoZip `
        -Sha256 'A0C3101B4158D1DFB7D6A78A7BF0F3DE80C96BB423C152BEEC8BEB22786F2238'
    Write-Host '  正在解压 Deno…' -ForegroundColor Yellow
    $denoStage = Join-Path $stage 'deno'
    Expand-Archive -LiteralPath $denoZip -DestinationPath $denoStage -Force
    $denoDir = Join-Path $tools 'deno'
    Reset-ChildDirectory $denoDir
    Copy-Item -LiteralPath (Join-Path $denoStage 'deno.exe') -Destination $denoDir

    $ffmpegAsset = Get-GitHubReleaseAssetMetadata `
        -Owner 'yt-dlp' `
        -Repository 'FFmpeg-Builds' `
        -Tag 'latest' `
        -AssetName 'ffmpeg-master-latest-win64-gpl.zip'
    $ffmpegZip = Join-Path $stage 'ffmpeg.zip'
    Get-VerifiedFile `
        -Name 'FFmpeg' `
        -Step 3 `
        -Url $ffmpegAsset.Url `
        -Destination $ffmpegZip `
        -Sha256 $ffmpegAsset.Sha256
    Write-Host '  正在解压 FFmpeg…' -ForegroundColor Yellow
    $ffmpegStage = Join-Path $stage 'ffmpeg'
    Expand-Archive -LiteralPath $ffmpegZip -DestinationPath $ffmpegStage -Force
    $ffmpegBin = Get-ChildItem -LiteralPath $ffmpegStage -Recurse -Filter 'ffmpeg.exe' -File | Select-Object -First 1
    $ffprobeBin = Get-ChildItem -LiteralPath $ffmpegStage -Recurse -Filter 'ffprobe.exe' -File | Select-Object -First 1
    if (-not $ffmpegBin -or -not $ffprobeBin) { throw 'FFmpeg 压缩包中缺少 ffmpeg.exe 或 ffprobe.exe。' }
    $ffmpegDir = Join-Path $tools 'ffmpeg'
    Reset-ChildDirectory $ffmpegDir
    Copy-Item -LiteralPath $ffmpegBin.FullName, $ffprobeBin.FullName -Destination $ffmpegDir

    $doukZip = Join-Path $stage 'douk.zip'
    Get-VerifiedFile `
        -Name 'DouK' `
        -Step 4 `
        -Url 'https://github.com/JoeanAmier/TikTokDownloader/releases/download/5.8/DouK-Downloader_V5.8_Windows_X64.zip' `
        -Destination $doukZip `
        -Sha256 'C96342EF1256B708B26AB742CAC2E6F29AED78E40C82D52098AF01737951910C'
    Write-Host '  正在解压 DouK…' -ForegroundColor Yellow
    $doukStage = Join-Path $stage 'douk'
    Expand-Archive -LiteralPath $doukZip -DestinationPath $doukStage -Force
    $doukMain = Get-ChildItem -LiteralPath $doukStage -Recurse -Filter 'DouK-Downloader.exe' -File | Select-Object -First 1
    if (-not $doukMain) { throw 'DouK 压缩包中缺少 DouK-Downloader.exe。' }
    $mainHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $doukMain.FullName).Hash
    if ($mainHash -ne 'DA81823ED80D1D38F49F1F7DBE441EC564AB3216E01378A940A91F44BC52D5C0') {
        throw "DouK main.exe 校验失败：$mainHash"
    }
    $doukDir = Join-Path $tools 'douk'
    $currentVolume = Join-Path $doukDir 'Volume'
    $legacyVolume = Join-Path $doukDir '_internal\Volume'
    $existingVolume = if (Test-Path -LiteralPath $currentVolume -PathType Container) { $currentVolume } else { $legacyVolume }
    $installedVolume = $currentVolume
    $volumeBackup = Join-Path $stage 'douk-volume-backup'
    if (Test-Path -LiteralPath $existingVolume -PathType Container) {
        Write-Host '  正在保留现有 DouK 设置、Cookie 和下载记录…' -ForegroundColor Yellow
        Move-Item -LiteralPath $existingVolume -Destination $volumeBackup
    }
    Reset-ChildDirectory $doukDir
    Copy-Item -Path (Join-Path $doukMain.Directory.FullName '*') -Destination $doukDir -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $doukDir 'DouK-Downloader.exe') -Destination (Join-Path $doukDir 'main.exe')
    if (Test-Path -LiteralPath $volumeBackup -PathType Container) {
        if (Test-Path -LiteralPath $installedVolume) {
            Remove-Item -LiteralPath $installedVolume -Recurse -Force
        }
        Move-Item -LiteralPath $volumeBackup -Destination $installedVolume
    } else {
        New-Item -ItemType Directory -Path $installedVolume -Force | Out-Null
    }
    New-Item -ItemType Directory -Path (Join-Path $installedVolume 'Download') -Force | Out-Null

    $configPath = Join-Path $state 'config.json'
    Write-Host ''
    Write-Host '正在生成 Companion 配置…' -ForegroundColor Cyan
    $existing = if (Test-Path -LiteralPath $configPath) {
        Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } else { $null }
    $token = if ($existing -and $existing.server.auth_token) { [string]$existing.server.auth_token } else {
        $bytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    }
    $legacyProxy = if ($existing -and $null -ne $existing.yt_dlp.proxy) { [string]$existing.yt_dlp.proxy } else { '' }
    $existingYouTube = if ($existing) { $existing.yt_dlp.platforms.youtube } else { $null }
    $existingBilibili = if ($existing) { $existing.yt_dlp.platforms.bilibili } else { $null }
    $youtubeProxy = if ($existingYouTube -and $existingYouTube.PSObject.Properties['proxy']) {
        [string]$existingYouTube.proxy
    } else { $legacyProxy }
    $bilibiliProxy = if ($existingBilibili -and $existingBilibili.PSObject.Properties['proxy']) {
        [string]$existingBilibili.proxy
    } else { '' }
    $youtubeDir = Join-Path $media 'YouTube-Bilibili'
    $douyinDir = Join-Path $media 'Douyin'
    New-Item -ItemType Directory -Path $youtubeDir, $douyinDir -Force | Out-Null

    $config = [ordered]@{
        server = [ordered]@{ host='127.0.0.1'; port=17392; auth_token=$token; allowed_origins=@('chrome-extension://*'); request_body_limit_bytes=16384 }
        downloads = [ordered]@{
            directory=$youtubeDir
            archive_file=(Join-Path $state 'download-archive.txt')
            jobs_file=(Join-Path $state 'jobs.json')
            database_file=(Join-Path $state 'jobs.db')
            log_file=(Join-Path $state 'listener.log')
        }
        scheduler = [ordered]@{ max_total_concurrent=3; max_ytdlp_concurrent=2; max_douk_concurrent=1; max_attempts=2; retry_delay_seconds=3 }
        yt_dlp = [ordered]@{
            executable=$ytDlp
            ffmpeg_location=$ffmpegDir
            proxy=''
            cookies_from_browser='chrome'
            platforms=[ordered]@{
                youtube=[ordered]@{ cookies_from_browser='chrome'; cookies_fallback_from_browser='firefox'; proxy=$youtubeProxy }
                bilibili=[ordered]@{ cookies_from_browser='chrome'; cookies_fallback_from_browser='firefox'; proxy=$bilibiliProxy }
            }
            js_runtime="deno:$(Join-Path $denoDir 'deno.exe')"
            remote_components=@()
            format='bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b[height<=1080]'
            merge_output_format='mp4'
            output_template='%(title).180B [%(id)s].%(ext)s'
            concurrent_fragments=4
            restrict_filenames=$false
            write_info_json=$true
            extra_args=@()
        }
        douk = [ordered]@{
            root=$doukDir
            wrapper=(Join-Path $scripts 'download_douyin.ps1')
            output_directory=$douyinDir
            powershell_executable='powershell.exe'
            refresh_cookie_from_browser=$false
            browser='Firefox'
            expected_sha256='DA81823ED80D1D38F49F1F7DBE441EC564AB3216E01378A940A91F44BC52D5C0'
        }
    }
    $config | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $configPath -Encoding UTF8

    function Get-DouKInitializationStatus {
        $statusFile = Join-Path $state 'douk-initialization-status.json'
        if ([System.IO.File]::Exists($statusFile)) { [System.IO.File]::Delete($statusFile) }
        $companionExe = Join-Path $install 'VideoDownloadCompanion.exe'
        $arguments = '--douk-init-status --douk-root "{0}" --result "{1}"' -f $doukDir, $statusFile
        $process = Start-Process -FilePath $companionExe -ArgumentList $arguments -WorkingDirectory $install -WindowStyle Hidden -Wait -PassThru
        if (-not (Test-Path -LiteralPath $statusFile -PathType Leaf)) {
            throw "无法读取 DouK 初始化状态；辅助进程退出代码：$($process.ExitCode)"
        }
        return Get-Content -LiteralPath $statusFile -Raw -Encoding UTF8 | ConvertFrom-Json
    }

    $initialization = Get-DouKInitializationStatus
    if (-not $initialization.initialized) {
        Write-Host ''
        Write-Host 'DouK 需要完成一次首次运行初始化。' -ForegroundColor Cyan
        Write-Host '即将打开 DouK 窗口；请选择语言、阅读并亲自接受免责声明。' -ForegroundColor Yellow
        Write-Host '进入主菜单后，可选择 2 → 9 从已登录的 Firefox 刷新抖音 Cookie，最后输入 Q 退出。'
        Write-Host '关闭 DouK 后，安装程序会自动检查初始化结果并继续。'
        $doukProcess = Start-Process -FilePath (Join-Path $doukDir 'main.exe') -WorkingDirectory $doukDir -WindowStyle Normal -Wait -PassThru
        $initialization = Get-DouKInitializationStatus
        if (-not $initialization.initialized) {
            throw 'DouK 初始化尚未完成：请在 DouK 中选择语言并输入 YES 接受免责声明后再退出。'
        }
        Write-Host 'DouK 首次初始化已完成。' -ForegroundColor Green
    } else {
        Write-Host 'DouK 已完成初始化；保留现有语言、Cookie 和下载记录。' -ForegroundColor Green
    }
}
catch {
    $installFailure = $_
    if ($volumeBackup -and $installedVolume -and (Test-Path -LiteralPath $volumeBackup -PathType Container)) {
        Write-Warning '安装未完成，正在把暂存的 DouK Volume 自动恢复到原位置。'
        try {
            $volumeParent = Split-Path -Parent $installedVolume
            New-Item -ItemType Directory -Path $volumeParent -Force | Out-Null
            if (Test-Path -LiteralPath $installedVolume) {
                Remove-Item -LiteralPath $installedVolume -Recurse -Force
            }
            Move-Item -LiteralPath $volumeBackup -Destination $installedVolume
        }
        catch {
            $rollbackFailure = $_.Exception.Message
            throw "依赖安装失败，DouK 数据自动回滚也失败。暂存数据保留在：$volumeBackup；回滚错误：$rollbackFailure；原始错误：$($installFailure.Exception.Message)"
        }
    }
    throw $installFailure
}
finally {
    if (Test-Path -LiteralPath $stage) {
        if ($volumeBackup -and (Test-Path -LiteralPath $volumeBackup -PathType Container)) {
            Write-Warning "DouK 暂存数据仍在：$volumeBackup；为防止数据丢失，本次不清理安装暂存目录。"
        } else {
            Remove-Item -LiteralPath $stage -Recurse -Force
        }
    }
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
    }
}

Write-Host ''
Write-Host "依赖和配置安装完成：$install" -ForegroundColor Green
