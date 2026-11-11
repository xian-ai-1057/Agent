#!/bin/sh
# 容器的進入點：以 root 啟動，整理好權限後改用一般使用者 harness 執行。
set -e

if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

# 掛載進來的資料夾可能屬於 root，讓 harness 可以寫入。
chown harness:harness /assistant
export HOME=/home/harness

# 有掛載主機的 Docker socket 時，把 harness 加進擁有它的群組，沙盒才能
# 啟動容器。群組編號每台主機不同，所以在啟動時才查。
if [ -S /var/run/docker.sock ]; then
    docker_gid="$(stat -c %g /var/run/docker.sock)"
    exec setpriv --reuid harness --regid harness --groups "$docker_gid" "$@"
fi
exec setpriv --reuid harness --regid harness --clear-groups "$@"
