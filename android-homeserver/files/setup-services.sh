#!/data/data/com.termux/files/usr/bin/bash
# runit 서비스 등록 — 서버를 자동 기동·자동 복구 대상으로 만든다.
#
#   bash ~/setup-services.sh                  깔려 있는 서버를 전부 등록
#   bash ~/setup-services.sh dash             특정 서비스만 등록
#   bash ~/setup-services.sh pyapp nodeapp    여러 개 지정
#
# 🔑 "깔려 있는 것만" 등록한다. 그래서 8장(Syncthing)처럼 나중에 한 장을 더 해도
#    **이 스크립트를 다시 실행하면** 새로 깐 것이 추가된다.
# ✅ 여러 번 실행해도 안전하다 — 이미 등록된 것은 같은 내용으로 다시 쓸 뿐이다.
#
# (sshd 는 `sv-enable sshd` 로 이미 등록했다고 가정한다)

set -uo pipefail
SV="$PREFIX/var/service"

# 🔑 sv · sv-enable 은 SVDIR 을 보고 서비스 폴더를 찾는다.
#    평소엔 Termux 로그인 셸이 잡아주지만, **PC에서 ssh 로 명령 한 줄만 실행하면**
#    그 셸에는 없다. 그러면 "unable to change to service directory" 로 전부 실패한다.
#    스크립트가 직접 잡아두면 어디서 실행하든 똑같이 동작한다.
export SVDIR="$SV"
export LOGDIR="${LOGDIR:-$PREFIX/var/log}"

# ── 서비스 정의 ────────────────────────────────────────────────────────────
# ⚠️ run 스크립트는 반드시 포그라운드(exec)여야 한다.
#    '&' 나 데몬 모드로 띄우면 runit 이 "끝났다"고 보고 1초마다 무한 재시작한다.
svc_run() {
  case "$1" in
    dufs)      echo 'exec "$HOME/start-dufs.sh" 2>&1' ;;
    jellyfin)  echo 'exec proot-distro login debian -- bash "$HOME/start-jellyfin.sh" 2>&1' ;;
    syncthing) echo 'exec "$HOME/start-syncthing.sh" 2>&1' ;;
    dash)      echo 'exec python "$HOME/myapp/dashboard.py" 2>&1' ;;
    pyapp)     echo 'exec python "$HOME/myapp/sensors.py" 2>&1' ;;
    nodeapp)   echo 'exec node "$HOME/myapp/control.js" 2>&1' ;;
    *)         return 1 ;;
  esac
}

# 준비됐는지(그 장을 했는지) 본다
svc_ready() {
  case "$1" in
    dufs)      [ -x "$HOME/dufs/dufs" ] && [ -f "$HOME/start-dufs.sh" ] ;;
    jellyfin)  [ -f "$HOME/start-jellyfin.sh" ] &&
               proot-distro login debian -- command -v jellyfin >/dev/null 2>&1 ;;
    syncthing) [ -f "$HOME/start-syncthing.sh" ] && command -v syncthing >/dev/null 2>&1 ;;
    dash)      [ -f "$HOME/myapp/dashboard.py" ] && command -v python >/dev/null 2>&1 ;;
    pyapp)     [ -f "$HOME/myapp/sensors.py" ] && command -v python >/dev/null 2>&1 ;;
    nodeapp)   [ -f "$HOME/myapp/control.js" ] && command -v node >/dev/null 2>&1 ;;
    *)         return 1 ;;
  esac
}

svc_why() {   # 왜 건너뛰었는지 한 줄로
  case "$1" in
    dufs)      echo "4장을 아직 안 했습니다" ;;
    jellyfin)  echo "5장을 아직 안 했습니다" ;;
    syncthing) echo "6장을 아직 안 했습니다 — 나중에 이 스크립트를 다시 실행하세요" ;;
    dash)      echo "8장을 아직 안 했습니다" ;;
    pyapp)     echo "부록 A 를 아직 안 했습니다" ;;
    nodeapp)   echo "부록 A 를 아직 안 했습니다 (Node 미설치)" ;;
  esac
}

# 인자가 없으면 기본 목록 — 이 순서대로 본다
DEFAULT="dufs jellyfin syncthing"
TARGETS="${*:-$DEFAULT}"

mk_service() {   # $1=서비스명  $2=run 스크립트 본문
  mkdir -p "$SV/$1/log"

  printf '%s\n' '#!/data/data/com.termux/files/usr/bin/sh' "$2" > "$SV/$1/run"

  # 로그: svlogd 가 타임스탬프를 붙이고 자동 로테이션한다
  printf '%s\n' \
    '#!/data/data/com.termux/files/usr/bin/sh' \
    'pwd=${PWD%/*}; service=${pwd##*/}' \
    'mkdir -p "$LOGDIR/sv/$service"' \
    'exec svlogd -tt "$LOGDIR/sv/$service"' > "$SV/$1/log/run"

  chmod +x "$SV/$1/run" "$SV/$1/log/run"
  echo "등록: $1"
}

enabled=()
for s in $TARGETS; do
  if ! svc_run "$s" >/dev/null 2>&1; then
    echo "🔴 모르는 서비스: $s"
    echo "   쓸 수 있는 이름: dufs jellyfin syncthing dash pyapp nodeapp"
    exit 1
  fi
  if svc_ready "$s"; then
    mk_service "$s" "$(svc_run "$s")"
    enabled+=("$s")
  else
    echo "건너뜀: $s ($(svc_why "$s"))"
  fi
done

if [ ${#enabled[@]} -eq 0 ]; then
  echo
  echo "🔴 등록할 것이 없습니다. 해당 장을 먼저 끝내고 다시 실행하세요."
  exit 1
fi

echo
echo "자동시작 켜는 중…"
sleep 8                      # runsvdir 이 새 폴더를 발견할 때까지(약 5초마다 훑는다)
for s in "${enabled[@]}"; do
  # ⚠️ 이미 켜져 있으면 sv-enable 이 실패한다. 다시 실행해도 멈추지 않게 받아 넘긴다.
  sv-enable "$s" || echo "  (이미 켜져 있음: $s)"
done

echo
echo "── 상태 (전부 run: 이면 성공) ──"
sleep 15
sv status sshd "${enabled[@]}"
