param(
    [string]$PayloadJson = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logRoot = Join-Path $repoRoot "Apollo\logs\nightly"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$launcherLog = Join-Path $logRoot ("background_launcher_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

function Resolve-Python {
    $candidates = @(
        "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe",
        "C:\Users\blyth\AppData\Local\Programs\Python\Python311\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Path) { return $cmd.Path }
    throw "python_not_found"
}

$pythonExe = Resolve-Python
Push-Location $repoRoot
try {
    "[$(Get-Date -Format s)] starting Apollo background pipeline step via $pythonExe" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    $args = @("-m", "Apollo.nightly_pipeline", "background-step")
    if ($PayloadJson) {
        $args += @("--payload", $PayloadJson)
    }
    & $pythonExe @args 2>&1 | Tee-Object -FilePath $launcherLog -Append
    $exitCode = $LASTEXITCODE
    "[$(Get-Date -Format s)] finished exit=$exitCode" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    exit $exitCode
}
finally {
    Pop-Location
}
