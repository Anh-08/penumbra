#!/usr/bin/env bash
# Penumbra public replay demo on one fresh Ubuntu 24.04 instance (T32). Run as root from the repo root:
#   sudo bash deploy/setup.sh
# Idempotent: safe to run again after a git pull. Installs docker and ufw, builds the image, installs the
# systemd unit, locks the firewall to SSH + web (80/443) and makes SSH key-only. Never takes a key from the
# shell. Live mode (optional, T42): put GEMINI_API_KEY, GEMINI_MODEL and PENUMBRA_OFFLINE=0 in
# /etc/penumbra/live.env (root, mode 600) and re-run; the unit passes those three names to the container.
set -euo pipefail

if [ "$(id -u)" != "0" ]; then echo "run as root (sudo bash deploy/setup.sh)" >&2; exit 1; fi
cd "$(dirname "$0")/.."
test -f deploy/Dockerfile || { echo "run from the penumbra repo" >&2; exit 1; }

# Refuse to run if a key is lying around in the shell environment: keys reach the service only through the
# root-only env file below, never through a shell or the repo.
for k in GEMINI_API_KEY ELEVENLABS_API_KEY; do
  if [ -n "${!k:-}" ]; then echo "$k is set in this shell; unset it, keys go in /etc/penumbra/live.env only" >&2; exit 1; fi
done

LIVE_ENV=/etc/penumbra/live.env
if [ -f "$LIVE_ENV" ]; then
  chown root:root "$LIVE_ENV"; chmod 600 "$LIVE_ENV"
  if grep -vE '^(#|$|GEMINI_API_KEY=|GEMINI_MODEL=|PENUMBRA_OFFLINE=)' "$LIVE_ENV" >/dev/null; then
    echo "$LIVE_ENV may only set GEMINI_API_KEY, GEMINI_MODEL and PENUMBRA_OFFLINE" >&2; exit 1
  fi
  grep -qE '^GEMINI_API_KEY=.+' "$LIVE_ENV" || { echo "$LIVE_ENV has no GEMINI_API_KEY" >&2; exit 1; }
  grep -qE '^GEMINI_MODEL=.+' "$LIVE_ENV" || { echo "$LIVE_ENV has no GEMINI_MODEL" >&2; exit 1; }
  grep -qE '^PENUMBRA_OFFLINE=0$' "$LIVE_ENV" || { echo "$LIVE_ENV must set PENUMBRA_OFFLINE=0 for live mode" >&2; exit 1; }
  echo "== live mode: ON (model key from $LIVE_ENV; values are never printed)"
else
  echo "== live mode: off (replay only; create $LIVE_ENV to enable)"
fi

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-buildx ufw curl

echo "== SSH: key-only (only if a key is installed, so we never lock you out)"
if [ -s /root/.ssh/authorized_keys ] || ls /home/*/.ssh/authorized_keys >/dev/null 2>&1; then
  install -d -m 755 /etc/ssh/sshd_config.d
  cat > /etc/ssh/sshd_config.d/10-penumbra.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
CONF
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
else
  echo "!! no authorized_keys found: SSH password login left as is; add your key and re-run" >&2
fi

echo "== firewall: SSH + web (80/443) only"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "== image"
docker build -f deploy/Dockerfile -t penumbra .

echo "== service"
install -m 644 deploy/penumbra.service /etc/systemd/system/penumbra.service
systemctl daemon-reload
systemctl enable --now penumbra
systemctl restart penumbra

echo "== waiting for the API"
for i in $(seq 1 60); do
  if curl -fsS -o /dev/null http://127.0.0.1/; then
    ip=$(curl -fsS -4 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo "OK: Penumbra is up at http://$ip/"; exit 0
  fi
  sleep 2
done
echo "the service did not answer on port 80; see: journalctl -u penumbra -n 100" >&2; exit 1
