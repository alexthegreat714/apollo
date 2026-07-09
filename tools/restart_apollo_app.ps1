param(
  [int]$Port = 5218,
  [int]$HealthTimeoutSeconds = 30,
  [switch]$StopExisting
)

$ErrorActionPreference = "Stop"

$ApolloRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $ApolloRoot "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Import-DotEnv {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return
  }

  Get-Content -LiteralPath $Path | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
      return
    }

    $idx = $line.IndexOf("=")
    $name = $line.Substring(0, $idx).Trim()
    $value = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
  }
}

Import-DotEnv -Path (Join-Path $ApolloRoot ".env")
$env:PYTHONPATH = [string]$ApolloRoot
$env:PORT = [string]$Port

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
  Select-Object -First 1

if ($existing) {
  if (-not $StopExisting) {
    throw "Port $Port is already listening under process $($existing.OwningProcess). Re-run with -StopExisting if this is the stale Apollo app."
  }

  Stop-Process -Id $existing.OwningProcess -Force
  Start-Sleep -Seconds 2
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $LogDir "apollo_app_$stamp.stdout.log"
$stderr = Join-Path $LogDir "apollo_app_$stamp.stderr.log"

$proc = Start-Process `
  -FilePath "py.exe" `
  -ArgumentList @("-3", "-Xutf8", "app.py") `
  -WorkingDirectory $ApolloRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -PassThru

$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$health = $null
$lastError = ""

while ((Get-Date) -lt $deadline) {
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
    if ($health.status -eq "ok") {
      break
    }
  } catch {
    $lastError = $_.Exception.Message
  }
  Start-Sleep -Seconds 1
}

if (-not $health -or $health.status -ne "ok") {
  $tail = ""
  if (Test-Path -LiteralPath $stderr) {
    $tail = (Get-Content -LiteralPath $stderr -Tail 40) -join "`n"
  }
  throw "Apollo did not become healthy on port $Port. Last error: $lastError`nStderr tail:`n$tail"
}

[pscustomobject]@{
  ok = $true
  pid = $proc.Id
  port = $Port
  health_status = $health.status
  app = $health.app
  stdout = $stdout
  stderr = $stderr
} | ConvertTo-Json -Depth 4
