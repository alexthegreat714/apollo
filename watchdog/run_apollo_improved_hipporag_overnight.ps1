param(
  [string]$RunId = "",
  [string]$RunDate = "",
  [int]$MaxNewChunks = 240,
  [int]$BatchSize = 20,
  [string]$LlmModel = "",
  [string]$OllamaUrl = "",
  [int]$LlmTimeoutSeconds = 45,
  [int]$LlmNumPredict = 300,
  [string]$CalendarEventId = ""
)

$ErrorActionPreference = "Stop"

$env:APOLLO_FAST_LLM_BASE_URL = if ($env:APOLLO_FAST_LLM_BASE_URL) { $env:APOLLO_FAST_LLM_BASE_URL } else { "http://127.0.0.1:11434" }
$env:APOLLO_FAST_LLM_MODEL = if ($env:APOLLO_FAST_LLM_MODEL) { $env:APOLLO_FAST_LLM_MODEL } else { "gemma3:12b" }
$env:APOLLO_MODEL_STORAGE_ROOT = if ($env:APOLLO_MODEL_STORAGE_ROOT) { $env:APOLLO_MODEL_STORAGE_ROOT } else { "D:\ApolloModels" }
$env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS = if ($env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS) { $env:APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS } else { "0" }
$env:OLLAMA_MODELS = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "ollama" }
$env:HF_HOME = if ($env:HF_HOME) { $env:HF_HOME } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "huggingface" }
$env:TRANSFORMERS_CACHE = if ($env:TRANSFORMERS_CACHE) { $env:TRANSFORMERS_CACHE } else { Join-Path $env:HF_HOME "transformers" }
$env:TORCH_HOME = if ($env:TORCH_HOME) { $env:TORCH_HOME } else { Join-Path $env:APOLLO_MODEL_STORAGE_ROOT "torch" }
if ([string]::IsNullOrWhiteSpace($LlmModel)) {
  $LlmModel = if ($env:APOLLO_HIPPORAG_LLM_MODEL) { $env:APOLLO_HIPPORAG_LLM_MODEL } else { $env:APOLLO_FAST_LLM_MODEL }
}
if ([string]::IsNullOrWhiteSpace($OllamaUrl)) {
  $OllamaUrl = $env:APOLLO_FAST_LLM_BASE_URL
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
if ([string]::IsNullOrWhiteSpace($RunDate)) {
  $RunDate = (Get-Date).ToString("yyyy-MM-dd")
}
if ([string]::IsNullOrWhiteSpace($RunId)) {
  $RunId = "apollo_improved_hipporag_$((Get-Date).ToString('yyyyMMdd_HHmm'))"
}

$logDir = Join-Path $repoRoot "Apollo\logs\hipporag_improve\$RunDate\$RunId"
$statusPath = Join-Path $logDir "status.json"
$ragDir = Join-Path $repoRoot "Apollo\chroma_db"
$kgPath = Join-Path $ragDir "financial_kg.json"
$quarantinePath = Join-Path $ragDir "financial_quarantine.json"
$backupKgPath = Join-Path $logDir "financial_kg.before.json"
$backupQuarantinePath = Join-Path $logDir "financial_quarantine.before.json"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-RunStatus {
  param([hashtable]$Payload)
  if (-not $Payload.ContainsKey("state")) {
    $Payload["state"] = "running"
  }
  if (-not $Payload.ContainsKey("message")) {
    $Payload["message"] = [string]$Payload["state"]
  }
  $Payload["updated_at"] = (Get-Date).ToUniversalTime().ToString("o")
  $Payload["run_id"] = $RunId
  $Payload["run_date"] = $RunDate
  $Payload["status_path"] = $statusPath
  $Payload["rag_dir"] = $ragDir
  $Payload["kg_path"] = $kgPath
  $Payload | ConvertTo-Json -Depth 12 | Set-Content -Path $statusPath -Encoding UTF8
}

function Update-SkyCalendarEvent {
  param(
    [string]$Status,
    [string]$Trigger
  )
  if ([string]::IsNullOrWhiteSpace($CalendarEventId)) { return }
  try {
    $payload = @{
      status = $Status
      trigger = $Trigger
    } | ConvertTo-Json -Depth 5
    Invoke-RestMethod -Method Patch -Uri "http://127.0.0.1:5011/calendar/events/$CalendarEventId" -ContentType "application/json" -Body $payload -TimeoutSec 10 | Out-Null
  } catch {
  }
}

function Invoke-Python {
  param(
    [string]$StepName,
    [string[]]$Arguments
  )
  $pythonExe = "python"
  $candidate = "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe"
  if (Test-Path $candidate) { $pythonExe = $candidate }
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $stdout = Join-Path $logDir "$StepName`_$stamp.stdout.log"
  $stderr = Join-Path $logDir "$StepName`_$stamp.stderr.log"
  $exitCode = 1
  Push-Location $repoRoot
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $pythonExe @Arguments > $stdout 2> $stderr
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
  } catch {
    $exitCode = 1
    try { Add-Content -Path $stderr -Value $_.Exception.Message -Encoding UTF8 } catch {}
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
  }
  return [ordered]@{
    step = $StepName
    exit_code = $exitCode
    ok = ($exitCode -eq 0)
    stdout = $stdout
    stderr = $stderr
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
  }
}

try {
  [System.Diagnostics.Process]::GetCurrentProcess().PriorityClass = [System.Diagnostics.ProcessPriorityClass]::BelowNormal
} catch {
}

$env:OMP_NUM_THREADS = "4"
$env:OPENBLAS_NUM_THREADS = "4"
$env:MKL_NUM_THREADS = "4"
$env:NUMEXPR_NUM_THREADS = "4"
$env:OLLAMA_URL = $OllamaUrl
$env:APOLLO_HIPPORAG_LLM_MODEL = $LlmModel
$env:APOLLO_HIPPORAG_LLM_TIMEOUT_SECS = [string]$LlmTimeoutSeconds
$env:APOLLO_HIPPORAG_LLM_NUM_PREDICT = [string]$LlmNumPredict

Write-RunStatus @{
  state = "running"
  message = "Preparing Apollo HippoRAG LLM refresh."
  current_stage = "prepare"
  model = $LlmModel
  max_new_chunks = $MaxNewChunks
  batch_size = $BatchSize
  live_trade_execution = $false
  cpu_expected_percent = 35
  gpu_3090_expected_percent = 65
  gb10_expected_percent = 0
}
Update-SkyCalendarEvent -Status "in_work" -Trigger "Apollo improved HippoRAG refresh is running through scheduled task ApolloImprovedHippoRAG20260428_2230."

if (Test-Path $kgPath) {
  Copy-Item -LiteralPath $kgPath -Destination $backupKgPath -Force
}
if (Test-Path $quarantinePath) {
  Copy-Item -LiteralPath $quarantinePath -Destination $backupQuarantinePath -Force
}

$prepareScript = Join-Path $logDir "prepare_reindex_batch.py"
@'
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
rag_dir = Path(sys.argv[2]).resolve()
collection_name = sys.argv[3]
max_new_chunks = max(1, int(sys.argv[4]))
out_path = Path(sys.argv[5]).resolve()

sys.path.insert(0, str(repo_root))

import chromadb
from chromadb.config import Settings

from Apollo.apollo_hipporag.graph_store import FinancialKG

finance_tokens = (
    "trading", "trade", "margin", "pattern day trader", "risk", "position",
    "slippage", "volatility", "liquidity", "stock", "equity", "portfolio",
    "capital", "leverage", "option", "etf", "sec", "finra", "market",
    "broker", "dealer", "regulation", "account", "transaction", "federal reserve",
)

client = chromadb.PersistentClient(
    path=str(rag_dir),
    settings=Settings(allow_reset=False, anonymized_telemetry=False),
)
collection = client.get_collection(collection_name)
ids = list(collection.get(include=[]).get("ids") or [])
kg = FinancialKG(str(rag_dir / "financial_kg.json"))
before = kg.stats()
indexed = [doc_id for doc_id in ids if kg.is_indexed(doc_id)]

selected = []
if indexed:
    fetched = collection.get(ids=indexed, include=["documents", "metadatas"])
    docs = fetched.get("documents") or []
    metas = fetched.get("metadatas") or []
    rows = []
    for idx, doc_id in enumerate(indexed):
        text = str(docs[idx] if idx < len(docs) else "")
        meta = metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}
        hay = " ".join([
            str(meta.get("kind") or ""),
            str(meta.get("title") or ""),
            str(meta.get("source") or ""),
            text[:1200],
        ]).lower()
        score = sum(1 for token in finance_tokens if token in hay)
        score += min(4, len(text) // 500)
        rows.append((-score, doc_id))
    rows.sort()
    selected = [doc_id for _, doc_id in rows[:max_new_chunks]]

changed = kg.remove_doc_ids(set(selected))
if changed:
    kg.save()
after = kg.stats()
payload = {
    "ok": True,
    "collection": collection_name,
    "total_chunks": collection.count(),
    "indexed_before": before.get("indexed_docs", 0),
    "nodes_before": before.get("nodes", 0),
    "edges_before": before.get("edges", 0),
    "selected_for_llm_refresh": len(selected),
    "selected_doc_ids": selected,
    "graph_changed": bool(changed),
    "indexed_after_prepare": after.get("indexed_docs", 0),
    "nodes_after_prepare": after.get("nodes", 0),
    "edges_after_prepare": after.get("edges", 0),
}
out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(json.dumps(payload, indent=2))
'@ | Set-Content -Path $prepareScript -Encoding UTF8

$prepare = Invoke-Python -StepName "prepare_reindex_batch" -Arguments @($prepareScript, $repoRoot, $ragDir, "apollo_financial", [string]$MaxNewChunks, (Join-Path $logDir "prepare_reindex_batch.json"))
if (-not $prepare.ok) {
  Write-RunStatus @{
    state = "failed"
    message = "Failed before graph refresh while preparing the Apollo re-index batch."
    current_stage = "prepare"
    prepare = $prepare
    backup_kg_path = $backupKgPath
    live_trade_execution = $false
  }
  Update-SkyCalendarEvent -Status "failed" -Trigger "Apollo improved HippoRAG refresh failed during prepare. Check the status artifact."
  exit 1
}

Write-RunStatus @{
  state = "running"
  message = "Running Apollo HippoRAG LLM refresh through Ollama."
  current_stage = "build_graph"
  model = $LlmModel
  max_new_chunks = $MaxNewChunks
  prepare = $prepare
  backup_kg_path = $backupKgPath
  live_trade_execution = $false
  cpu_expected_percent = 35
  gpu_3090_expected_percent = 65
  gb10_expected_percent = 0
}

$buildArgs = @(
  "-m", "Apollo.apollo_hipporag.build_graph",
  "--use-llm",
  "--rag-dir", $ragDir,
  "--collection", "apollo_financial",
  "--max-new-chunks", [string]$MaxNewChunks,
  "--batch-size", [string]$BatchSize,
  "--llm-model", $LlmModel,
  "--ollama-url", $OllamaUrl,
  "--llm-timeout", [string]$LlmTimeoutSeconds,
  "--llm-num-predict", [string]$LlmNumPredict
)
$build = Invoke-Python -StepName "build_graph_llm_refresh" -Arguments $buildArgs

$summaryScript = Join-Path $logDir "summarize_graph.py"
@'
from __future__ import annotations

import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
rag_dir = Path(sys.argv[2]).resolve()
prepare_path = Path(sys.argv[3]).resolve()
out_path = Path(sys.argv[4]).resolve()
sys.path.insert(0, str(repo_root))

from Apollo.apollo_hipporag.graph_store import FinancialKG

prepare = {}
try:
    prepare = json.loads(prepare_path.read_text(encoding="utf-8"))
except Exception:
    prepare = {}
kg = FinancialKG(str(rag_dir / "financial_kg.json"))
stats = kg.stats()
payload = {
    "ok": True,
    "prepare": prepare,
    "stats": stats,
    "selected_for_llm_refresh": int(prepare.get("selected_for_llm_refresh") or 0),
    "indexed_before": int(prepare.get("indexed_before") or 0),
    "indexed_after": int(stats.get("indexed_docs") or 0),
    "nodes_before": int(prepare.get("nodes_before") or 0),
    "nodes_after": int(stats.get("nodes") or 0),
    "edges_before": int(prepare.get("edges_before") or 0),
    "edges_after": int(stats.get("edges") or 0),
}
out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
print(json.dumps(payload, indent=2))
'@ | Set-Content -Path $summaryScript -Encoding UTF8

$summary = Invoke-Python -StepName "summarize_graph" -Arguments @($summaryScript, $repoRoot, $ragDir, (Join-Path $logDir "prepare_reindex_batch.json"), (Join-Path $logDir "graph_summary.json"))

if (-not $build.ok) {
  if ((Test-Path $backupKgPath) -and (Test-Path $kgPath)) {
    Copy-Item -LiteralPath $backupKgPath -Destination $kgPath -Force
  }
  if ((Test-Path $backupQuarantinePath) -and (Test-Path $quarantinePath)) {
    Copy-Item -LiteralPath $backupQuarantinePath -Destination $quarantinePath -Force
  }
  Write-RunStatus @{
    state = "failed"
    message = "Apollo HippoRAG LLM refresh failed; restored backed-up graph."
    current_stage = "failed_restored"
    build = $build
    summary = $summary
    backup_kg_path = $backupKgPath
    live_trade_execution = $false
  }
  Update-SkyCalendarEvent -Status "failed" -Trigger "Apollo improved HippoRAG refresh failed and restored the backed-up graph. Check the status artifact."
  exit 1
}

Write-RunStatus @{
  state = "complete"
  message = "Apollo HippoRAG LLM refresh completed."
  current_stage = "complete"
  build = $build
  summary = $summary
  backup_kg_path = $backupKgPath
  graph_summary_path = (Join-Path $logDir "graph_summary.json")
  live_trade_execution = $false
  cpu_expected_percent = 35
  gpu_3090_expected_percent = 65
  gb10_expected_percent = 0
}
Update-SkyCalendarEvent -Status "understood" -Trigger "Apollo improved HippoRAG refresh completed. Check the status artifact and graph summary."

exit 0
