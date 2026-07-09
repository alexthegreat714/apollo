param(
    [string]$RunId = "",
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Continue"

$EngineeringRoot = "C:\Users\blynth\Desktop\Engineering"
if (-not (Test-Path -LiteralPath $EngineeringRoot)) {
    $EngineeringRoot = "C:\Users\blyth\Desktop\Engineering"
}
$ApolloRoot = Join-Path $EngineeringRoot "Apollo"
$OpsRoot = Join-Path $EngineeringRoot "_ops"
if (-not $RunId) {
    $RunId = "aegis_ocr97_pytest_" + (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmssfff")
}
$RunId = ($RunId -replace "[^A-Za-z0-9_.-]", "_").Trim("._-")
$RunRoot = Join-Path (Join-Path $OpsRoot "runs") $RunId
$ContractRoot = Join-Path $OpsRoot "contracts"
$StatusPath = Join-Path $RunRoot "status.json"
$EvidencePath = Join-Path $RunRoot "evidence.md"
$ContractPath = Join-Path $ContractRoot "$RunId.json"

New-Item -ItemType Directory -Force -Path $RunRoot | Out-Null
New-Item -ItemType Directory -Force -Path $ContractRoot | Out-Null

function Write-Status {
    param(
        [string]$State,
        [int]$ExitCode = -1,
        [string]$Message = "",
        [string]$ReportMd = "",
        [string]$ReportJson = "",
        [string]$LogPath = ""
    )
    $payload = [ordered]@{
        ok = ($State -eq "complete" -and $ExitCode -eq 0)
        contract_id = $RunId
        title = "Aegis OCR97 full guarded pytest"
        owner = "Aegis"
        state = $State
        exit_code = $ExitCode
        updated_at = (Get-Date).ToUniversalTime().ToString("o").Replace("+00:00", "Z")
        message = $Message
        artifacts = [ordered]@{
            status_path = $StatusPath
            evidence_path = $EvidencePath
            contract_path = $ContractPath
            report_md = $ReportMd
            report_json = $ReportJson
            log_path = $LogPath
        }
    }
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

$testFiles = Get-ChildItem -LiteralPath (Join-Path $ApolloRoot "tests") -File -Filter "test_ocr*.py" |
    Sort-Object Name |
    ForEach-Object { "tests/$($_.Name)" }

$contract = [ordered]@{
    contract_id = $RunId
    title = "Aegis OCR97 full guarded pytest"
    objective = "Run the full OCR97 pytest set through Apollo/tools/run_ocr_pytest_guarded.py so stale 41 percent hangs are bounded and a report is written even on failure."
    owner = "Aegis"
    created_by = "Codex handoff"
    systems = @("Aegis", "Apollo", "OCR97")
    execution = @(
        [ordered]@{
            step_id = "run_ocr97_guarded_pytest"
            kind = "command"
            command = "python tools/run_ocr_pytest_guarded.py --timeout-seconds $TimeoutSeconds -- " + ($testFiles -join " ") + " -q"
            cwd = $ApolloRoot
            timeout_seconds = $TimeoutSeconds
        }
    )
    artifacts = [ordered]@{
        status_path = $StatusPath
        evidence_path = $EvidencePath
        report_dir = (Join-Path $ApolloRoot "reports\ocr97_pytest")
        log_dir = (Join-Path $ApolloRoot "logs\pytest")
    }
}
$contract | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ContractPath -Encoding UTF8

$started = (Get-Date).ToUniversalTime().ToString("o").Replace("+00:00", "Z")
@(
    "# Aegis OCR97 Full Guarded Pytest",
    "",
    "- Run id: ``$RunId``",
    "- Started: ``$started``",
    "- Apollo root: ``$ApolloRoot``",
    "- Guard timeout seconds: ``$TimeoutSeconds``",
    "- Contract: ``$ContractPath``",
    "- Status: ``$StatusPath``",
    "",
    "## Test Files",
    "",
    ($testFiles | ForEach-Object { "- ``$_``" })
) | Set-Content -LiteralPath $EvidencePath -Encoding UTF8

Write-Status -State "running" -Message "Aegis local OCR97 guarded pytest started."

Set-Location -LiteralPath $ApolloRoot
$arguments = @("tools/run_ocr_pytest_guarded.py", "--timeout-seconds", "$TimeoutSeconds", "--") + $testFiles + @("-q")
& python @arguments
$exitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 1 }

$latestReport = Get-ChildItem -LiteralPath (Join-Path $ApolloRoot "reports\ocr97_pytest") -File -Filter "ocr97_pytest_*.md" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$latestJson = Get-ChildItem -LiteralPath (Join-Path $ApolloRoot "reports\ocr97_pytest") -File -Filter "ocr97_pytest_*.json" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$latestLog = Get-ChildItem -LiteralPath (Join-Path $ApolloRoot "logs\pytest") -File -Filter "apollo_ocr_pytest_*.log" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

$finished = (Get-Date).ToUniversalTime().ToString("o").Replace("+00:00", "Z")
Add-Content -LiteralPath $EvidencePath -Encoding UTF8 -Value @(
    "",
    "## Completion",
    "",
    "- Finished: ``$finished``",
    "- Exit code: ``$exitCode``",
    "- Guard report: ``$($latestReport.FullName)``",
    "- Guard JSON: ``$($latestJson.FullName)``",
    "- Guard log: ``$($latestLog.FullName)``"
)

if ($exitCode -eq 0) {
    Write-Status -State "complete" -ExitCode $exitCode -Message "Aegis local OCR97 guarded pytest passed." -ReportMd $latestReport.FullName -ReportJson $latestJson.FullName -LogPath $latestLog.FullName
} else {
    Write-Status -State "failed" -ExitCode $exitCode -Message "Aegis local OCR97 guarded pytest failed or timed out. See guard report." -ReportMd $latestReport.FullName -ReportJson $latestJson.FullName -LogPath $latestLog.FullName
}

exit $exitCode
