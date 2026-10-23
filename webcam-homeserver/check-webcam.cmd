@echo off
chcp 65001 >nul
rem ─────────────────────────────────────────────────────────────
rem  웹캠 점검 — go2rtc 설정을 쓰기 전에 반드시 먼저 돌린다
rem    1) FFmpeg 이 설치돼 있는지
rem    2) 웹캠 장치 이름
rem    3) 그 웹캠이 지원하는 해상도·fps·코덱 조합
rem ─────────────────────────────────────────────────────────────

echo ===== 1) FFmpeg 확인 =====
ffmpeg -version 2>nul | findstr /B "ffmpeg version"
if errorlevel 1 (
  echo   [X] FFmpeg 을 찾을 수 없습니다.
  echo       https://www.gyan.dev/ffmpeg/builds/ 에서 release-essentials 를 받아
  echo       PATH 에 넣거나 go2rtc.yaml 의 ffmpeg.bin 에 경로를 적으세요.
  goto :end
)
echo.

echo ===== 2) 웹캠 장치 이름 =====
echo   ("(video)" 로 끝나는 줄이 웹캠입니다. 따옴표 안 이름을 그대로 쓰세요)
echo.
ffmpeg -list_devices true -f dshow -i dummy 2>&1 | findstr /C:"(video)"
echo.

if "%~1"=="" (
  echo ===== 3) 지원 조합 =====
  echo   장치 이름을 인자로 넣어 다시 실행하세요:
  echo       check-webcam.cmd "Cox cpc1080"
  goto :end
)

echo ===== 3) "%~1" 지원 조합 =====
echo   pixel_format / 해상도 / fps 를 확인하고 go2rtc.yaml 에 그대로 반영하세요.
echo   mjpeg 쪽 fps 가 보통 더 높습니다 - input_format=mjpeg 를 꼭 넣으세요.
echo.
ffmpeg -list_options true -f dshow -i video="%~1" 2>&1 | findstr /C:"pixel_format" /C:"vcodec"

:end
echo.
pause
