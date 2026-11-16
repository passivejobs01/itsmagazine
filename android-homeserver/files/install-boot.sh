#!/data/data/com.termux/files/usr/bin/sh
# 부팅 자동시작 — 부팅 스크립트를 제자리에 놓는다.
#
#   실행:  sh ~/install-boot.sh
#
# 하는 일
#   ① ~/.termux/boot/ 폴더를 만든다 (Termux:Boot 이 이 폴더만 본다)
#   ② 부팅 스크립트를 start-services.sh 라는 이름으로 그 안에 둔다
#   ③ 🔴 실행 권한을 건다 — 이게 없으면 Termux:Boot 이 **그냥 무시한다**
#   ④ 제대로 들어갔는지 확인해서 보여준다
#
# ✅ 여러 번 실행해도 안전하다.
#
# 🔴 이 스크립트가 못 해주는 것이 하나 있다 — **Termux:Boot 앱을 한 번 여는 것**.
#    설치만 하고 한 번도 실행하지 않은 앱에는 안드로이드가 부팅 신호를 보내지 않는다.
#    아래에서 다시 알려준다.

set -u

BOOTDIR="$HOME/.termux/boot"
TARGET="$BOOTDIR/start-services.sh"
SRC="$HOME/boot-start-services.sh"

echo
echo "부팅 자동시작 설치"
echo "────────────────────────────────────────────"

# ── ① 폴더 ────────────────────────────────────────────────────────────────
mkdir -p "$BOOTDIR" || { echo "  ❌ $BOOTDIR 를 만들지 못했습니다"; exit 1; }

# ── ② 스크립트 ────────────────────────────────────────────────────────────
if [ -f "$SRC" ]; then
    mv "$SRC" "$TARGET"
    echo "  ✅ 옮김 — ~/boot-start-services.sh → ~/.termux/boot/start-services.sh"
elif [ -f "$TARGET" ]; then
    echo "  ✅ 이미 있음 — ~/.termux/boot/start-services.sh"
else
    # 받아둔 게 없으면 직접 만든다. 내용은 두 줄뿐이다.
    cat > "$TARGET" <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
. /data/data/com.termux/files/usr/etc/profile.d/start-services.sh
EOF
    echo "  ✅ 새로 만듦 — ~/.termux/boot/start-services.sh"
fi

# ── ③ 실행 권한 ───────────────────────────────────────────────────────────
chmod +x "$TARGET"

# ── ④ 확인 ────────────────────────────────────────────────────────────────
echo
echo "  들어간 내용:"
sed 's/^/      /' "$TARGET"
echo
echo "  권한:"
ls -l "$TARGET" | sed 's/^/      /'

case "$(ls -l "$TARGET")" in
    *x*) ;;
    *)   echo
         echo "  🔴 실행 권한(x)이 없습니다. 이대로면 부팅 때 무시됩니다."
         exit 1 ;;
esac

# ── 앱이 깔려 있는지 ──────────────────────────────────────────────────────
echo
if [ -d /data/data/com.termux.boot ]; then
    echo "  ✅ Termux:Boot 앱 설치됨"
else
    echo "  🔴 Termux:Boot 앱이 안 보입니다."
    echo "     F-Droid 에서 설치하세요 — 🔴 Termux 와 **같은 곳**이어야 합니다."
fi

echo
echo "────────────────────────────────────────────"
echo "  🔴 마지막 한 가지는 폰에서 직접 해야 합니다."
echo
echo "      Termux:Boot 앱 아이콘을 **한 번 탭**하세요."
echo "      빈 화면이 잠깐 떴다 닫히는 게 전부입니다."
echo
echo "  안드로이드는 '설치 후 한 번도 실행되지 않은 앱'을 정지 상태로 보고"
echo "  부팅 완료 신호를 **아예 보내지 않습니다.** 신호를 놓친 게 아니라"
echo "  받을 자격이 없는 상태입니다."
echo
echo "  👉 그다음 절전 설정을 하고, 재부팅해서 확인하면 끝입니다."
echo "────────────────────────────────────────────"
echo
