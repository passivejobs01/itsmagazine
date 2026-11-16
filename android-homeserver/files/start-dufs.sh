#!/data/data/com.termux/files/usr/bin/bash
# dufs — 폰 저장소를 웹/WebDAV 로 공개한다.
#
# 아이디·비밀번호는 아래 두 파일에서만 읽는다(명령줄·ps 목록에 안 남는다).
#   ~/.webdav_user
#   ~/.webdav_pass
#
# 실행:  sh ~/start-dufs.sh        (6장에서 runit 이 이 파일을 실행한다)
# 중지:  Ctrl+C  /  sv down dufs

set -u
PORT="${PORT:-8092}"
SHARE="${SHARE:-/storage/emulated/0}"
CFG="$HOME/dufs/dufs.yaml"
DUFS="$HOME/dufs/dufs"

for f in "$HOME/.webdav_user" "$HOME/.webdav_pass"; do
  [ -s "$f" ] || { echo "중단: $f 가 없거나 비어 있습니다. 4-1 을 먼저 하세요."; exit 1; }
done

# 🔑 비밀번호는 설정 파일(600)에만 둔다. 명령줄에 쓰면 `ps` 목록에 그대로 보인다.
#    주소·포트는 비밀이 아니므로 명령줄로 넘긴다(아래에서 바꿔 끼워야 하기 때문).
umask 077
cat > "$CFG" <<YAML
serve-path: $SHARE
auth:
  - $(cat "$HOME/.webdav_user"):$(cat "$HOME/.webdav_pass")@/:rw
allow-upload: true
allow-delete: true
allow-search: true
allow-archive: true
YAML
chmod 600 "$CFG"

# ── 어느 주소로 열 것인가 ──────────────────────────────────────────────────
# 🔑 0.0.0.0(전부) 이 제일 좋다 — 폰 IP 가 바뀌어도 그대로 동작한다.
#
# 🔴 그런데 안드로이드 11 이후로는 그게 안 되는 기기가 있다.
#    dufs 는 0.0.0.0 으로 뜰 때 "어떤 주소들로 열렸는지" 안내하려고
#    인터페이스 목록을 읽는데, 안드로이드가 `/proc/net/*` 접근을 막아
#    시작도 못 하고 죽는다:
#
#        Error: Failed to get local interface addresses
#        Caused by: Permission denied (os error 13)
#
#    (갤럭시 S25 · Android 17 에서 실측. 갤럭시 노트9 · Android 10 은 멀쩡하다)
#
# 👉 그래서 **먼저 0.0.0.0 으로 해보고, 실패하면** 실제 주소를 찾아 직접 지정한다.
#    주소 찾기는 `/proc/net` 이 아니라 **SIOCGIFCONF**(소켓 ioctl)로 한다 — 이건 막혀 있지 않다.

list_addrs() {
    python - <<'PY' 2>/dev/null
import array, fcntl, socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
n = 64
while True:
    size = n * 40
    buf = array.array("B", b"\0" * size)
    out = fcntl.ioctl(s.fileno(), 0x8912, struct.pack("iL", size, buf.buffer_info()[0]))
    used = struct.unpack("iL", out)[0]
    if used < size:
        break
    n *= 2
raw = buf.tobytes()[:used]
seen = []
for i in range(0, used, 40):
    ip = socket.inet_ntoa(raw[i + 20:i + 24])
    # 127.0.0.1 은 폰 자신만 쓰는 주소라 맨 앞에 따로 넣는다.
    if ip not in seen and not ip.startswith("127."):
        seen.append(ip)
print(",".join(["127.0.0.1"] + seen))
PY
}

echo "dufs 시작: $SHARE -> :$PORT (인증 필요, 읽기/쓰기)"

# 1차 — 전부 열기
if "$DUFS" --config "$CFG" --bind 0.0.0.0 --port "$PORT"; then
    exit 0
fi

# 2차 — 실제 주소를 찾아 다시
ADDRS="$(list_addrs)"
if [ -z "$ADDRS" ]; then
    echo "🔴 열 주소를 찾지 못했습니다. python 이 깔려 있는지 확인하세요(1장)."
    exit 1
fi
echo "📌 0.0.0.0 으로 못 떠서 실제 주소로 다시 엽니다: $ADDRS"
echo "   (안드로이드가 인터페이스 목록 조회를 막은 기기입니다 — 동작에는 문제 없습니다)"
exec "$DUFS" --config "$CFG" --bind "$ADDRS" --port "$PORT"
