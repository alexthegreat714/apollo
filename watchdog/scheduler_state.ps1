function Write-ApolloJsonAtomic {
    param([string]$Path, [object]$Payload)
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $tempPath = "${Path}.tmp.$PID"
    $Payload | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $tempPath -Encoding UTF8
    Move-Item -LiteralPath $tempPath -Destination $Path -Force
}

function Test-ApolloCommandIdentity {
    param([string]$CommandLine, [object[]]$ExpectedTokens)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    foreach ($token in @($ExpectedTokens)) {
        if ($CommandLine.IndexOf([string]$token, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            return $false
        }
    }
    return $true
}

function Get-ApolloSchedulerDecision {
    param(
        [string]$ManifestState,
        [double]$HeartbeatAgeMinutes,
        [bool]$LockStale,
        [bool]$ProcessExists,
        [bool]$ProcessIdentityMatches,
        [string]$TaskState = ""
    )
    $terminal = @("succeeded", "failed", "abandoned", "recovered_stale", "abandoned_identity_mismatch")
    $activeState = $ManifestState -in @("starting", "running")
    $hung = $activeState -and $HeartbeatAgeMinutes -gt 15 -and $LockStale
    $classification = $ManifestState
    $mayTerminate = $false

    if ($activeState -and -not $ProcessExists) {
        $classification = "abandoned"
    } elseif ($activeState -and -not $ProcessIdentityMatches) {
        $classification = "abandoned_identity_mismatch"
    } elseif ($hung -and $ProcessExists -and $ProcessIdentityMatches) {
        $classification = "hung_exact_process"
        $mayTerminate = $true
    } elseif ($activeState) {
        $classification = "active"
    }

    $consistent = (
        ($TaskState -eq "Running" -and $classification -eq "active") -or
        ($TaskState -eq "Ready" -and ($ManifestState -in $terminal))
    )
    return [pscustomobject]@{
        classification = $classification
        hung = $hung
        may_terminate = $mayTerminate
        consistent = $consistent
    }
}
