param()

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "run_apollo_improved_hipporag_overnight.ps1"
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
$lockPath = Join-Path $repoRoot "Apollo\logs\hipporag_improve\apollo_improved_hipporag.lock"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $lockPath) | Out-Null

if (Test-Path $lockPath) {
  $lock = Get-Item $lockPath
  if ($lock.LastWriteTime -gt (Get-Date).AddHours(-6)) {
    Write-Output "Apollo HippoRAG refresh already running or recently started; skipping this trigger."
    exit 0
  }
  Remove-Item -LiteralPath $lockPath -Force
}

try {
  @"
pid=$PID
started=$(Get-Date -Format o)
"@ | Set-Content -Path $lockPath -Encoding UTF8

  $env:APOLLO_FAST_LLM_BASE_URL = if ($env:APOLLO_FAST_LLM_BASE_URL) { $env:APOLLO_FAST_LLM_BASE_URL } else { "http://127.0.0.1:11434" }
  $env:APOLLO_FAST_LLM_MODEL = if ($env:APOLLO_FAST_LLM_MODEL) { $env:APOLLO_FAST_LLM_MODEL } else { "gemma3:12b" }
  $env:APOLLO_MODEL_STORAGE_ROOT = if ($env:APOLLO_MODEL_STORAGE_ROOT) { $env:APOLLO_MODEL_STORAGE_ROOT } else { "D:\ApolloModels" }
  $env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS = if ($env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS) { $env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS } else { "0" }
  $env:OLLAMA_MODELS = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "ollama" }
  $env:HF_HOME = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "huggingface" }
  $env:TRANSFORMERS_CACHE = if ($env:TRANSFORMERS_CACHE) { $env:TRANSFORMERS_CACHE } else { Join-Path $env:HF_HOME "transformers" }
  $env:TORCH_HOME = if ($env:TORCH_HOME) { $env:TORCH_HOME } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "torch" }
  $hipporagModel = if ($env:APOLLO_HIPPORAG_LLM_MODEL) { $env:APOLLO_HIPPORAG_LLM_MODEL } else { $env:APOLLO_FAST_LLM_MODEL }

  & $runner `
    -MaxNewChunks 120 `
    -BatchSize 20 `
    -LlmModel $hipporagModel

  exit $LASTEXITCODE
} finally {
  if (Test-Path $lockPath) {
    Remove-Item -LiteralPath $lockPath -Force
  }
}
