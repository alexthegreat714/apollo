param(
  [switch]$Notify
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$engineering = Split-Path -Parent $root
$pythonExe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $pythonExe)) {
  $pythonExe = "python"
}

$logDir = Join-Path $root "logs\learning\scheduler"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $logDir "apollo_learning_$stamp.stdout.log"
$stderr = Join-Path $logDir "apollo_learning_$stamp.stderr.log"
$lockPath = Join-Path $logDir "apollo_learning.lock"

if (Test-Path $lockPath) {
  $age = (Get-Date) - (Get-Item $lockPath).LastWriteTime
  if ($age.TotalMinutes -lt 45) {
    exit 0
  }
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

Set-Content -Path $lockPath -Value $PID -Encoding UTF8
try {
  Push-Location $engineering
  $proc = Start-Process -FilePath $pythonExe -ArgumentList @("-m", "Apollo.learning_loop", "run") -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  if ($proc.ExitCode -ne 0) {
    if ($Notify) {
      $payload = @{
        level = "warn"
        title = "Apollo learning loop failed"
        message = "Apollo post-close learning loop exited with code $($proc.ExitCode). stderr: $stderr"
        tags = @("warning", "apollo", "learning")
        source = "apollo"
        event = "learning_loop_failed"
      } | ConvertTo-Json -Depth 6
      try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:5216/alerts/external" -ContentType "application/json" -Body $payload | Out-Null
      } catch {
      }
    }
    exit $proc.ExitCode
  }
} finally {
  Pop-Location
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

exit 0
