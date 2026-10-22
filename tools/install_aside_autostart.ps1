# Windows 작업 스케줄러에 'AutoPublishing_Aside' 작업 등록 — 로그온 시 Aside 최소화 시작.
#
# 사용법:
#   powershell -ExecutionPolicy Bypass -File tools\install_aside_autostart.ps1
# 제거:
#   powershell -ExecutionPolicy Bypass -File tools\install_aside_autostart.ps1 -Uninstall

param([switch]$Uninstall)

$projectDir = (Resolve-Path "$PSScriptRoot\..").Path
$taskName = "AutoPublishing_Aside"

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "제거 완료: $taskName"
    }
    exit 0
}

$wrapper = Join-Path $projectDir "tools\aside_background_launcher.ps1"
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapper`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Auto Publishing — Aside 최소화 시작 (네이버 발행·통계·색인용)" | Out-Null
Write-Host "등록: $taskName (로그온 시 Aside 최소화 시작)"
