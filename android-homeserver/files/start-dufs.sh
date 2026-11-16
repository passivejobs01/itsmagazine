#!/data/data/com.termux/files/usr/bin/bash
# dufs — 폰 저장소를 웹/WebDAV 로 공개한다.
#
# 아이디·비밀번호는 아래 두 파일에서만 읽는다(명령줄·ps 목록에 안 남는다).
#   ~/.webdav_user
#   ~/.webdav_pass
#
# 실행:  nohup ~/start-dufs.sh > ~/dufs.log 2>&1 &
# 중지:  pkill -f 'dufs --config'

set -u
PORT="${PORT:-8092}"
SHARE="${SHARE:-/storage/emulated/0}"
CFG="$HOME/dufs/dufs.yaml"

for f in "$HOME/.webdav_user" "$HOME/.webdav_pass"; do
  [ -s "$f" ] || { echo "중단: $f 가 없거나 비어 있습니다. 가이드 4-2 를 먼저 하세요."; exit 1; }
done

umask 077
cat > "$CFG" <<YAML
serve-path: $SHARE
bind: 0.0.0.0
port: $PORT
auth:
  - $(cat "$HOME/.webdav_user"):$(cat "$HOME/.webdav_pass")@/:rw
allow-upload: true
allow-delete: true
allow-search: true
allow-archive: true
YAML
chmod 600 "$CFG"

echo "dufs 시작: $SHARE -> 0.0.0.0:$PORT (인증 필요, 읽기/쓰기)"
exec "$HOME/dufs/dufs" --config "$CFG"
