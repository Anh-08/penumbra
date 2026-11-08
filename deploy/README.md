# Public replay demo on one Vultr instance (T32)

One container serves the API, the WebSocket and the built frontend on port 80. It runs **replay mode
only**: the Fault storm button plays `runs/demo/live-fault-storm.jsonl` (paced, ~90 s). No Gemini or
ElevenLabs key ever goes to the server; the "run live" toggle falls back to the replay with a callout.
The firewall allows only SSH (key-only) and web (80/443). The local laptop demo stays the primary demo.

Files: `Dockerfile` (frontend build with Node 20, runtime Python 3.11 with the pinned
`backend/requirements.txt`), `Dockerfile.dockerignore`, `penumbra.service` (systemd), `setup.sh`
(everything below in one idempotent script).

## Human steps (about 15 minutes, most of it the image build)

1. **SSH key in Vultr.** In the Vultr portal: Account → SSH Keys → Add SSH Key, paste your public key
   (`cat ~/.ssh/id_ed25519.pub`).
2. **Create the instance.** Products → Deploy → Cloud Compute (shared CPU), **Ubuntu 24.04 LTS x64**,
   at least **2 vCPU / 4 GB RAM** (the storm replay is CPU-bound), any nearby region. Tick your SSH key
   under "SSH Keys". Deploy and note the IPv4 address.
3. **Log in:** `ssh root@<IP>` (key login; no password needed).
4. **Get the code** on the instance (public repo):
   ```bash
   git clone https://github.com/Anh-08/penumbra.git && cd penumbra
   ```
   (Or from your laptop: `git archive --format=tar.gz -o /tmp/penumbra.tgz HEAD && scp /tmp/penumbra.tgz root@<IP>:`
   then on the instance `mkdir penumbra && tar -xzf penumbra.tgz -C penumbra && cd penumbra`.)
   Do **not** copy `~/.config/penumbra/.env` or any key.
5. **Run the script:**
   ```bash
   sudo bash deploy/setup.sh
   ```
   It installs docker, docker-buildx and ufw, makes SSH key-only (only if an authorized key is present, so it
   cannot lock you out), sets the firewall to SSH + 80 + 443, builds the image, installs and starts
   `penumbra.service`, and ends with `OK: Penumbra is up at http://<IP>/`. It refuses to run if
   `GEMINI_API_KEY` or `ELEVENLABS_API_KEY` is set in the shell.
6. **Verify:**
   - Open `http://<IP>/`: the globe loads with the Real / Simulated legend.
   - Click **Fault storm**: SAT-12 auto-executes (FR-2), SAT-07 waits for **Approve**, SAT-19 ends
     infeasible / SAFE_MODE; then fast-forward and reveal.
   - `curl -s http://<IP>/api/bench | head -c 200` returns the benchmark results.
   - On the instance: `ufw status` lists only OpenSSH, 80/tcp and 443/tcp; `ssh -o PubkeyAuthentication=no root@<IP>`
     from your laptop is refused.
7. **Post the URL** on the AGENT BOARD and in Devpost.

## Operating it

- Logs: `journalctl -u penumbra -f`. Restart: `systemctl restart penumbra`. Stop: `systemctl stop penumbra`.
- Update after a merge: `cd penumbra && git pull && sudo bash deploy/setup.sh` (rebuilds and restarts).
- One shared demo for every visitor: a second Fault storm click while a storm runs gets HTTP 409.
- Port 443 is open for a later TLS front end; the container itself serves plain HTTP on 80.
- **Destroy the instance after judging** (Vultr portal → the instance → Destroy).

## Tested (2026-11-08, B1)

- **Clean venv, same steps as the image:** a fresh Python 3.11 venv with the pinned `backend/requirements.txt`, the
  frontend built with `npm ci && npm run build`, then uvicorn started with exactly the container's environment
  (`PENUMBRA_REPLAY=runs/demo/live-fault-storm.jsonl`, `PENUMBRA_STATIC_DIR=frontend/dist`, no keys).
- **Driven over real HTTP and WebSocket like a judge:** index page 200, `/api/bench` (6 results files), Fault storm
  200 (a second click 409), SAT-12 auto-executed (FR-2), SAT-07 approved and executed, reveal 200
  (`stuck_on_load`); demo states storm, agent, storm, agent, approval, storm, agent, fastforward, reveal.
  The run took 74 s at pace 0 on a 22-core laptop; expect ~90 s paced, plus more on a small instance.
- **"Run live" without keys:** it falls back to the replay with a callout. The server log contains no key material.
- **Not run here:** `docker build` (this laptop's user has no access to the docker daemon) and `setup.sh` itself
  (it needs root, apt and ufw). Run `setup.sh` on the instance; if the image build fails, the log names the step.
