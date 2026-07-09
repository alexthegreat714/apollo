$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "run_apollo_homework_trade_after_ocr_apr28.ps1"

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner `
  -OcrTaskName "OCR97MixedCorpusBenchmarkApr28_0100" `
  -Tickers "NVDA,AMD,AVGO,MSFT,AMZN" `
  -MaxWaitMinutes 360 `
  -PollSeconds 60 `
  -Notify

exit $LASTEXITCODE
