#!/data/data/com.termux/files/usr/bin/sh
# 폰 부팅 시 Termux:Boot 이 실행하는 스크립트.
# 설치 위치: ~/.termux/boot/start-services.sh  (실행 권한 필요)
#
#   termux-wake-lock          안드로이드가 절전으로 재우지 못하게 붙잡는다
#   start-services.sh         runit 을 기동 → 등록된 서비스가 모두 올라온다

termux-wake-lock
. /data/data/com.termux/files/usr/etc/profile.d/start-services.sh
