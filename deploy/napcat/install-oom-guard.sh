#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -m 0755 "$script_dir/napcat-oom-guard.py" /usr/local/sbin/napcat-oom-guard.py
install -m 0644 "$script_dir/napcat-oom-guard.service" /etc/systemd/system/napcat-oom-guard.service
install -m 0644 "$script_dir/napcat-oom-guard.timer" /etc/systemd/system/napcat-oom-guard.timer
systemctl daemon-reload
systemctl enable --now napcat-oom-guard.timer
systemctl start napcat-oom-guard.service
