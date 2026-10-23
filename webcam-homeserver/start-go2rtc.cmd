@echo off
rem go2rtc 실행 — 작업 스케줄러 등록용
rem
rem  🔴 cd /d 가 핵심이다. go2rtc 는 '현재 작업 디렉터리' 에서 go2rtc.yaml 을 찾는다.
rem     다른 위치에서 실행하면 설정을 못 읽고 기본값으로 조용히 뜬다.

cd /d C:\go2rtc
if not exist logs mkdir logs
go2rtc.exe >> logs\go2rtc.log 2>&1
