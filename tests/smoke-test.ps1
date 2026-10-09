[CmdletBinding()]
param(
    [string]$Config = (Join-Path $env:LOCALAPPDATA 'YouTubeYtDlpBridge\config.json'),
    [string]$VideoUrl = ''
)

$ErrorActionPreference = 'Stop'
$settings = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$baseUrl = "http://$($settings.server.host):$($settings.server.port)"
$token = $settings.server.auth_token
$allowedOrigin = 'chrome-extension://smoke-test'

$health = Invoke-RestMethod -Uri "$baseUrl/api/health"
if ($health.name -ne 'youtube-ytdlp-bridge') { throw 'Unexpected health response.' }

function Get-ErrorStatus {
    param([hashtable]$Headers, [string]$Body)
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/download" -Method Post -Headers $Headers -ContentType 'application/json' -Body $Body | Out-Null
        return 200
    } catch {
        return [int]$_.Exception.Response.StatusCode
    }
}

$validBody = @{ url = 'https://www.youtube.com/watch?v=3IDn1iMxblo' } | ConvertTo-Json
$badBody = @{ url = 'https://example.com/not-youtube' } | ConvertTo-Json
$unauthorized = Get-ErrorStatus -Headers @{ Origin = $allowedOrigin } -Body $validBody
$badOrigin = Get-ErrorStatus -Headers @{ Origin = 'https://evil.example'; 'X-YTDLP-Token' = $token } -Body $validBody
$badUrl = Get-ErrorStatus -Headers @{ Origin = $allowedOrigin; 'X-YTDLP-Token' = $token } -Body $badBody
$jobs = Invoke-RestMethod -Uri "$baseUrl/api/jobs?limit=5" -Headers @{ Origin = $allowedOrigin; 'X-YTDLP-Token' = $token }

if ($unauthorized -ne 401) { throw "Expected 401, received $unauthorized" }
if ($badOrigin -ne 403) { throw "Expected 403, received $badOrigin" }
if ($badUrl -ne 400) { throw "Expected 400, received $badUrl" }
if ($health.version -ne '0.7.0') { throw "Expected listener v0.7.0, received $($health.version)" }
if ($null -eq $jobs.summary.limits.total -or $null -eq $jobs.jobs) { throw 'Jobs API did not return scheduler state.' }

$result = [ordered]@{
    health = 'ok'
    unauthorized = $unauthorized
    badOrigin = $badOrigin
    badUrl = $badUrl
    jobsApi = 'ok'
    active = $jobs.summary.active
    totalLimit = $jobs.summary.limits.total
    databaseExists = Test-Path -LiteralPath $jobs.summary.database
}

if ($VideoUrl) {
    $payload = @{ url = $VideoUrl; title = 'Smoke test'; capturedAt = (Get-Date).ToUniversalTime().ToString('o') } | ConvertTo-Json
    $response = Invoke-RestMethod -Uri "$baseUrl/api/download" -Method Post -Headers @{ Origin = $allowedOrigin; 'X-YTDLP-Token' = $token } -ContentType 'application/json' -Body $payload
    $result.jobId = $response.job.id
    $result.jobStatus = $response.job.status
    $result.duplicate = $response.duplicate
}

[pscustomobject]$result | ConvertTo-Json
