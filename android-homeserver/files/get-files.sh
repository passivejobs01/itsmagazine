#!/data/data/com.termux/files/usr/bin/sh
# 안드로이드 홈서버 — 가이드에 쓰이는 파일을 한 번에 받습니다.
#
#   받기:  curl -fsSL -o ~/get-files.sh \
#            https://raw.githubusercontent.com/passivejobs01/itsmagazine/main/android-homeserver/files/get-files.sh
#   보기:  cat ~/get-files.sh          ← 🔴 실행하기 전에 한 번 보세요
#   실행:  sh ~/get-files.sh
#
# 🔑 이 스크립트가 하는 일은 "받아서 제자리에 두는 것"까지입니다.
#    패키지 설치·서비스 등록·실행은 가이드의 각 장에서 직접 합니다 —
#    무엇을 왜 하는지 보면서 가는 게 이 문서의 목적이기 때문입니다.
#
# ✅ 여러 번 실행해도 안전합니다. 받다가 끊겨도 **반쯤 받은 파일을 남기지 않습니다**
#    (.part 로 받아서 성공했을 때만 제자리로 옮깁니다).

set -u

# 📌 저장소를 복제해서 쓰신다면 주소만 바꿔 끼우면 됩니다:
#      BASE=https://내주소/files  sh ~/get-files.sh
BASE="${BASE:-https://raw.githubusercontent.com/passivejobs01/itsmagazine/main/android-homeserver/files}"

ok=0
fail=0

# fetch <저장소 안 경로> <폰에 저장할 경로>
fetch() {
    rem="$1"
    loc="$2"
    tmp="$loc.part"

    # 2>/dev/null — curl 이 재시도하면서 찍는 영어 오류가 아래 ❌ 줄과 겹쳐 보인다.
    #               무엇이 실패했는지는 우리가 한글로 한 줄 찍는 것으로 충분하다.
    if ! curl -fsSL --retry 2 --connect-timeout 10 -o "$tmp" "$BASE/$rem" 2>/dev/null; then
        printf '  ❌ %-24s 내려받기 실패 (주소·인터넷 확인)\n' "$rem"
        rm -f "$tmp"
        fail=$((fail + 1))
        return 1
    fi

    if [ ! -s "$tmp" ]; then
        printf '  ❌ %-24s 빈 파일이 왔습니다\n' "$rem"
        rm -f "$tmp"
        fail=$((fail + 1))
        return 1
    fi

    # 저장소 주소가 틀리면 파일 대신 "없음" 안내 웹페이지가 올 수 있다.
    # 그대로 두면 나중에 "스크립트가 이상하다"로 한참 헤매게 된다.
    case "$(head -c 32 "$tmp" 2>/dev/null)" in
        '<!DOCTYPE'* | '<html'* | '<HTML'*)
            printf '  ❌ %-24s 파일이 아니라 웹페이지가 왔습니다\n' "$rem"
            rm -f "$tmp"
            fail=$((fail + 1))
            return 1
            ;;
    esac

    mv "$tmp" "$loc"
    printf '  ✅ %-24s %s바이트\n' "$rem" "$(wc -c < "$loc" | tr -d ' ')"
    ok=$((ok + 1))
}

echo
echo "안드로이드 홈서버 — 파일 내려받기"
echo "────────────────────────────────────────────"
echo
echo "[1/2] 홈 폴더 (~)"

fetch install-dufs.sh        "$HOME/install-dufs.sh"
fetch start-dufs.sh          "$HOME/start-dufs.sh"
fetch install-jellyfin.sh    "$HOME/install-jellyfin.sh"
fetch start-jellyfin.sh      "$HOME/start-jellyfin.sh"
fetch install-syncthing.sh   "$HOME/install-syncthing.sh"
fetch start-syncthing.sh     "$HOME/start-syncthing.sh"
fetch setup-services.sh      "$HOME/setup-services.sh"
fetch install-boot.sh        "$HOME/install-boot.sh"
fetch boot-start-services.sh "$HOME/boot-start-services.sh"

echo
echo "[2/2] 내 스크립트 (~/myapp)"

mkdir -p "$HOME/myapp"
fetch myapp/dashboard.py "$HOME/myapp/dashboard.py"
fetch myapp/sensors.py   "$HOME/myapp/sensors.py"
fetch myapp/control.js   "$HOME/myapp/control.js"
fetch myapp/server.py    "$HOME/myapp/server.py"
fetch myapp/server.js    "$HOME/myapp/server.js"
fetch myapp/game.py      "$HOME/myapp/game.py"

# 실행 권한 — 🔴 이게 없으면 Termux:Boot 이 부팅 스크립트를 "그냥 무시"합니다.
#    에러가 안 나서 원인을 찾기 어려운 자리라 여기서 한 번에 걸어 둡니다.
chmod +x "$HOME/install-dufs.sh" "$HOME/start-dufs.sh" \
         "$HOME/install-jellyfin.sh" "$HOME/start-jellyfin.sh" \
         "$HOME/install-syncthing.sh" "$HOME/start-syncthing.sh" \
         "$HOME/setup-services.sh" "$HOME/install-boot.sh" \
         "$HOME/boot-start-services.sh" 2>/dev/null

echo
echo "────────────────────────────────────────────"
printf '  받음 %d개' "$ok"
if [ "$fail" -gt 0 ]; then
    printf ' · 실패 %d개\n' "$fail"
    echo
    echo "  🔴 실패한 파일이 있습니다. 인터넷을 확인하고 다시 실행하세요."
    echo "     (이미 받은 파일은 그대로 두고 실패한 것만 다시 받습니다)"
    echo
    exit 1
fi

printf '\n'
echo
echo "  받은 파일이 어디에 쓰이는지:"
echo "    install-dufs.sh          4장  파일 서버 설치 + 첫 실행"
echo "    start-dufs.sh            4장  파일 서버 실행"
echo "    install-jellyfin.sh      5장  미디어 서버 설치 + 첫 실행"
echo "    start-jellyfin.sh        5장  미디어 서버 실행"
echo "    install-syncthing.sh     6장  동기화 설치 + 첫 실행"
echo "    start-syncthing.sh       6장  동기화 실행"
echo "    setup-services.sh        7장  자동시작 서비스 등록"
echo "    install-boot.sh          7장  부팅 스크립트 설치"
echo "    boot-start-services.sh   7장  부팅 스크립트"
echo "    myapp/dashboard.py       8장  카메라 + 상태 대시보드"
echo "    myapp/sensors.py         부록 A  폰 센서 대시보드"
echo "    myapp/control.js         부록 A  폰 제어판"
echo "    myapp/server.py · .js    부록 A  내 스크립트 예제"
echo "    myapp/game.py            게임 + 순위표 (폰이 점수를 보관한다)"
echo
echo "  📌 내용이 궁금하면 언제든 볼 수 있습니다:  cat ~/start-dufs.sh"
echo
echo "  👉 다음은 가이드 4장(파일 서버)으로 갑니다."
echo
