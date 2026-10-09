[CmdletBinding()]
param(
    [string]$CompilerPath = '',
    [switch]$KeepStaging
)

$ErrorActionPreference = 'Stop'
$installerDir = [System.IO.Path]::GetFullPath($PSScriptRoot)
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $installerDir '..\..'))
$staging = Join-Path $installerDir 'staging'
$venv = Join-Path $projectRoot '.build-venv'
$python = Join-Path $venv 'Scripts\python.exe'
$dist = Join-Path $projectRoot 'companion\package-dist'
$issScript = Join-Path $installerDir 'VideoDownloadCompanion.iss'

if (-not $CompilerPath) {
    $compilerCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    $CompilerPath = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) {
    throw 'Inno Setup 6 compiler not found. Install JRSoftware.InnoSetup with winget or pass -CompilerPath.'
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    python -m venv $venv
}
$issSource = Get-Content -LiteralPath $issScript -Raw -Encoding UTF8
$appSource = Get-Content -LiteralPath (Join-Path $projectRoot 'companion/companion.py') -Raw -Encoding UTF8
$appVersionMatch = [regex]::Match($appSource, 'APP_VERSION\s*=\s*"([^"]+)"')
$installerVersionMatch = [regex]::Match($issSource, '#define MyAppVersion\s+"([^"]+)"')
if (-not $appVersionMatch.Success -or -not $installerVersionMatch.Success -or $appVersionMatch.Groups[1].Value -ne $installerVersionMatch.Groups[1].Value) {
    throw 'Source and installer versions differ.'
}
if ($issSource -match '\{userprofile\}') {
    throw 'Invalid Inno Setup constant found: {userprofile}. Use GetEnv(''USERPROFILE'') instead.'
}
& $python -m pip install --disable-pip-version-check -r (Join-Path $projectRoot 'companion\requirements-build.txt')
if ($LASTEXITCODE -ne 0) { throw 'Unable to install build requirements.' }

& $python -m PyInstaller `
    --noconfirm --clean --onefile --noconsole `
    --name VideoDownloadCompanion `
    --paths (Join-Path $projectRoot 'listener') `
    --exclude-module setuptools `
    --exclude-module pkg_resources `
    --exclude-module jaraco `
    --exclude-module backports `
    --distpath $dist `
    --workpath (Join-Path $projectRoot 'companion\build') `
    --specpath (Join-Path $projectRoot 'companion') `
    (Join-Path $projectRoot 'companion\companion.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $staging 'scripts') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $staging 'installer') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $dist 'VideoDownloadCompanion.exe') -Destination $staging
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
$scriptCopies = @(
    @{ Source=(Join-Path $projectRoot 'listener\download_douyin.ps1'); Destination=(Join-Path $staging 'scripts\download_douyin.ps1') },
    @{ Source=(Join-Path $projectRoot 'listener\refresh_douk_cookie.ps1'); Destination=(Join-Path $staging 'scripts\refresh_douk_cookie.ps1') },
    @{ Source=(Join-Path $installerDir 'install-dependencies.ps1'); Destination=(Join-Path $staging 'installer\install-dependencies.ps1') },
    @{ Source=(Join-Path $installerDir 'stop-existing.ps1'); Destination=(Join-Path $staging 'installer\stop-existing.ps1') }
)
foreach ($copy in $scriptCopies) {
    [System.IO.File]::WriteAllText($copy.Destination, (Get-Content -LiteralPath $copy.Source -Raw -Encoding UTF8), $utf8Bom)
}

& $CompilerPath $issScript
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup build failed.' }

$output = Join-Path $projectRoot ("dist\installer\Video-Download-Companion-Setup-{0}.exe" -f $appVersionMatch.Groups[1].Value)
if (-not (Test-Path -LiteralPath $output -PathType Leaf)) { throw "Installer not created: $output" }
$result = [ordered]@{
    installer = $output
    size = (Get-Item -LiteralPath $output).Length
    sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $output).Hash
}
if (-not $KeepStaging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
$result | ConvertTo-Json
