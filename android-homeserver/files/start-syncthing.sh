#!/data/data/com.termux/files/usr/bin/sh
# Syncthing 실행 — runit 이 붙잡을 수 있게 **포그라운드**로 돈다.
#
# 호출:  sh ~/start-syncthing.sh
#
# 플래그가 전부 "스스로 하지 마라" 인 이유
#   --no-browser   폰에는 띄울 브라우저가 없다
#   --no-restart   스스로 재시작하지 않는다 — 되살리는 건 runit 의 일이다.
#                  둘 다 하려 들면 프로세스가 둘로 갈라진다.
#   --no-upgrade   스스로 업데이트하지 않는다. Termux 패키지로 관리하므로
#                  (`pkg upgrade`) 자체 업데이트는 오히려 어긋남을 만든다.
exec syncthing serve --no-browser --no-restart --no-upgrade 2>&1
