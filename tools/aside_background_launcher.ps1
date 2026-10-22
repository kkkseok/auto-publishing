# Aside 브라우저를 최소화 상태로 시작 — 작업 스케줄러(AutoPublishing_Aside)가 로그온 시 호출.
#
# 네이버 발행(publishers/naver_aside)·네이버 통계 수집·서치어드바이저 색인 제출은
# 모두 `aside repl` 을 쓰는데, Aside 앱이 꺼져 있으면 CLI 가 "Aside isn't running"
# 으로 실패한다. 2026-10-22 재부팅 뒤 Aside 가 꺼진 채로 발견돼 추가했다.

$aside = "C:\Program Files\Aside\Application\Aside.exe"

if (-not (Test-Path $aside)) {
    Write-Host "[ap-aside] Aside 미발견: $aside"
    exit 1
}
if (Get-Process Aside -ErrorAction SilentlyContinue) {
    Write-Host "[ap-aside] 이미 실행 중"
    exit 0
}

# WScript.Shell.Run 의 WindowStyle=7 = SW_SHOWMINNOACTIVE — 최소화 + 포커스 안 가져감
$shell = New-Object -ComObject WScript.Shell
$shell.Run("`"$aside`"", 7, $false) | Out-Null
