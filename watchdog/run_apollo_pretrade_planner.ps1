param(
  [string]$Ticker = "",
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logDir = Join-Path $repoRoot "Apollo\logs\pretrade_gate\scheduler"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$lockPath = Join-Path $logDir "apollo_pretrade_gate.lock"

if (Test-Path $lockPath) {
  $lockAge = (Get-Date) - (Get-Item $lockPath).LastWriteTime
  if ($lockAge.TotalMinutes -lt 20) {
    $skipLog = Join-Path $logDir ("apollo_pretrade_gate_skipped_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
    "Skipped: active lock exists at $lockPath age_minutes=$([math]::Round($lockAge.TotalMinutes, 1))" | Out-File -FilePath $skipLog -Encoding utf8
    exit 0
  }
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

"pid=$PID started=$(Get-Date -Format o)" | Out-File -FilePath $lockPath -Encoding utf8

try {
  [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal
} catch {
}

$pythonExe = "python"
$candidate = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
if (Test-Path $candidate) {
  $pythonExe = $candidate
}

Set-Location $repoRoot
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $logDir "apollo_pretrade_gate_$stamp.stdout.log"
$stderr = Join-Path $logDir "apollo_pretrade_gate_$stamp.stderr.log"

$argsList = @("-m", "Apollo.pretrade_gate", "check", "--trigger", "planner")
if (-not [string]::IsNullOrWhiteSpace($Ticker)) {
  $argsList += @("--ticker", $Ticker)
}
if ($Force) {
  $argsList += "--force"
}

try {
  $proc = Start-Process -FilePath $pythonExe -ArgumentList $argsList -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  exit $proc.ExitCode
} finally {
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}
