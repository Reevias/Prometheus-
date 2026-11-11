#!/usr/bin/env bash
# Prometheus cloud copy: one-command setup on a fresh Ubuntu machine (Oracle Cloud Always Free, Ampere A1).
# Run it on the cloud machine:   bash cloud-setup.sh
# It asks for: a GitHub token that can read your Prometheus repository, and a Prometheus password for this copy.
set -euo pipefail

REPO="${PROMETHEUS_REPO:-Reevias/Prometheus-}"
DIR="$HOME/prometheus"

echo "== 1/6 System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq git python3 python3-venv python3-pip curl adb >/dev/null   # adb: phone control

echo "== 2/6 Prometheus's code"
if [ ! -d "$DIR/.git" ]; then
  read -r -s -p "GitHub token with read access to $REPO (fine-grained, Contents: read): " GH; echo
  git clone -q "https://x-access-token:${GH}@github.com/${REPO}.git" "$DIR"
  git -C "$DIR" remote set-url origin "https://x-access-token:${GH}@github.com/${REPO}.git"
  unset GH
else
  git -C "$DIR" pull -q --ff-only
fi

echo "== 3/6 Python packages (a few minutes)"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -e "$DIR[photos]" || "$DIR/.venv/bin/pip" install -q -e "$DIR"
"$DIR/.venv/bin/pip" install -q faster-whisper || true      # lecture notes from recordings sent by the phone

echo "== 4/6 Password and cloud mode"
if ! "$DIR/.venv/bin/python" -c "from prometheus.config import load_settings; import sys; sys.exit(0 if load_settings().password_hash else 1)"; then
  "$DIR/.venv/bin/python" -m prometheus.cli init
fi
"$DIR/.venv/bin/python" -m prometheus.cli cloud-setup

echo "== 5/6 Start with the machine (systemd)"
sudo tee /etc/systemd/system/prometheus.service >/dev/null <<UNIT
[Unit]
Description=Prometheus (cloud copy)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -m prometheus.cli background
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now prometheus

echo "== 6/6 Private link (Tailscale): nothing is opened to the internet"
if ! command -v tailscale >/dev/null; then curl -fsSL https://tailscale.com/install.sh | sh; fi
sudo tailscale up --hostname prometheus-cloud
sudo tailscale serve --bg 8000
echo
echo "Done. Your cloud copy's private address:"
tailscale serve status | grep -o 'https://[^ ]*' | head -1 || true
echo "Put that address and the sync key above into your laptop: Settings → Cloud copy."
