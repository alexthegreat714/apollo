$ErrorActionPreference = "Stop"

$taskName = "ApolloLearningLoop1628"
$scriptPath = "C:\Users\blyth\Desktop\Engineering\Apollo\watchdog\run_apollo_learning_loop_1628.ps1"
$action = New-ScheduledTaskAction -Execute "C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Notify"
$trigger = New-ScheduledTaskTrigger -Daily -At 4:28PM
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Scheduled $taskName for 4:28 PM daily."
