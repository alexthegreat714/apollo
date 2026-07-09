param(
  [ValidateSet("open", "finalize", "status")]
  [string]$Action = "open",
  [string]$Note = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logDir = Join-Path $repoRoot "Apollo\logs\daily_reports\scheduler"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$pythonExe = "python"
$candidate = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
if (Test-Path $candidate) {
  $pythonExe = $candidate
}

Set-Location $repoRoot
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stdout = Join-Path $logDir "apollo_daily_report_${Action}_$stamp.stdout.log"
$stderr = Join-Path $logDir "apollo_daily_report_${Action}_$stamp.stderr.log"

$argsList = @("-m", "Apollo.daily_report", $Action)
if (-not [string]::IsNullOrWhiteSpace($Note)) {
  $argsList += @("--note", $Note)
}

$proc = Start-Process -FilePath $pythonExe -ArgumentList $argsList -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
exit $proc.ExitCode
