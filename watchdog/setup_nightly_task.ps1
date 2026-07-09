#Requires -RunAsAdministrator
param(
    [switch]$EnableDaytimePreTrade
)
$ErrorActionPreference = "Stop"

$psExe   = "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe"
$workDir = "C:\Users\blyth\Desktop\Engineering"

function Fix-Task {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [string]$ExtraArgs = "",
        [string]$TriggerTime,
        [int]$TimeoutHours = 10
    )
    $argStr = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
    if ($ExtraArgs) { $argStr += " $ExtraArgs" }
    $action   = New-ScheduledTaskAction -Execute $psExe -Argument $argStr -WorkingDirectory $workDir
    $trigger  = New-ScheduledTaskTrigger -Daily -At $TriggerTime
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopOnIdleEnd `
        -ExecutionTimeLimit (New-TimeSpan -Hours $TimeoutHours) `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
    Write-Host "OK: $TaskName"
}

function Fix-CommandTask {
    param(
        [string]$TaskName,
        [string]$Execute,
        [string]$Arguments,
        [string]$TriggerTime,
        [int]$TimeoutHours = 2
    )
    $action   = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -WorkingDirectory $workDir
    $trigger  = New-ScheduledTaskTrigger -Daily -At $TriggerTime
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopOnIdleEnd `
        -ExecutionTimeLimit (New-TimeSpan -Hours $TimeoutHours) `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
    Write-Host "OK: $TaskName"
}

function Fix-RepeatingTask {
    param(
        [string]$TaskName,
        [string]$ScriptPath,
        [string]$StartTime,
        [string]$IntervalMinutes = "30",
        [string]$DurationHours = "7",
        [int]$TimeoutHours = 1
    )
    $argStr = "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
    $action   = New-ScheduledTaskAction -Execute $psExe -Argument $argStr -WorkingDirectory $workDir
    $trigger  = New-ScheduledTaskTrigger -Once -At $StartTime -RepetitionInterval (New-TimeSpan -Minutes ([int]$IntervalMinutes)) -RepetitionDuration (New-TimeSpan -Hours ([int]$DurationHours))
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -DontStopOnIdleEnd `
        -ExecutionTimeLimit (New-TimeSpan -Hours $TimeoutHours) `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
    Write-Host "OK: $TaskName"
}

# New nightly pipeline (was never registered)
Fix-Task `
    -TaskName   "ApolloNightlyPipeline0100" `
    -ScriptPath "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_nightly_daily.ps1" `
    -TriggerTime "01:00AM" `
    -TimeoutHours 10

# Bounded main-corpus graph refresh after the nightly feed/article ingest.
Fix-Task `
    -TaskName   "ApolloImprovedHippoRAG0315" `
    -ScriptPath "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_improved_hipporag_0315.ps1" `
    -TriggerTime "03:15AM" `
    -TimeoutHours 4

# Fix bare powershell.exe path on existing active tasks
Fix-Task `
    -TaskName   "ApolloMarketStudy0730" `
    -ScriptPath "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_market_study_0730.ps1" `
    -ExtraArgs  "-Notify" `
    -TriggerTime "07:30AM" `
    -TimeoutHours 2

Fix-Task `
    -TaskName   "ApolloDailyReportOpen0745" `
    -ScriptPath "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_daily_report.ps1" `
    -ExtraArgs  "-Action open" `
    -TriggerTime "07:45AM" `
    -TimeoutHours 1

Fix-Task `
    -TaskName   "ApolloPremarketSimReadiness0815" `
    -ScriptPath "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_premarket_sim_readiness.ps1" `
    -TriggerTime "08:15AM" `
    -TimeoutHours 1

if ($EnableDaytimePreTrade) {
    Fix-RepeatingTask `
        -TaskName   "ApolloPreTradePlanner" `
        -ScriptPath "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_pretrade_planner.ps1" `
        -StartTime  "08:45AM" `
        -IntervalMinutes "30" `
        -DurationHours "7" `
        -TimeoutHours 1
} else {
    schtasks /Change /TN "ApolloPreTradePlanner" /Disable 2>$null | Out-Null
    Write-Host "SKIP: ApolloPreTradePlanner daytime schedule disabled by default. Use -EnableDaytimePreTrade to register it."
}

Fix-Task `
    -TaskName   "ApolloSwingStudy1620" `
    -ScriptPath "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_swing_study_1620.ps1" `
    -ExtraArgs  "-Notify" `
    -TriggerTime "04:20PM" `
    -TimeoutHours 2

Fix-Task `
    -TaskName   "ApolloDailyReportFinalize1635" `
    -ScriptPath "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_daily_report.ps1" `
    -ExtraArgs  "-Action finalize" `
    -TriggerTime "04:35PM" `
    -TimeoutHours 1

Fix-CommandTask `
    -TaskName    "ApolloTradeCycle" `
    -Execute     "C:\Users\blyth\AppData\Local\Programs\Python\Python312\python.exe" `
    -Arguments   "-m Apollo.trade_cycle run" `
    -TriggerTime "04:00AM" `
    -TimeoutHours 2

Fix-Task `
    -TaskName   "ApolloOvernightGateCheck0615" `
    -ScriptPath "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\check_overnight_gate.ps1" `
    -TriggerTime "06:15AM" `
    -TimeoutHours 1

Write-Host ""
Write-Host "=== Verification ==="
$verifyTasks = @("ApolloNightlyPipeline0100", "ApolloImprovedHippoRAG0315", "ApolloMarketStudy0730", "ApolloDailyReportOpen0745", "ApolloPremarketSimReadiness0815", "ApolloSwingStudy1620", "ApolloDailyReportFinalize1635", "ApolloTradeCycle", "ApolloOvernightGateCheck0615")
if ($EnableDaytimePreTrade) {
    $verifyTasks += "ApolloPreTradePlanner"
}
foreach ($name in $verifyTasks) {
    Write-Host "--- $name ---"
    schtasks /query /tn $name /fo LIST /v 2>&1 | Select-String "Task To Run|Next Run Time|Status"
}
