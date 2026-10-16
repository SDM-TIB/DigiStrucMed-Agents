#!/bin/bash
# Startup script of the QLever graph server (runs as root on every boot, idempotent).
#   1. mount the data disk (formatted once)
#   2. docker and the QLever CLI (pinned version)
#   3. the graph version named in the instance metadata: download, check its sha256, build the index once
#   4. start the SPARQL server and the QLever UI (containers restart on their own after a reboot)
# memory figures follow the machine's RAM, so the same script serves a large build VM and a small serving VM.
set -euo pipefail
exec > >(tee -a /var/log/qlever-startup.log | logger -t qlever-startup) 2>&1
echo "startup $(date -Is)"

meta() { curl -sf -H 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }

DATA=/srv/qlever
DEV=/dev/disk/by-id/google-qlever-data

# 1. data disk
if ! blkid "$DEV" >/dev/null 2>&1; then mkfs.ext4 -m 0 -F "$DEV"; fi
mkdir -p "$DATA"
mountpoint -q "$DATA" || mount -o discard,defaults "$DEV" "$DATA"
grep -q "$DEV" /etc/fstab || echo "$DEV $DATA ext4 discard,defaults,nofail 0 2" >> /etc/fstab

# 2. software
if ! command -v docker >/dev/null; then
  apt-get update -q
  apt-get install -y -q docker.io python3-venv
fi
systemctl enable --now docker
CLI_VERSION="$(meta qlever-cli)"
if [ "$(/opt/qlever-cli/bin/pip show qlever 2>/dev/null | awk '/^Version/{print $2}')" != "$CLI_VERSION" ]; then
  python3 -m venv /opt/qlever-cli
  /opt/qlever-cli/bin/pip install -q "qlever==$CLI_VERSION"
fi
QLEVER=/opt/qlever-cli/bin/qlever
IMAGE="$(meta qlever-image)"
UI_IMAGE="$(meta qlever-ui-image)"
[ -s "$DATA/access-token" ] || (umask 077; head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' > "$DATA/access-token")

# 3. graph version and index
SHA="$(meta graph-sha256)"
URI="$(meta graph-uri)"
DIR="$DATA/graphs/$SHA"
mkdir -p "$DIR"
cd "$DIR"
meta qleverfile > Qleverfile
RAM_MB="$(awk '/MemTotal/ {print int($2 / 1024)}' /proc/meminfo)"
if [ ! -f INDEX_COMPLETE ]; then
  if [ ! -f graph.nq.gz ] || [ "$(sha256sum graph.nq.gz | cut -d' ' -f1)" != "$SHA" ]; then
    gcloud storage cp "$URI" graph.nq.gz
    GOT="$(sha256sum graph.nq.gz | cut -d' ' -f1)"
    if [ "$GOT" != "$SHA" ]; then echo "graph sha256 $GOT, expected $SHA: not indexed"; exit 1; fi
  fi
  "$QLEVER" index --image "$IMAGE" --stxxl-memory "$((RAM_MB * 45 / 100))M" --overwrite-existing
  printf '{"graph_sha256": "%s", "graph_uri": "%s", "image": "%s", "built": "%s", "ram_mb": %s}\n' \
    "$SHA" "$URI" "$IMAGE" "$(date -Is)" "$RAM_MB" > INDEX_COMPLETE
  rm -f graph.nq.gz  # the index holds everything; the file stays in the bucket
fi
ln -sfn "$DIR" "$DATA/current"

# 4. server and UI. the server of an older graph version on the same port is replaced
"$QLEVER" start --image "$IMAGE" --kill-existing-with-same-port \
  --memory-for-queries "$((RAM_MB * 35 / 100))M" --cache-max-size "$((RAM_MB * 15 / 100))M" \
  --access-token "$(cat "$DATA/access-token")"
"$QLEVER" ui --ui-image "$UI_IMAGE"
echo "ready $(date -Is): graph $SHA"
