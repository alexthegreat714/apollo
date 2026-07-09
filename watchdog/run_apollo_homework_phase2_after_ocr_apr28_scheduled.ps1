$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "run_apollo_homework_phase2_after_ocr_apr28.ps1"

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner `
  -OcrTaskName "OCR97MixedCorpusBenchmarkApr28_0100" `
  -Tickers "NVDA,AMD,AVGO,MSFT,AMZN" `
  -MaxWaitMinutes 480 `
  -PollSeconds 60 `
  -GraphMaxNewChunks 1000 `
  -GraphTimeoutSeconds 1800 `
  -Notify

exit $LASTEXITCODE
