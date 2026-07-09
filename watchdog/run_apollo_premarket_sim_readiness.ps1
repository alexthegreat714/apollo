param(
  [string]$RunId = "",
  [string]$BaseUrl = "http://127.0.0.1:5218",
  [int]$MaxStaleDays = 4
)

$ErrorActionPreference = "Stop"
$repoRoot = "C:\Users\blyth\Desktop\Engineering"
$logRoot = Join-Path $repoRoot "Apollo\logs\premarket_sim_readiness"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
if ([string]::IsNullOrWhiteSpace($RunId)) {
  $RunId = "apollo_premarket_sim_readiness_$stamp"
}

$stdout = Join-Path $logRoot "$RunId.stdout.log"
$stderr = Join-Path $logRoot "$RunId.stderr.log"

$moduleArgs = @(
  "-m", "Apollo.premarket_sim_readiness",
  "--run-id", $RunId,
  "--base-url", $BaseUrl,
  "--max-stale-days", [string]$MaxStaleDays
)

"[$(Get-Date -Format s)] Apollo premarket simulation readiness: $RunId" | Tee-Object -FilePath $stdout -Append | Out-Null
$allArgs = @("-3") + $moduleArgs
$proc = Start-Process -FilePath "py" -ArgumentList $allArgs -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -Wait
"[$(Get-Date -Format s)] exit=$($proc.ExitCode)" | Tee-Object -FilePath $stdout -Append | Out-Null

exit $proc.ExitCode
