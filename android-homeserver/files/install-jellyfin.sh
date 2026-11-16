#!/data/data/com.termux/files/usr/bin/sh
# 미디어 서버(Jellyfin) — 데비안 설치부터 첫 실행까지 한 번에.
#
#   실행:  sh ~/install-jellyfin.sh
#
# 하는 일 다섯 가지
#   ① proot-distro 설치
#   ② 데비안 설치 (약 500MB · 1분)
#   ③ 데비안 안에 Jellyfin 설치 (저장소 등록 + apt)
#   ④ 폰 저장소를 데비안에서 보이게 바로가기 만들기
#   ⑤ Jellyfin 실행 — 🔑 여기서 멈춰 있는 게 정상입니다.
#      브라우저로 확인한 뒤 Ctrl+C 로 멈추세요(7장에서 자동 실행으로 등록합니다).
#
# ✅ 여러 번 실행해도 안전합니다. 이미 끝난 단계는 건너뜁니다.
#
# ⏱ 처음 실행하면 5~10분 걸립니다. 대부분 내려받는 시간입니다.
#
# 📌 다른 이름으로 깔고 싶으면:  DISTRO=debian-test sh ~/install-jellyfin.sh

set -u

DISTRO="${DISTRO:-debian}"      # 폰에 올릴 컨테이너 이름
IMAGE="${IMAGE:-debian}"        # 받아올 배포판 이미지 (보통 이름과 같습니다)
PORT="${PORT:-8096}"
JF_START="$HOME/start-jellyfin.sh"
INSIDE="$HOME/.jf-setup-inside.sh"

# 🔑 데비안 안에서도 Termux 홈은 이 경로로 그대로 보입니다(프룻은 폴더를 감추지 않습니다).
TERMUX_HOME="/data/data/com.termux/files/home"

started="$(date +%s)"
elapsed() {   # 시작부터 지금까지 "3분 12초" 형태로
    s=$(( $(date +%s) - started ))
    [ "$s" -ge 60 ] && printf '%d분 %d초' "$((s / 60))" "$((s % 60))" || printf '%d초' "$s"
}

echo
echo "미디어 서버(Jellyfin) 설치"
echo "────────────────────────────────────────────"
echo "  ⏱ 처음이면 5~10분쯤 걸립니다. 대부분 내려받는 시간입니다."
echo "     중간에 글자가 한동안 안 바뀌어도 멈춘 게 아닙니다."
echo

# ── ① proot-distro ────────────────────────────────────────────────────────
if command -v proot-distro >/dev/null 2>&1; then
    echo "  ✅ proot-distro 있음"
else
    echo "  · proot-distro 설치 중"
    pkg install -y proot-distro >/dev/null 2>&1 || {
        echo "  ❌ proot-distro 설치 실패 — 'pkg upgrade' 를 먼저 해보세요"
        exit 1
    }
    echo "  ✅ proot-distro 설치 완료"
fi

# ── ② 배포판 ──────────────────────────────────────────────────────────────
# 🔑 설치 여부는 폴더를 뒤지지 않고 "들어가 보는 것"으로 판단합니다.
#    proot-distro 버전마다 저장 위치가 달라서, 경로로 찾으면 버전이 바뀔 때 깨집니다.
if proot-distro login "$DISTRO" -- true >/dev/null 2>&1; then
    echo "  ✅ $DISTRO 있음"
else
    echo "  · $DISTRO 설치 중 — 약 500MB, 1분쯤 걸립니다"
    if [ "$DISTRO" = "$IMAGE" ]; then
        ok_install="proot-distro install $IMAGE"
    else
        # 이름을 따로 줄 때만 --name 을 씁니다(같은 배포판을 두 벌 깔 때).
        ok_install="proot-distro install --name $DISTRO $IMAGE"
    fi
    if ! $ok_install; then
        echo "  ❌ $DISTRO 설치 실패"
        exit 1
    fi
    echo "  ✅ $DISTRO 설치 완료 ($(elapsed))"
fi

# ── ③ Jellyfin ────────────────────────────────────────────────────────────
echo
if proot-distro login "$DISTRO" -- command -v jellyfin >/dev/null 2>&1; then
    echo "  ✅ Jellyfin 이미 설치됨"
else
    echo "  · Jellyfin 설치 중 — 내려받는 데 몇 분 걸립니다"

    # 🔑 데비안 안에서 할 일을 파일로 적어 넘깁니다.
    #    한 줄짜리 명령으로 밀어 넣으면 따옴표가 두 겹으로 겹쳐 아주 틀리기 쉽습니다.
    cat > "$INSIDE" <<'INSIDE_EOF'
#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

echo "    [1/4] 패키지 목록 갱신 (20초쯤)"
apt-get update -qq

echo "    [2/4] 기본 도구 설치 (curl · gnupg)"
apt-get install -y -qq curl gnupg ca-certificates

echo "    [3/4] Jellyfin 저장소 등록"
mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key \
  | gpg --dearmor --yes -o /etc/apt/keyrings/jellyfin.gpg

# 🔑 저장소 등록 파일을 "지금 이 데비안에 맞춰" 만듭니다.
#    코드명(trixie/bookworm…)과 아키텍처(arm64/armhf)가 안 맞으면
#    apt 가 "저장소에 그런 게 없다"며 설치를 거부합니다.
. /etc/os-release
ARCH="$(dpkg --print-architecture)"
cat > /etc/apt/sources.list.d/jellyfin.sources <<EOF
Types: deb
URIs: https://repo.jellyfin.org/${ID}
Suites: ${VERSION_CODENAME}
Components: main
Architectures: ${ARCH}
Signed-By: /etc/apt/keyrings/jellyfin.gpg
EOF
echo "    저장소: ${ID} / ${VERSION_CODENAME} / ${ARCH}"

apt-get update -qq

echo "    [4/4] Jellyfin 내려받기·설치 — ⏱ 여기가 가장 오래 걸립니다 (수백 MB)"
echo "          아래로 진행 상황이 흐릅니다."
# 🔑 이 단계만 -qq 를 빼고 apt 의 출력을 그대로 보여줍니다.
#    받는 양이 많아서, 조용히 돌면 멈춘 것처럼 보입니다.
# ⚠️ 'invoke-rc.d: policy-rc.d denied execution of start' 경고가 나오는 게 정상입니다.
#    폰에는 systemd 가 없어서 자동 기동만 못 할 뿐, 설치는 정상입니다.
apt-get install -y jellyfin
INSIDE_EOF

    if ! proot-distro login "$DISTRO" -- bash "$TERMUX_HOME/.jf-setup-inside.sh"; then
        echo
        echo "  ❌ Jellyfin 설치 실패"
        echo "     위 메시지에 'Suites' 나 '404' 가 보이면 데비안 버전 문제입니다."
        rm -f "$INSIDE"
        exit 1
    fi
    rm -f "$INSIDE"
    echo "  ✅ Jellyfin 설치 완료"
fi

# ── ④ 폰 저장소 바로가기 ──────────────────────────────────────────────────
# 🔴 Jellyfin 폴더 선택창에서 /storage/emulated 까지만 보이고 그 안의 0 이 안 보입니다.
#    안드로이드가 "통과는 되지만 목록은 못 읽는" 권한(drwx--x--x)을 주기 때문입니다.
#    바로가기를 만들어 두면 탐색이 됩니다.  ln -sfn 이라 여러 번 해도 안전합니다.
echo
proot-distro login "$DISTRO" -- sh -c '
  ln -sfn /storage/emulated/0/Movies   /media/movies
  ln -sfn /storage/emulated/0/Download /media/download
  ln -sfn /storage/emulated/0          /media/phone
' && echo "  ✅ 라이브러리 바로가기 — /media/movies · /media/download · /media/phone"

# ── ⑤ 실행 ────────────────────────────────────────────────────────────────
if [ ! -f "$JF_START" ]; then
    echo
    echo "  ❌ ~/start-jellyfin.sh 가 없습니다."
    echo "     「스크립트 내려받기」를 먼저 하세요:  sh ~/get-files.sh"
    exit 1
fi

echo
echo "────────────────────────────────────────────"
echo "  여기까지 $(elapsed) 걸렸습니다."
echo
echo "  이제 띄웁니다. ⏱ 첫 기동은 20~30초 걸립니다."
echo "  'Startup complete' 가 보이면 브라우저로 들어가세요:"
echo
echo "      http://<폰주소>:$PORT/"
echo
echo "  계정을 만들고, 라이브러리 폴더로 /media/movies 를 지정하면 됩니다."
echo "  🔴 하드웨어 가속은 켜지 마세요 — 폰에서는 동작하지 않습니다."
echo "  🔑 확인했으면 Ctrl+C 로 멈추세요 — 7장에서 자동 실행으로 등록합니다."
echo "────────────────────────────────────────────"
echo

exec proot-distro login "$DISTRO" -- bash "$TERMUX_HOME/start-jellyfin.sh"
