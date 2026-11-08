#!/usr/bin/env bash
# T33: run the same B4 benchmark in the deployed Python 3.11 image, with bench/ bind-mounted.
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "Run as root on the Vultr instance" >&2
  exit 1
fi

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

# The public service has a 3 GiB limit; stop it so the benchmark has the 4 GiB host to itself.
systemctl stop penumbra
trap 'systemctl start penumbra' EXIT

docker run --rm --network none --memory=3g --cpus=2 --user root \
  --volume "$repo_dir/bench:/app/bench" --workdir /app penumbra \
  python -m bench.b4 --output bench/results/b4_vultr.json \
  --machine-label "Vultr vc2-2c-4gb (Ubuntu 24.04, New York)" \
  --vcpu 2 --ram-gb 4 --limit-ram-gib 3

echo "Wrote $repo_dir/bench/results/b4_vultr.json"
