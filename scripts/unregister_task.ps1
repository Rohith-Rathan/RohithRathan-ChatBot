param([string]$TaskName = "TrainerCentralAssignmentTracker")
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'."
