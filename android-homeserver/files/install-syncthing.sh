#!/data/data/com.termux/files/usr/bin/sh
# 동기화 서버(Syncthing) — 설치부터 첫 실행까지 한 번에.
#
#   실행:  sh ~/install-syncthing.sh
#
# 하는 일 다섯 가지
#   ① Syncthing 설치
#   ② 설정·키 생성 — 로그인은 4장에서 만든 아이디·비밀번호를 그대로 씁니다
#   ③ PC에서 열 수 있게 GUI 주소 개방
#   ④ 🔴 바깥(공개 서버)에 자기를 알리지 않게 끄기
#   ⑤ Syncthing 실행 — 🔑 여기서 멈춰 있는 게 정상입니다.
#      브라우저로 확인한 뒤 Ctrl+C 로 멈추세요(6장에서 자동 실행으로 등록합니다).
#
# ✅ 여러 번 실행해도 안전합니다. 이미 끝난 단계는 건너뜁니다.
#
# 📌 동기화 폴더를 다른 곳에 두려면:  SYNC_DIR=~/문서 sh ~/install-syncthing.sh

set -u

PORT="${PORT:-8384}"
SYNC_DIR="${SYNC_DIR:-$HOME/sync}"
CONF="$HOME/.local/state/syncthing/config.xml"
START="$HOME/start-syncthing.sh"

started="$(date +%s)"
elapsed() {
    s=$(( $(date +%s) - started ))
    [ "$s" -ge 60 ] && printf '%d분 %d초' "$((s / 60))" "$((s % 60))" || printf '%d초' "$s"
}

echo
echo "동기화 서버(Syncthing) 설치"
echo "────────────────────────────────────────────"

# ── ① 설치 ────────────────────────────────────────────────────────────────
if command -v syncthing >/dev/null 2>&1; then
    echo "  ✅ 이미 설치됨 — $(syncthing --version 2>/dev/null | awk '{print $2}')"
else
    echo "  · 설치 중 (Termux 패키지)"
    if ! pkg install -y syncthing >/dev/null 2>&1; then
        echo "  ❌ 설치 실패 — 'pkg upgrade' 를 먼저 해보세요"
        exit 1
    fi
    echo "  ✅ 설치 완료 — $(syncthing --version 2>/dev/null | awk '{print $2}') ($(elapsed))"
fi

# 🔴 설정 파일을 고치는 단계가 있어서, 돌고 있으면 먼저 멈춰야 합니다.
#    Syncthing 은 끝날 때 자기 설정을 **메모리 내용으로 덮어씁니다** —
#    돌고 있는 채로 고치면 아래 ③④가 조용히 되돌려집니다.
if pgrep -x syncthing >/dev/null 2>&1; then
    echo
    echo "  🔴 Syncthing 이 이미 돌고 있습니다."
    echo "     설정을 고치려면 먼저 멈춰야 합니다(지금 고쳐도 종료할 때 되돌아갑니다):"
    echo
    echo "         export SVDIR=\$PREFIX/var/service && sv down syncthing"
    echo
    echo "     멈춘 뒤 이 스크립트를 다시 실행하세요."
    exit 1
fi

# ── ② 설정·키 ─────────────────────────────────────────────────────────────
echo
if [ -s "$CONF" ]; then
    echo "  ✅ 설정 있음 (다시 만들려면 ~/.local/state/syncthing 을 지우고 실행)"
else
    for f in "$HOME/.webdav_user" "$HOME/.webdav_pass"; do
        if [ ! -s "$f" ]; then
            echo "  ❌ $f 가 없습니다."
            echo "     4장(파일 서버)을 먼저 하세요:  sh ~/install-dufs.sh"
            echo "     🔑 이 폰의 서비스는 아이디·비밀번호를 하나로 묶어 씁니다."
            exit 1
        fi
    done

    echo "  · 설정·키 생성 — 로그인은 4장에서 정한 것을 그대로 씁니다"
    # 🔑 --gui-password=- 는 비밀번호를 **표준입력**으로 받습니다.
    #    명령줄에 직접 쓰면 `ps` 목록에 그대로 보입니다.
    U="$(tr -d '\n' < "$HOME/.webdav_user")"
    if ! tr -d '\n' < "$HOME/.webdav_pass" \
         | syncthing generate --gui-user="$U" --gui-password=- >/dev/null 2>&1; then
        echo "  ❌ 설정 생성 실패"
        exit 1
    fi
    echo "  ✅ 설정 생성 완료"
fi

if [ ! -s "$CONF" ]; then
    echo "  ❌ 설정 파일을 찾을 수 없습니다: $CONF"
    exit 1
fi

# ── ③ GUI 주소 개방 + 찌꺼기 정리 ─────────────────────────────────────────
# 기본값은 127.0.0.1 이라 **폰 안에서만** 열립니다. PC에서 보려면 열어야 합니다.
sed -i 's|<address>127.0.0.1:'"$PORT"'</address>|<address>0.0.0.0:'"$PORT"'</address>|' "$CONF"
# ⚠️ generate 가 남기는 '빈 폴더' 항목을 지웁니다 — 두면 GUI 에 오류 폴더로 잡힙니다.
sed -i '/<folder id="" /,/<\/folder>/d' "$CONF"
echo "  ✅ GUI 주소 개방 — 0.0.0.0:$PORT"

# ── ④ 🔴 바깥에 알리지 않게 ───────────────────────────────────────────────
# 설치 직후 Syncthing 은 인터넷의 공개 서버에 자기 존재를 알립니다.
# 집 밖 동기화를 위한 기능인데, Tailscale 을 쓰면 필요하지 않습니다.
# 🔑 로컬 탐색(localAnnounce)은 켜 둡니다 — 같은 Wi-Fi 안에서 서로 찾는 기능입니다.
for opt in globalAnnounceEnabled relaysEnabled natEnabled; do
    sed -i "s|<$opt>true</$opt>|<$opt>false</$opt>|" "$CONF"
done
echo "  ✅ 전역 탐색 · 릴레이 · NAT 통과 끔 (로컬 탐색은 유지)"

# 확인 — 실제로 꺼졌는지 눈으로 보여줍니다
for opt in globalAnnounceEnabled relaysEnabled natEnabled localAnnounceEnabled; do
    v="$(grep -oE "<$opt>[^<]*</$opt>" "$CONF" | head -1 | sed -e "s|<$opt>||" -e "s|</$opt>||")"
    printf '       %-24s %s\n' "$opt" "${v:-?}"
done

# ── ⑤ 동기화 폴더 ─────────────────────────────────────────────────────────
mkdir -p "$SYNC_DIR"
echo "  ✅ 동기화 폴더 — $SYNC_DIR"

if [ ! -f "$START" ]; then
    echo
    echo "  ❌ ~/start-syncthing.sh 가 없습니다."
    echo "     「스크립트 내려받기」를 먼저 하세요:  sh ~/get-files.sh"
    exit 1
fi

# ── ⑥ 첫 실행 ─────────────────────────────────────────────────────────────
echo
echo "────────────────────────────────────────────"
echo "  여기까지 $(elapsed) 걸렸습니다."
echo
echo "  이제 띄웁니다. 🔑 주소가 https 입니다 — http 로 치면 빈 화면이 뜹니다:"
echo
echo "      https://<폰주소>:$PORT/"
echo
echo "  ⚠️ 브라우저가 '안전하지 않음' 경고를 띄웁니다. 스스로 만든 인증서라 그렇습니다."
echo "     고급 ▸ 계속 진행 으로 들어가면 됩니다."
echo "  로그인은 4장에서 정한 아이디·비밀번호입니다."
echo "  🔑 확인했으면 Ctrl+C 로 멈추세요 — 6장에서 자동 실행으로 등록합니다."
echo "────────────────────────────────────────────"
echo

exec sh "$START"
