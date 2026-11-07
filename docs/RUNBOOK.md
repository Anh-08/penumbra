# Penumbra RUNBOOK — bắt đầu mới từ bây giờ

Một file duy nhất để theo dõi. Đánh dấu `[x]` khi xong, ghi giờ vào cột bên phải. Lệnh và prompt chép nguyên văn.
Vai trò: **B** = Anh-08 (Claude Max + Codex), **A** = Nguyen-Le-Tuan (Claude Pro + Codex). Cả hai dùng Ubuntu.
Đã xong trước khi bắt đầu runbook này: mỗi người có Gemini API key riêng.

---

## Phase 0 — Cả hai, song song (≈ 5 phút)

- [ ] B: giải nén `penumbra-kit.zip` vào `~/Desktop/Penumbra` sao cho `CLAUDE.md` nằm ngay trong thư mục đó (không có thư mục con `penumbra-kit/`). ____
- [ ] A: **không** giải nén kit. A sẽ clone repo ở Phase 3. Tạo thư mục tạm cho việc chuẩn bị máy: `mkdir -p ~/penumbra-prep`. ____
- [ ] Cả hai: tạo file env (ElevenLabs để trống nếu chưa có key): ____

```bash
mkdir -p ~/.config/penumbra && chmod 700 ~/.config/penumbra
cat > ~/.config/penumbra/.env <<'EOF'
GEMINI_API_KEY=dán_key_của_bạn
GEMINI_MODEL=gemini-3.6-flash
ELEVENLABS_API_KEY=
EOF
chmod 600 ~/.config/penumbra/.env
printf '\nset -a; source ~/.config/penumbra/.env; set +a\n' >> ~/.bashrc
```

---

## Phase 1 — Hai prompt chuẩn bị chạy song song (≈ 20–30 phút)

### 1A. B: mở Claude Code tại `~/Desktop/Penumbra`, dán Prompt S1

- [ ] Prompt S1 chạy xong, có checklist. ____
- [ ] Nếu `GEMINI_MODEL` nó đề xuất khác, sửa trong `~/.config/penumbra/.env`. ____

**Prompt S1 (B, trong repo)**

```
You are setting up the workspace for team Penumbra at UB Hacking 2026. Repo root: ~/Desktop/Penumbra (the kit is unzipped here). The hacking period has started.

SCOPE RULE: this session is setup only. Do not write product code: nothing in backend/, frontend/, bench/, tests/, contracts/ or deploy/, no app scaffolding. Product code is written by the agent sessions through approved issues. If a step would need product code, stop and tell me.

1. Repo (local only)
   - Confirm these exist and do not modify them: CLAUDE.md, AGENTS.md (must be identical to CLAUDE.md; verify with diff), CONTRACTS.md, ISSUES.md, .gitignore, scripts/{board.sh,path_guard.py,seed_issues.py}, .github/{CODEOWNERS,workflows/ci.yml}, docs/RUNBOOK.md. Run `chmod +x scripts/*.sh scripts/*.py`.
   - Check `git config user.name` and `user.email` are set; if not, ask me.
   - `git init -b main` if needed. Do NOT add a remote. Do NOT push.
   - Create docs/PRE-EVENT.md: an honest list of what existed before the hacking period (planning docs, contracts draft, workflow tooling) and what this session adds (data files, environment, agent config), plus the sentence "All product code is written during the hacking period." Update it as you go.
   - At the end, commit everything on main with the message: "Setup: planning docs, workflow tooling, data, agent config (no product code)".

2. Tools (ask me before any sudo command)
   - Check: python3 (need >=3.11), node (>=20), npm, git, gh (>=2.30, needs `gh issue pin`), jq, notify-send, claude, codex. Show a found/missing table with install commands for anything missing. For codex, the install command is `npm install -g @openai/codex`, then `codex login`.
   - Run `gh auth status`.

3. Python env (outside the repo)
   - Create a venv at ~/.venvs/penumbra and install: skyfield sgp4 numpy ortools fastapi "uvicorn[standard]" websockets pydantic python-dotenv google-genai httpx pytest.
   - Smoke-test the imports with a single `python -c` line. Do not create files for this.
   - Write the exact installed versions to docs/pinned-versions.txt.

4. Node cache (no project)
   - Run `npm cache add` for cesium, vite, vite-plugin-cesium, react, react-dom, @vitejs/plugin-react, gsap, so installing on venue Wi-Fi is fast.
   - Add their versions to docs/pinned-versions.txt.

5. Data only (CelesTrak policy: one download per URL; never retry on 403)
   - Download exactly once:
     https://celestrak.org/NORAD/elements/gp.php?GROUP=planet&FORMAT=json -> data/omm/fleet.json
     Verify it is a JSON array with at least 24 objects; print the count and the newest EPOCH. If the response is not JSON, stop and show me. Do not retry.
   - Get the ephemeris: ~/.venvs/penumbra/bin/python -c "from skyfield.api import Loader; Loader('data/ephem')('de421.bsp')"
   - Write data/stations.json with 3 stations, coordinates labeled approximate. Fields: station_id (uppercase, no digits), name, lat_deg, lon_deg, alt_m.
     - SVALBARD: ~78.23 N, 15.40 E, ~450 m
     - TROLL (Antarctica): ~72.01 S, 2.54 E, ~1275 m
     - FAIRBANKS (Alaska): ~64.86 N, 147.85 W, ~200 m
   - Write data/README.md: source URLs, fetch time (UTC), and a note that the app replaces real satellite names with SAT-01..SAT-24.

6. Secrets (never print, log or commit them)
   - ~/.config/penumbra/.env exists with GEMINI_API_KEY and GEMINI_MODEL; ELEVENLABS_API_KEY may be empty for now (skip its test if empty).
   - Smoke-test the Gemini key with one curl call and print only the HTTP status. Look up the current endpoint in the official docs; do not guess it.
   - List the available models for this key and confirm or correct GEMINI_MODEL: prefer a Flash-Lite model if it supports function calling (free-tier daily quota is much higher), otherwise a pinned Flash version. Do not edit my .env; tell me what to set.

7. Agent permissions (config, not product code)
   - Create .claude/settings.json in Claude Code's documented format. It is committed and shared by two machines with different home paths, so use NO absolute paths: match commands by name (python, pytest, npm, git, gh, jq, scripts/board.sh, scripts/path_guard.py). Allow git except push --force; deny sudo, rm -rf, git push --force, and reading any .env file. Show me the file before saving it.
   - Look up the Codex CLI docs and print the settings for ~/.codex/config.toml and ~/.codex/rules so Codex can run tests and git without prompting but no destructive commands. Use my real home path in what you print. Do not edit those files yourself.

8. Skills (user level, outside the repo)
   - Read the README of https://github.com/nextlevelbuilder/ui-ux-pro-max-skill and https://github.com/pbakaus/impeccable and install each the way its README says, under ~/.claude/skills/ or wherever the README requires. Do not copy them into the repo. Report which skills Claude Code now sees.
   - Check whether the connected Figma MCP server can create frames/nodes or only read; report the tool names. Create nothing in Figma.

9. Report: a final checklist (done / failed / needs me), under 25 lines.
```

### 1B. A: mở Claude Code tại `~/penumbra-prep`, dán Prompt S2

- [ ] Prompt S2 chạy xong. ____
- [ ] Dán config Codex mà nó in ra vào `~/.codex/config.toml` và `~/.codex/rules/default.rules`. ____

**Prompt S2 (A, chuẩn bị máy, không có repo)**

```
You are preparing machine A for team Penumbra at UB Hacking 2026. This folder (~/penumbra-prep) is scratch; the project repo will be cloned later. Write no project files here.

1. Tools (ask me before any sudo command): check python3 (>=3.11), node (>=20), npm, git, gh (>=2.30), jq, notify-send, claude, codex. Show a found/missing table with install commands. For codex: `npm install -g @openai/codex`, then `codex login`. Run `gh auth status`.
2. Python env: create ~/.venvs/penumbra and install: skyfield sgp4 numpy ortools fastapi "uvicorn[standard]" websockets pydantic python-dotenv google-genai httpx pytest. Smoke-test the imports with one `python -c` line. Print the installed versions (B keeps the canonical list in the repo).
3. Node cache: `npm cache add` for cesium, vite, vite-plugin-cesium, react, react-dom, @vitejs/plugin-react, gsap.
4. Secrets (never print or log them): ~/.config/penumbra/.env exists with GEMINI_API_KEY and GEMINI_MODEL. Smoke-test the Gemini key with one curl call (HTTP status only; look up the endpoint in the official docs). List the models for this key and tell me if GEMINI_MODEL should change.
5. Codex: look up the Codex CLI docs and print the settings for ~/.codex/config.toml and ~/.codex/rules so Codex can run tests and git without prompting but no destructive commands (deny sudo, rm -rf, git push --force, git reset --hard). Use my real home path. Do not edit those files yourself.
6. Skills (user level): install https://github.com/nextlevelbuilder/ui-ux-pro-max-skill and https://github.com/pbakaus/impeccable the way their READMEs say, under ~/.claude/skills/ or wherever required. Report which skills Claude Code now sees.
7. Report: checklist (done / failed / needs me), under 20 lines.
```

---

## Phase 2 — B: tạo repo và giao việc (≈ 10 phút, sau khi S1 xong)

- [ ] Tạo repo public và push: ____

```bash
cd ~/Desktop/Penumbra
gh repo create Anh-08/penumbra --public --source=. --push
```

- [ ] Mời A: trên GitHub, repo → Settings → Collaborators → Add people → `Nguyen-Le-Tuan`. ____
- [ ] Branch protection cho `main`: Settings → Rules → Rulesets → New branch ruleset, target `main`: ____
  - Require a pull request before merging: required approvals **0**, bật **Require review from Code Owners**.
  - Require status checks to pass: thêm theo tên `path-guard`, `backend`, `frontend`.
  - Bật **Block force pushes**. Cho phép admin bypass để xử lý khẩn cấp.
- [ ] Seed labels, bảng tin và issues (chỉ B, chỉ một lần): ____

```bash
python3 scripts/seed_issues.py --dry-run     # hai người liếc qua
python3 scripts/seed_issues.py --approve
```

- [ ] Ghi số `BOARD_ISSUE` in ở dòng cuối vào `~/.bashrc` và nhắn cho A: ____

```bash
printf '\nexport BOARD_ISSUE=<số>\n' >> ~/.bashrc && source ~/.bashrc
```

---

## Phase 3 — A: nhận repo (≈ 5 phút, sau khi B push và mời)

- [ ] Chấp nhận lời mời collaborator (email hoặc github.com/notifications). ____
- [ ] Clone và kiểm tra dữ liệu: ____

```bash
git clone https://github.com/Anh-08/penumbra.git ~/Desktop/Penumbra
cd ~/Desktop/Penumbra
ls data/omm/fleet.json data/stations.json data/ephem/de421.bsp
printf '\nexport BOARD_ISSUE=<số B đưa>\n' >> ~/.bashrc && source ~/.bashrc
```

- [ ] H0-DATA (A, data only, không đợi ai): tải một bộ dữ liệu lão hóa pin Li-ion của NASA PCoE (kho NASA Prognostics hoặc bản mirror trên Kaggle) vào `data/pcoe/`, thêm mục vào `data/README.md` (nguồn, giấy phép, giờ tải), commit trên branch `human/data` và mở PR. Nếu còn thời gian, tải thêm BIRDS vào `data/birds/`. ____

---

## Phase 4 — Khởi động 4 phiên agent (cả hai)

Trước khi mở phiên, trong mỗi terminal:

```bash
cd ~/Desktop/Penumbra && source ~/.venvs/penumbra/bin/activate && source ~/.bashrc
```

| | Terminal 1 | Terminal 2 | Terminal 3 |
|---|---|---|---|
| **B** | Claude Code: `You are B1. Read CLAUDE.md, then start the task loop.` | Codex: `You are B2. Read AGENTS.md, then start the task loop.` | `scripts/board.sh B1` |
| **A** | Claude Code: `You are A1. Read CLAUDE.md, then start the task loop.` | Codex: `You are A2. Read AGENTS.md, then start the task loop.` | `scripts/board.sh A1` |

- [ ] B1 đang làm T01 (T01 cũng tạo `docs/HANDOFF.md` và `docs/STATUS.md`). ____
- [ ] B2, A1, A2 đã claim issue đầu tiên (T02, T06, T09). ____
- [ ] Cả bốn phiên đều thấy `BOARD_ISSUE` (chạy `scripts/board.sh <mã> --once` một lần thử). ____

---

## Phase 5 — B: phiên design, 25 phút, chạy song song với B1

Mở thêm một phiên Claude Code tại `~/Desktop/Penumbra`, dán Prompt D. Nó chỉ viết vào `docs/design/`. A2 dùng kết quả từ T15 trở đi; T09 không đợi.

- [ ] Prompt D xong, commit `docs/design/` qua PR trên branch `human/design`. ____

**Prompt D (design)**

```
Use the ui-ux-pro-max and impeccable skills. You are the design session for Penumbra. Read CLAUDE.md, CONTRACTS.md (sections Demo director, Fleet, API/WebSocket) and ISSUES.md T09, T15, T18, T21, T35 first. Output goes only to docs/design/ and, if possible, Figma. Do not touch frontend/ or any product code. Time box: 25 minutes.

Goal: a mission-control UI that makes a judge stop at our table within 15 seconds and understand "AI proposes, physics verifies, humans approve" within 3 minutes.

Deliver:
1. docs/design/DESIGN.md: visual direction (dark mission-control, one accent color for alerts, one for verified/green, one for refused/red), typography, spacing scale, CSS tokens as :root variables, the permanent "Real / Simulated" legend, and the rule that the Cesium globe is the hero and HUD panels never cover more than 40% of it.
2. docs/design/LAYOUT.md: one 1920×1080 layout for the expo screen: globe left/center, right column = triage queue + hypothesis bars, bottom strip = tool-call trace, overlay panel = plan × hypothesis matrix with the SOC chart, and the Flight Director approve bar. Exact px widths and z-order. A second variant for 1366×768.
3. docs/design/MOTION.md: the GSAP motion spec per demo state (attract, storm, agent, approval, fastforward, reveal): what animates, duration, easing, and the one thing that must never animate while the judge reads. Matrix cells light up one by one; a refused approval shakes once and shows the failing hypotheses; reveal draws the true SOC line over the band.
4. docs/design/STORYBOARD.md: 8 frames of the 3-minute demo with the screen state and the spoken line for each.
5. Figma: if the Figma MCP can create frames, build the 1920×1080 layout as frames (globe as a placeholder image) in the connected team folder and put the file link in DESIGN.md. If the MCP is read-only, build the same layout as a static HTML mock in docs/design/mock/index.html (plain HTML and CSS, no app logic, no Cesium) and tell me to import it with the html.to.design plugin.

Constraints: readable from 2 m away at an expo table; no chat bubbles anywhere; numbers only come from the backend; implementable by one Codex session in 4 hours.
Finish with a 10-line summary for A2, the frontend session.
```

---

## Phase 6 — Việc nền, làm khi agent đang chạy

- [ ] B: đăng ký Vultr qua mlh.link/vultr-signup, nhận credit, thêm SSH key. Chưa tạo instance (tạo lúc T32, khoảng 3:30 AM). ____
- [ ] Ai có tài khoản ElevenLabs: tạo key, điền vào `.env` của cả hai máy trước 4 AM (T28). ____
- [ ] Hỏi trên Discord của Radial: nộp qua Devpost có đủ không, hay cần form riêng. ____
- [ ] Devpost: tạo project, chọn tất cả track phù hợp theo thứ tự: Best General, Radial, Best AI/ML, Spirit, MLH Gemini, Best UI/UX, MLH Vultr, MLH ElevenLabs, Most Over-Engineered, MLH GoDaddy. ____
- [ ] B, 11 PM: bản nháp Devpost (H2) với 4 mục bắt buộc trong ISSUES.md. ____

---

## Mốc chết

| Giờ | Mốc | Nếu trượt |
|---|---|---|
| Sat 18:00 | Orbit: eclipse + contacts cho 24 vệ tinh (T02–T04) | Dùng contact tính sẵn, cố định |
| Sat 22:00 | Truth sim + verifier cho 1 vệ tinh (T07–T12) | Bỏ Monte Carlo, chạy deterministic × hypotheses |
| Sun 02:00 | Vòng lặp 1 vệ tinh end-to-end, có Gemini (T22) | Bỏ fleet scheduler và peer comparison, demo 1 vệ tinh |
| Sun 06:00 | **Feature freeze**, MORNING DIGEST | Chỉ nhận PR `fix` |
| Sun 09:00 | Khóa số B1–B5, quay video fallback | Dùng số đã có, ghi rõ n |
| Sun 12:00 | Nộp Devpost (hạn cứng 12:30) | |

Ngủ: A 2:00–4:00, B 4:00–6:00. Trong lúc B ngủ, B1 chỉ tự merge PR xanh không chạm contract hay `.github/`; A duyệt những gì cần người.

---

## Nhật ký (ghi tay)

| Giờ | Ai | Việc / sự cố | Quyết định |
|---|---|---|---|
| | | | |
