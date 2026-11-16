#!/bin/bash
# Jellyfin 실행 — 프룻 데비안 "안에서" 돈다.
#
# 반드시 포그라운드로 실행해야 한다.
#   - 데몬으로 띄우면 프룻 세션이 끝날 때 같이 죽는다.
#   - runit 도 포그라운드를 요구한다(끝나면 "죽었다"로 보고 재시작).
#
# 호출:  proot-distro login debian -- bash ~/start-jellyfin.sh

# 🔑 데비안 패키지가 깔아 둔 환경값을 먼저 읽습니다.
#    경로 변수들은 아래 플래그가 어차피 덮어쓰지만, 이 두 줄은 플래그로 줄 수 없습니다:
#
#      LD_PRELOAD=/usr/lib/jellyfin/libjemalloc.so   메모리 할당기를 jemalloc 으로 교체
#      MALLOC_TRIM_THRESHOLD_=131072                 쓰고 난 메모리를 OS 에 더 자주 반납
#
#    🔴 이걸 빼면 Jellyfin 이 메모리를 더 오래 붙들고 있습니다. 램이 좁은 폰에서는
#       안드로이드가 백그라운드 프로세스를 수거(kill)할 위험이 그만큼 올라갑니다.
set -a
. /etc/default/jellyfin
set +a

exec /usr/bin/jellyfin \
  --webdir=/usr/share/jellyfin/web \
  --ffmpeg=/usr/lib/jellyfin-ffmpeg/ffmpeg \
  --datadir /var/lib/jellyfin \
  --configdir /etc/jellyfin \
  --logdir /var/log/jellyfin \
  --cachedir /var/cache/jellyfin
