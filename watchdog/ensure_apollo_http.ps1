param(
  [string]$BaseUrl = "http://127.0.0.1:5218",
  [int]$Port = 5218,
  [int]$WaitSeconds = 75
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$apolloRoot = Join-Path $repoRoot "Apollo"
$statusDir = Join-Path $apolloRoot "logs\http_keepalive"
$statusPath = Join-Path $statusDir "latest_status.json"
New-Item -ItemType Directory -Force -Path $statusDir | Out-Null

function Write-Status {
  param([hashtable]$Payload)
  $Payload.checked_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  $Payload.base_url = $BaseUrl
  $Payload.port = $Port
  $Payload | ConvertTo-Json -Depth 8 | Set-Content -Path $statusPath -Encoding UTF8
}

function Test-ApolloHealth {
  try {
    $response = Invoke-RestMethod -Uri "$($BaseUrl.TrimEnd('/'))/health" -Method Get -TimeoutSec 8
    return @{
      ok = (($response.status -eq "ok") -or ($response.ok -eq $true))
      body = $response
      error = ""
    }
  } catch {
    return @{
      ok = $false
      body = $null
      error = $_.Exception.Message
    }
  }
}

$env:APOLLO_SIM_MAIN_LANE = "aggressive_news_cycle"
$env:APOLLO_SIM_BROKER_PROVIDER = "paper_sim"
$env:APOLLO_SIM_EXECUTION_MODE = "paper"
$env:APOLLO_SIM_LIVE_ENABLED = "0"

$initial = Test-ApolloHealth
if ($initial.ok) {
  Write-Status @{
    ok = $true
    action = "already_running"
    initial = $initial
  }
  exit 0
}

$restart = Join-Path $repoRoot "tools\restart_agent_headless.ps1"
if (-not (Test-Path $restart)) {
  Write-Status @{
    ok = $false
    action = "restart_script_missing"
    initial = $initial
    restart_script = $restart
  }
  exit 2
}

$stdoutPath = Join-Path $statusDir ("restart_{0}.stdout.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$stderrPath = Join-Path $statusDir ("restart_{0}.stderr.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
$proc = Start-Process -FilePath "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" `
  -ArgumentList @(
    "-WindowStyle", "Hidden",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $restart,
    "-AgentName", "Apollo",
    "-Port", "$Port",
    "-EntryMode", "script",
    "-EntryTarget", "app.py",
    "-Root", "$repoRoot",
    "-AgentDir", "$apolloRoot",
    "-LogSubdir", "logs"
  ) `
  -WindowStyle Hidden `
  -Wait `
  -PassThru `
  -RedirectStandardOutput $stdoutPath `
  -RedirectStandardError $stderrPath

$deadline = (Get-Date).AddSeconds([Math]::Max(1, $WaitSeconds))
$final = $null
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Seconds 2
  $final = Test-ApolloHealth
  if ($final.ok) {
    Write-Status @{
      ok = $true
      action = "restarted"
      initial = $initial
      final = $final
      restart_exit_code = $proc.ExitCode
      stdout_path = $stdoutPath
      stderr_path = $stderrPath
    }
    exit 0
  }
}

Write-Status @{
  ok = $false
  action = "restart_failed_or_unhealthy"
  initial = $initial
  final = $final
  restart_exit_code = $proc.ExitCode
  stdout_path = $stdoutPath
  stderr_path = $stderrPath
}
exit 1
