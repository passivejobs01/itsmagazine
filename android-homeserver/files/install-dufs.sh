#!/data/data/com.termux/files/usr/bin/sh
# 파일 서버(dufs) — 설치부터 첫 실행까지 한 번에.
#
#   실행:  sh ~/install-dufs.sh
#
# 하는 일 네 가지
#   ① 폰에 맞는 dufs 를 받아 ~/dufs/dufs 에 설치
#   ② 아이디·비밀번호를 물어서 ~/.webdav_user · ~/.webdav_pass 에 저장(600)
#   ③ 설치가 제대로 됐는지 확인
#   ④ dufs 를 띄웁니다 — 🔑 여기서 멈춰 있는 게 정상입니다.
#      브라우저로 확인한 뒤 Ctrl+C 로 멈추세요(7장에서 자동 실행으로 등록합니다).
#
# ✅ 여러 번 실행해도 안전합니다. 이미 끝난 단계는 건너뜁니다.
#
# 📌 버전·포트를 바꾸려면:  DUFS_VERSION=0.48.0 PORT=9000 sh ~/install-dufs.sh

set -u

VERSION="${DUFS_VERSION:-0.47.0}"
PORT="${PORT:-8092}"
DEST="$HOME/dufs"
START="$HOME/start-dufs.sh"

started="$(date +%s)"
elapsed() {   # 시작부터 지금까지 "1분 23초" 형태로
    s=$(( $(date +%s) - started ))
    [ "$s" -ge 60 ] && printf '%d분 %d초' "$((s / 60))" "$((s % 60))" || printf '%d초' "$s"
}

echo
echo "파일 서버(dufs) 설치"
echo "────────────────────────────────────────────"

# ── ① 설치 ────────────────────────────────────────────────────────────────
# 🔑 폰마다 CPU 가 달라서 받아야 할 파일이 다릅니다. 직접 고르지 않고 알아서 맞춥니다.
arch="$(uname -m)"
case "$arch" in
    aarch64 | arm64) target="aarch64-unknown-linux-musl" ;;
    armv8l | armv7l) target="armv7-unknown-linux-musleabihf" ;;
    armv6l | arm)    target="arm-unknown-linux-musleabihf" ;;
    x86_64 | amd64)  target="x86_64-unknown-linux-musl" ;;
    *)
        echo "  ❌ 어떤 파일을 받아야 할지 모르겠습니다 (CPU: $arch)"
        echo "     https://github.com/sigoden/dufs/releases 에서 직접 받아 ~/dufs/dufs 로 두세요."
        exit 1
        ;;
esac

if [ -x "$DEST/dufs" ] && "$DEST/dufs" --version >/dev/null 2>&1; then
    echo "  ✅ 이미 설치됨 — $("$DEST/dufs" --version 2>/dev/null | head -1)"
else
    echo "  · CPU $arch → $target"
    tmp="$HOME/.dufs-dl"
    rm -rf "$tmp"
    mkdir -p "$tmp" || exit 1

    tgz="dufs-v$VERSION-$target.tar.gz"
    url="https://github.com/sigoden/dufs/releases/download/v$VERSION/$tgz"

    echo "  · 내려받는 중 (v$VERSION · 약 2MB)"
    # 🔑 --progress-bar: 진행률 막대를 보여줍니다.
    #    조용히 받으면 느린 회선에서 "멈춘 건가?" 싶어 중간에 끄게 됩니다.
    #    -L 은 깃허브가 실제 파일 주소로 넘길 때 따라가기 위한 것입니다.
    if ! curl -fL --progress-bar --retry 2 --connect-timeout 10 -o "$tmp/$tgz" "$url"; then
        echo "  ❌ 내려받기 실패"
        echo "     버전이 맞는지 확인하세요: https://github.com/sigoden/dufs/releases"
        rm -rf "$tmp"
        exit 1
    fi

    if ! tar xzf "$tmp/$tgz" -C "$tmp" 2>/dev/null; then
        echo "  ❌ 압축을 풀지 못했습니다 (받다가 끊겼을 수 있습니다)"
        rm -rf "$tmp"
        exit 1
    fi

    mkdir -p "$DEST"
    mv "$tmp/dufs" "$DEST/dufs" || { rm -rf "$tmp"; exit 1; }
    chmod +x "$DEST/dufs"
    rm -rf "$tmp"          # 받은 압축파일은 남겨두지 않습니다

    if ! "$DEST/dufs" --version >/dev/null 2>&1; then
        echo "  ❌ 설치는 됐는데 실행이 안 됩니다 (CPU 가 안 맞는 파일일 수 있습니다)"
        exit 1
    fi
    echo "  ✅ 설치 완료 — $("$DEST/dufs" --version 2>/dev/null | head -1) ($(elapsed))"
fi

# ── ② 아이디·비밀번호 ─────────────────────────────────────────────────────
# 🔴 인증 없이 열면 폰 저장소 전체(사진 포함)가 공개됩니다. 그래서 건너뛸 수 없습니다.
echo
if [ -s "$HOME/.webdav_user" ] && [ -s "$HOME/.webdav_pass" ]; then
    echo "  ✅ 아이디·비밀번호 있음 (바꾸려면 ~/.webdav_user · ~/.webdav_pass 를 지우고 다시 실행)"
else
    if [ ! -t 0 ]; then
        echo "  ❌ 아이디·비밀번호를 물어볼 수 없습니다(입력이 터미널이 아닙니다)."
        echo "     Termux 창이나 SSH 에서 직접 실행해 주세요."
        exit 1
    fi

    echo "  로그인 정보를 정합니다. 🔑 화면에만 입력하고 파일로 바로 저장하므로"
    echo "  명령 기록(~/.bash_history)에 남지 않습니다."
    echo

    user=""
    while [ -z "$user" ]; do
        printf '  아이디: '
        IFS= read -r user
        [ -z "$user" ] && echo "  ⚠️ 비워둘 수 없습니다."
    done

    pass=""
    while [ -z "$pass" ]; do
        printf '  비밀번호: '
        stty -echo 2>/dev/null
        IFS= read -r pass
        stty echo 2>/dev/null
        printf '\n'
        if [ -z "$pass" ]; then
            echo "  ⚠️ 비워둘 수 없습니다."
            continue
        fi
        printf '  한 번 더: '
        stty -echo 2>/dev/null
        IFS= read -r pass2
        stty echo 2>/dev/null
        printf '\n'
        if [ "$pass" != "$pass2" ]; then
            echo "  ⚠️ 두 번 입력한 값이 다릅니다. 다시 하겠습니다."
            pass=""
        fi
    done

    umask 077
    printf '%s' "$user" > "$HOME/.webdav_user"
    printf '%s' "$pass" > "$HOME/.webdav_pass"
    chmod 600 "$HOME/.webdav_user" "$HOME/.webdav_pass"
    unset pass pass2
    echo "  ✅ 저장했습니다 (주인만 읽을 수 있게 권한을 걸었습니다)"
fi

# ── ③ 실행 스크립트 확인 ──────────────────────────────────────────────────
if [ ! -f "$START" ]; then
    echo
    echo "  ❌ ~/start-dufs.sh 가 없습니다."
    echo "     「스크립트 내려받기」를 먼저 하세요:  sh ~/get-files.sh"
    exit 1
fi

# ── ④ 첫 실행 ─────────────────────────────────────────────────────────────
echo
echo "────────────────────────────────────────────"
echo "  이제 띄웁니다. 브라우저로 확인해 보세요:"
echo
echo "      http://<폰주소>:$PORT/"
echo
echo "  로그인되면 성공입니다."
echo "  🔑 확인했으면 Ctrl+C 로 멈추세요 — 7장에서 자동 실행으로 등록합니다."
echo "────────────────────────────────────────────"
echo

PORT="$PORT" exec sh "$START"
