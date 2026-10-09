[CmdletBinding()]
param(
    [string]$CompilerPath = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$appSource = Get-Content -LiteralPath (Join-Path $projectRoot 'companion\companion.py') -Raw -Encoding UTF8
$appVersionMatch = [regex]::Match($appSource, 'APP_VERSION\s*=\s*"([^"]+)"')
if (-not $appVersionMatch.Success) { throw 'Unable to read APP_VERSION.' }
$appVersion = $appVersionMatch.Groups[1].Value
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$extensionVersion = [string]$manifest.version

$buildScript = Join-Path $projectRoot 'companion\installer\build-installer.ps1'
if ($CompilerPath) {
    & $buildScript -CompilerPath $CompilerPath
} else {
    & $buildScript
}
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }

$releaseRoot = Join-Path $projectRoot "dist\release\v$appVersion"
$expectedRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'dist\release'))
$resolvedRelease = [System.IO.Path]::GetFullPath($releaseRoot)
if (-not $resolvedRelease.StartsWith($expectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Release path escaped the expected directory: $resolvedRelease"
}
if (Test-Path -LiteralPath $resolvedRelease) {
    Remove-Item -LiteralPath $resolvedRelease -Recurse -Force
}
New-Item -ItemType Directory -Path $resolvedRelease | Out-Null

$installerName = "Video-Download-Companion-Setup-$appVersion.exe"
$installerSource = Join-Path $projectRoot "dist\installer\$installerName"
if (-not (Test-Path -LiteralPath $installerSource -PathType Leaf)) {
    throw "Installer was not created: $installerSource"
}
Copy-Item -LiteralPath $installerSource -Destination (Join-Path $resolvedRelease $installerName)

$extensionName = "video-download-companion-extension-$extensionVersion.zip"
$extensionZip = Join-Path $resolvedRelease $extensionName
Compress-Archive -Path (Join-Path $projectRoot 'extension\*') -DestinationPath $extensionZip -CompressionLevel Optimal
Copy-Item -LiteralPath (Join-Path $projectRoot 'companion\installer\THIRD_PARTY_NOTICES.md') -Destination $resolvedRelease
Copy-Item -LiteralPath (Join-Path $projectRoot 'scripts\verify-installed.ps1') -Destination $resolvedRelease
Copy-Item -LiteralPath (Join-Path $projectRoot "docs\releases\v$appVersion.md") -Destination (Join-Path $resolvedRelease 'RELEASE_NOTES.md')

$assets = Get-ChildItem -LiteralPath $resolvedRelease -File | Sort-Object Name
$sumLines = foreach ($asset in $assets) {
    '{0}  {1}' -f (Get-FileHash -LiteralPath $asset.FullName -Algorithm SHA256).Hash, $asset.Name
}
$sumPath = Join-Path $resolvedRelease 'SHA256SUMS.txt'
[System.IO.File]::WriteAllLines($sumPath, $sumLines, (New-Object System.Text.UTF8Encoding($false)))

$result = [ordered]@{
    version = $appVersion
    extension_version = $extensionVersion
    release_directory = $resolvedRelease
    assets = @(
        Get-ChildItem -LiteralPath $resolvedRelease -File | Sort-Object Name | ForEach-Object {
            [ordered]@{
                name = $_.Name
                bytes = $_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
            }
        }
    )
}
$result | ConvertTo-Json -Depth 4
