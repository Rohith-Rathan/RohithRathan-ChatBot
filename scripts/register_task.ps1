# Registers a Windows Task Scheduler job that runs the tracker every 30 minutes.
# Usage (PowerShell, no admin needed):
#   .\scripts\register_task.ps1 -VenvPath "C:\Users\<you>\.venvs\trainer-central"
param(
    [string]$VenvPath = "$env:USERPROFILE\.venvs\trainer-central",
    [string]$TaskName = "TrainerCentralAssignmentTracker",
    [int]$IntervalMinutes = 30
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $VenvPath "Scripts\pythonw.exe"   # pythonw = no console window
if (-not (Test-Path $Pythonw)) { throw "pythonw.exe not found at $Pythonw" }
if (-not (Test-Path (Join-Path $ProjectDir "token.json"))) {
    Write-Warning "token.json missing - run 'python -m src.test_drive' once to authorize Google Drive."
}

$action  = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m src.main" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
           -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
           -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
           -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
# Runs as the current user while logged on (no stored password).
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Drive -> Claude -> Trainer Central dashboard, every $IntervalMinutes min" -Force | Out-Null

Write-Host "Registered '$TaskName': every $IntervalMinutes min, first run at $((Get-Date).AddMinutes(1))."
Write-Host "Logs: $ProjectDir\logs\tracker.log"
