param(
    [string]$PayloadJson = ""
)

$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$logRoot = Join-Path $repoRoot "Apollo\logs\nightly"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$launcherLog = Join-Path $logRoot ("launcher_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

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
$exitCode = 1
Push-Location $repoRoot
try {
    "[$(Get-Date -Format s)] starting Apollo nightly pipeline via $pythonExe" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    $args = @("-m", "Apollo.nightly_pipeline", "run")
    if ($PayloadJson) {
        $args += @("--payload", $PayloadJson)
    }
    $prevErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $pythonExe @args *>> $launcherLog
    if ($LASTEXITCODE -ne $null) {
        $exitCode = [int]$LASTEXITCODE
    } else {
        $exitCode = 0
    }
    $ErrorActionPreference = $prevErrorAction
}
catch {
    "[$(Get-Date -Format s)] launcher error: $($_)" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    $exitCode = 1
}
finally {
    "[$(Get-Date -Format s)] finished exit=$exitCode" | Tee-Object -FilePath $launcherLog -Append | Out-Null
    Pop-Location
}
exit $exitCode
