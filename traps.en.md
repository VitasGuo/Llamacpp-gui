# Pitfall Log (Traps)

> Lessons learned the hard way: symptom → root cause → fix → takeaway. Each entry is cross-referenced from `process.md` by number.

---

## #1 A QPixmap drawn at runtime cannot be passed directly to QIcon APIs

**Symptom**: Starting the GUI raises `TypeError: setWindowIcon(self, icon: QIcon): argument 1 has unexpected type 'QPixmap'`.

**Root cause**: `_create_app_icon()` in `ui/app.py` paints with QPainter and returns a `QPixmap`, while `QSystemTrayIcon.setIcon` / `QWidget.setWindowIcon` both require a `QIcon`.

**Fix**: End `_create_app_icon()` with `return QIcon(pixmap)` and add `QIcon` to the QtGui imports (see `ui/app.py`).

**Lesson**: In PyQt6, paint artifacts (QPixmap) and icon objects (QIcon) are not interchangeable; every `*Icon*` API argument must be wrapped in `QIcon`.

**Status**: Fixed in v1.1.0.

---

## #2 QProgressBar's built-in percent text renders as mojibake

**Symptom**: The percent numbers inside the progress bars (CPU/RAM/GPU util/VRAM/temperature) on the monitor page render as garbage, while the QLabel numbers and Chinese labels next to the bars look fine.

**Root cause**: `ui/monitor_tab.py` uses `QProgressBar` whose built-in value text (default format `%p%`) renders abnormally in the middle of the bar under this environment (font/rendering path). The percentage was already shown by adjacent `QLabel`s (`util_label` / `mem_label` etc.), so the built-in text was redundant.

**Fix**: Call `setTextVisible(False)` on the 5 progress bars (GpuCard's `util_bar`/`mem_bar`/`temp_bar`, system `_cpu_bar`/`_ram_bar`) to disable built-in text; percentages continue to be shown by the normal QLabels — removing both the mojibake and the duplicate value.

**Lesson**: If the percentage is already rendered by a separate QLabel, disable the bar's built-in text with `setTextVisible(False)` instead of relying on its self-rendered text.

**Status**: Fixed in v1.2.1. **Recurred 2026-09-24 (fixed in v1.19.4)**: the QProgressBar in the download queue "Progress" column of `ui/model_tab.py` never had its built-in text disabled — the digits rendered as mojibake, and the user described it as "the percentage digits look like they're written in Chinese". Fixed the same way: added module-level `_make_progress_cell(value)` / `_set_progress_cell(cell, value)` (bar `setTextVisible(False)` + right-side QLabel showing `NN%`), and switched `_add_queue_row` / `_update_queue_row` to use them; added an offscreen regression test `tests/test_download_progress_cell.py` (asserts built-in text is off and the QLabel text contains only ASCII digits).

**Lesson (supplement)**: **Every new QProgressBar must simultaneously decide "who renders the percent text"** — miss one and it recurs (this time `model_tab` missed it; `monitor_tab` / `update_tab` had already followed the convention). Rule: if the bar needs text at all, always `setTextVisible(False)` + a side QLabel.

---

## #3 Prefix matching `mmproj-` misses non-standard vision-projection names

**Symptom**: When batch-generating scripts, `gemma-4-12b-heretic-mmproj-f16.gguf` in the `gemma-4-12b-heretic-abliterated-GGUF` directory was not recognized as a vision projection, was mistaken for a main model, produced an extra script, and that model was not bound to its mmproj.

**Root cause**: The one-off script generator used `basename.lower().startswith("mmproj-")` to detect projection files, but this model's mmproj is named `...-mmproj-f16.gguf` (mmproj in the middle, not a prefix). Prefix matching is not general.

**Fix**: Changed detection to `"mmproj" in basename.lower()` (substring match) and excluded files containing mmproj from the main-model scan. After rerunning, all 14 scripts were correct and vision models fully bound.

**Lesson**: Don't trust fixed prefixes when classifying file "types" — naming is not consistent across model families; substring matching is more robust. This pitfall only affected one-off generation logic, never entered the source tree.

---

## #4 `cmd /c` followed by an argument starting with a quote — `&&` splits the command and the .bat won't run

**Symptom**: Clicking any launch script logs `'"C:\...\data\scripts\XXX.bat"' 不是内部或外部命令，也不是可运行的程序或批处理文件` (not recognized as an internal or external command), and the process exits instantly (different PID each time, all immediate).

**Root cause**: `start_script()` in `service/process_service.py` originally passed `["cmd", "/c", f'chcp 65001 >nul && "{bat_path}"']` (list-style argument passing). Windows has special quote-stripping rules for arguments that start with a quote right after `cmd /c`, and `&&` further splits the command into two, so the bat path ends up parsed as a single command that can't be found.

**Fix**: Use a single command string with `cmd /d /s /c` + `call`, and guarantee the bat path is wrapped in double quotes:

```python
cmdline = f'cmd /d /s /c "chcp 65001>nul && call \'{bat_path}\'"'.replace("'", '"')
process = subprocess.Popen(cmdline, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000, ...)
```

Note: cmd does not treat single quotes as quotes, so the path must be double-quoted — hence the `.replace("'", '"')`. Verified llama-server starts and logs normally (see `process_service.py`).

**Lesson**: The most robust way to pass arguments to `cmd /c` from Python is "single string + `/d /s /c` + `call` + double-quoted path", not a list with embedded quotes. This pitfall affected the startup of every script — a core path.

**Status**: Fixed in v1.4.0.

---

## #5 Child processes pop black cmd windows when launched without a console, and status polling freezes the GUI

**Symptom**: During GUI operation, black console windows flash frequently and the interface stutters badly. Especially visible when autostarted (pythonw, no console).

**Root cause** (two layers):

1. All `subprocess.run(["tasklist"/"taskkill"])` calls in `service/process_service.py` lacked `creationflags=CREATE_NO_WINDOW`. When the GUI runs under pythonw (no console), every child process pops a black cmd window.
2. `_refresh_script_statuses()` in `ui/app.py` synchronously ran `tasklist` every 2s on the **GUI thread** (measured ~0.6s per run), blocking the main event loop → UI stutter.

**Fix**:

1. Added `NO_WINDOW = 0x08000000` at the top of `process_service.py` and applied `creationflags=NO_WINDOW` to all 7 `subprocess.run/Popen` calls to eliminate console windows entirely.
2. New `ui/workers/status_worker.py` (`StatusPoller` QThread): does tasklist liveness probes on a background thread and reports via the `status_refreshed` signal; the GUI thread only renders. `app.py` replaced the old `_status_timer` with `StatusPoller`, and `_refresh_script_statuses` only calls `request_poll()`.

**Lesson**: Windows GUI programs (especially pythonw / pyinstaller -w) must uniformly pass `CREATE_NO_WINDOW` when spawning child processes; any background probe that runs periodically and takes non-trivial time (>tens of ms) — tasklist/IO/network — must not run on the GUI thread; move it to a QThread/background thread with signal callbacks. This pitfall affected script startup and the fluidity of the whole UI — a core path.

**Status**: Fixed in v1.4.1.

---

## #6 In `MainWindow.__init__`, `_status_poller` is created after `_restore_service_state`, crashing at startup

**Symptom**: After switching to background polling, the GUI crashed immediately on startup (no message under pythonw — appears as "clicked but nothing happens / instant exit"). Running main.py with console python shows:

```
File "ui/app.py", line 415, in _refresh_script_statuses
    self._status_poller.request_poll()
AttributeError: 'MainWindow' object has no attribute '_status_poller'
```

**Root cause**: In `MainWindow.__init__`, `_restore_service_state()` (which calls `_refresh_script_statuses()` → `self._status_poller.request_poll()`) runs **before** `self._status_poller = StatusPoller(...)` is created — the attribute doesn't exist yet.

**Fix**: Moved the creation/connection/start of `StatusPoller` to before `_init_ui()` and `_restore_service_state()` (see `ui/app.py`), with a comment documenting the ordering constraint.

**Lesson**: When converting periodic/async components from QTimer to background threads, check every **synchronous call site** in `__init__` comes after the component is created. This class of "creation order" bug manifests as a silent crash under pythonw (no window) and is easily misread as "not applied" or "still the same". Verification can't rely on syntax checks alone — you must actually launch the GUI. This pitfall affected GUI startup — a core path.

**Status**: Fixed in v1.4.2.

---

## #7 A stale old GUI instance still holds the bridge port — after changing the bridge service, tests get fake 404s / new code appears inert

**Symptom**: After adding `GET /bridge/info` to `chat/handlers.py`, a unit test that starts `start_bridge()` and requests `/bridge/info` keeps getting 404 `{"error":"not found"}`, while old endpoints like `/settings`, `/conversations` work fine; even a debug print at the top of `do_GET` doesn't show, as if the new code wasn't loaded. Yet `start_bridge()` reports "bound successfully, port 18765".

**Root cause**: Stale `pythonw` processes of an older GUI (possibly several) still exist on the machine, and their bridge server still listens on 18765 as recorded in `data/chat/bridge_port.txt`. Python's `HTTPServer` defaults to `allow_reuse_address=1` (Windows SO_REUSEADDR permits overlapping binds), so the new test server "successfully" binds 18765 and returns that port — but the OS actually delivers TCP connections to the **old process's** socket. The old code has no `/bridge/info`, so it falls into `_serve_static` and returns 404; the debug print also goes to a socket nobody reads, so you never see it.

**Fix**: Before validating bridge changes, confirm no old GUI/pythonw instance is holding the bridge port (`Get-NetTCPConnection -LocalPort <port> -State Listen` / `Get-Process pythonw`); in tests, temporarily rename `data/chat/bridge_port.txt` so the new server binds a **fresh port**, then restore it. In real usage: after changing bridge code, **fully quit (including tray) all old instances** before restarting, otherwise the old bridge keeps running the in-memory old code.

**Lesson**: When debugging a local-port service, `bind success` ≠ `requests reach my service`. Windows SO_REUSEADDR lets a new process "fake-succeed" on the same port while the first binder still receives the traffic. Debug order: first check which process is listening on the port and whether stale GUIs remain, then suspect your own code. (The project-memory rule "kill old GUI instances before testing a new version" refers to this pitfall.)

---

## #8 Tailscale IP detection fails when the command is not on PATH — needs multi-path fallback

**Symptom**: Tailscale is installed and logged in locally (`tailscale status` works), but from the GUI (especially `pythonw` without console / PyInstaller builds) `subprocess.run(["tailscale", "ip", "-4"])` returns empty and the IP can't be detected, so the "Tailscale only" listen option gets no address.

**Root cause**: `tailscale.exe` is installed by default at `C:\Program Files\Tailscale\`, which is **not necessarily on the GUI process's PATH** (when the GUI is launched from Explorer/shortcut, PATH differs from the shell). Also, `tailscale ip -4` output is the local Tailscale IP (CGNAT 100.64.0.0/10 range, e.g. `100.x.x.x`); without validation you could mistakenly pick up other addresses.

**Fix**: `service/tailscale.py` probes in three tiers with exact range validation:

1. `tailscale` from PATH; 2. known install path `C:\Program Files\Tailscale\tailscale.exe`; 3. `psutil.net_if_addrs()` — enumerate NICs for an IPv4 in the 100.64.0.0/10 range as fallback. Each tier is accepted only if `is_tailscale_ip()` confirms it's inside the CGNAT range; if all fail, return an empty string and the dialog falls back to `0.0.0.0` so scripts still start.

**Lesson**: In GUIs/packaged apps you can't assume an "external executable" is on PATH — provide known paths + system APIs (psutil NIC enumeration) as layered fallbacks; validate detection results against the protocol/subnet to avoid picking up wrong addresses. (Tailscale's CGNAT range 100.64.0.0/10 is a reliable marker for "this is a Tailscale address".)

---

## #9 New-script dialog calls `.text()` on a QComboBox — checking `--host` always crashes (v1.5.0 regression)

**Symptom**: Opening the "New launch script" dialog, the General group is checked by default; clicking OK crashes immediately: `AttributeError: 'QComboBox' object has no attribute 'text'`. The new-script feature never worked once after v1.5.0.

**Root cause**: `get_config()` in `ui/dialogs/new_script_dialog.py` calls `.text().strip()` uniformly on all value widgets, but v1.5.0 changed the `--host` value widget from QLineEdit to QComboBox (`_build_choice_widget`), and QComboBox has no `text()` method. When Tailscale was validated at the time, the 14 `.bat` files' `--host` were hand-edited directly, bypassing the dialog — so this never surfaced.

**Fix**: `get_config()` dispatches on widget type — when `sw["choices"]` is present use `currentData()` to get the real value (e.g., the Tailscale IP), otherwise keep `.text()`. Also added a regression test `tests/test_new_script_dialog.py` (offscreen platform running QApplication, check all params and assert no crash). (Fixed in v1.6.0)

**Lesson**: When merging "widget type swap" changes, at least double-click through the affected interaction paths — a basic new-script smoke test would have caught this one immediately. Same-class hazard: the settings dialog's host field was changed to the same dropdown in lockstep (to avoid semantic divergence between the two places).

---

## #10 Monitor `/metrics` hardcodes 127.0.0.1 — precise t/s never works when listening on Tailscale

**Symptom**: After all scripts use `--host <Tailscale IP>`, the monitor page's t/s can only fall back to the log-regex method; the precise `/metrics` counter (which also zeroes during idle) never engages, and the log shows a `/metrics 抓取失败 [...] 127.0.0.1:808x: ... 连接拒绝` (connection refused) entry every 60s.

**Root cause**: `_fetch_server_tps()` in `service/monitor_service.py` hardcoded `http://127.0.0.1:{port}/metrics`. When llama-server binds a Tailscale IP (100.x.x.x) it **does not listen on loopback**, so the 127.0.0.1 connection is necessarily refused — even though `pids.json` already recorded the `host` field and it simply wasn't used.

**Fix**: `_poll_servers_once` takes the runtime entry's `host` and passes it to `_fetch_server_tps(name, port, host)`; `0.0.0.0`/empty falls back to 127.0.0.1 (loopback is reachable when bound to all interfaces). Requesting a service bound to the Tailscale IP uses that IP directly (it's a local NIC address). (Fixed in v1.6.0)

**Lesson**: Never assume 127.0.0.1 is reachable for any "service address" — once `--host` became configurable, every client (metrics, chat fallback address, port probing) must follow the actual host. Fastest way to debug this class: look for throttled `/metrics 抓取失败` entries in app.log.

---

## #11 Restoring running services runs tasklist on the GUI thread again (a regression variant of trap #5)

**Symptom**: After a GUI restart, when `pids.json` restores still-running services (scripts without an active LogWorker), the UI hiccups regularly every 2s — exactly like the problem fixed in v1.4.1.

**Root cause**: `_sync_control_panel()` in `ui/app.py`, when the selected script has no active LogWorker, falls back to `process_service.is_running(name)` → `_pid_alive()` → a **synchronous tasklist (~0.6s) on the GUI thread**. Ironically, the StatusPoller signal already carries `alive_set`, but `_apply_script_statuses` only used it to refresh the list and never cached it for the panel.

**Fix**: `_apply_script_statuses` caches each round's `runtime + alive_set` into `self._last_runtime / self._last_alive`; `_sync_control_panel` only reads the cache plus the LogWorker list — no more synchronous tasklist. On stop/end, pop the cached entries immediately so the state reflects at once (not waiting for the next poll round). (Fixed in v1.6.0)

**Lesson**: "No blocking operations on the GUI thread" must hold across **every** call path, not just the one that originally broke; once background polling results arrive, cache them for any later consumer instead of letting a side path re-query.

---

## #12 After pausing a download, changing the directory and resuming produces a corrupted GGUF with a missing header

**Symptom**: Downloading model A to D1 → pause → change the download directory to D2 in settings → resume "succeeds", but loading the GGUF reports a format error (missing header / wrong size), and it's hard to suspect the download.

**Root cause**: `start_download()` in `service/download_service.py` resumed a paused task using `entry.downloaded` (byte count from the old file in D1), but `dest_path` now points to D2. D2 has no local file (or a different size), so `download_file` issues `Range: bytes=N-` with append mode ("ab") onto D2's **empty file — appending only the tail half**, producing a file with just the ending.

**Fix**: On resume, recompute the resume baseline from the **actual local file size** (no file in the new directory → start from 0; complete file already present → mark done directly), never trusting `entry.downloaded`. (Fixed in v1.6.0)

**Lesson**: Persisted "progress values" are only usable after being reconciled against the **actual current environment state**; whenever external conditions (paths/directories) can change, resume logic must re-verify local facts — especially resumable downloads.

---

## #13 llama.cpp release asset naming evolves with upstream — variant identification must not be a hardcoded enum

**Symptom**: The initial version-management implementation hardcoded a variant enum from the 2026-08 asset list (cuda-12.4 / cuda-13.1 / hip-radeon). Validating against the actual latest release (b10793, 2026-09-03) revealed: CUDA 13 series changed from `cuda-13.1` to `cuda-13.3`, AMD packages changed from `hip-radeon` to `rocm-10.0`, and a new `openvino-2026.3.1` appeared — the recommendation logic that insisted on `cuda-13.1` silently broke on the latest version (machines with driver ≥580 got downgraded to cuda-12.4).

**Root cause**: llama.cpp release asset names are not a stable contract; variant suffixes (CUDA version numbers, backend names) evolve with the upstream build matrix. Exact-string matching/enumeration for classification and recommendation silently breaks whenever versions change.

**Fix**: `recommend_variant()` in `service/llamacpp_update_service.py` processes by `cuda-` prefix family: `sorted(..., reverse=True)` descending tries all CUDA variants, validating the driver threshold per major version (13+ needs driver ≥580, 12.x ≥528), degrading step by step; display names are generated by prefix rules in `variant_label()` (`cuda-*` / `openvino-*` / `rocm-*`), with unknown new variants falling back to the raw key. The cudart companion package matches `cudart-{variant}` dynamically, also not hardcoded. (v1.7.0; tests cover cuda-13.3 / old-driver degradation / no-CUDA-asset fallback)

Also noted: public mirror stability varies — measured ghfast.top SSL handshake timeouts (unusable), gh-proxy.com works; GitHub direct is intermittently flaky (10060). The UI mirror-prefix placeholder suggests the verified `https://gh-proxy.com/`; when downloads fail, switch mirrors before suspecting the code.

**Status**: Avoided in v1.7.0 (prefix-family matching).

---

## #14 Auto-download state flag never set — the silent download path silently dies (no error at all)

**Symptom**: The background silent download feature on the version-management page never triggers — local detection and the release list both return fine, but `_maybe_auto_download()` always returns early; the smoke test asserts `auto start wrong: []` / `two-channel plan wrong: []`, with no exception or log.

**Root cause**: `_on_local_info()` in `ui/update_tab.py` declared the precondition flag `self._local_info_done` (initialized False in `__init__`) but **never set it to True** in the callback; line 304's `if not self._local_info_done: return` short-circuits forever. Two companion issues: ① `_refresh_installed()` never backfilled `self._installed_dirs` (installed_keys filter is always empty → already-installed versions get re-planned, and `_copy_sources()` is left with only the current directory); ② `_install_silent` was only assigned inside `_start_install()`, so when that method was replaced externally (test mocks / future refactors) the flag is lost and the completion callback goes down the wrong dialog branch. A classic "state-machine flag declared but never set", with the failure mode being silent skipping — no exception, no log, only an end-to-end assertion can catch it.

**Fix**: Three fixes in `ui/update_tab.py` (v1.7.1): ① set `self._local_info_done = True` at the start of the `_on_local_info()` callback; ② `_refresh_installed()` collects `self._installed_dirs = [item["dir"] for item in items]`; ③ `_start_next_auto()` sets `self._install_silent = True` **before** calling `_start_install(..., silent=True)` (the internal assignment in `_start_install` stays, idempotent).

**Lesson**: Cross-callback readiness flags (done/handled) must be explicitly set inside their data-source callbacks, and this class of "condition never satisfied" bug can only be exposed by chain-level smoke tests — pure-function unit tests all green does not mean the UI orchestration layer is correct.

---

## #15 Newer llama.cpp removes `--mmap` (uses `--load-mode` instead) — old scripts exit instantly

**Symptom**: A newly created MiniCPM5 script fails on startup, logging `error: invalid argument: --mmap` / `进程已结束` (exit code 1, before model load).

**Root cause**: llama.cpp removed `--mmap / --no-mmap` in recent versions (this machine's b10883), making mmap default behavior controlled by `--load-mode auto|mmap|mlock|mmap+mlock`. Older builds (e.g., b10819) still accept `--mmap` (marked DEPRECATED), so after switching versions all existing scripts containing `--mmap` break. Argument parsing fails before model load, appearing as an instant exit.

**Fix**: Removed `--mmap` from all scripts in the b10883 environment (new default load-mode=auto is mmap-first, behaviorally equivalent). One-shot batch handling: `re.sub(r"--mmap\s*\^?\r?\n", "", content)`, written back to both `.bat` and `scripts.json`; cleaned 7 existing scripts (Qwen3.8-27B, gemma-4-12b-heretic-abliterated, GLM-4.7-Flash, gemma-4-26B-A4B, qwen3.8-27B-IQ3, Qwen3.8-27B-IQ2, MiniCPM5).

**Lesson**: llama.cpp CLI arguments are unstable as upstream evolves (same lesson class as #13). After switching install versions (v1.7.0 version management), deprecated/removed arguments in old scripts cause silent instant exits; when debugging "process exits instantly", first check whether the current `llama-server --help` still supports every argument in the script — never assume arguments stay valid. Script generation should avoid writing non-stable parameters.

---

## #16 Mixed path separators: Qt gives `/`, os.path gives `\` — scripts and GUI display disagree

**Symptom**: The GUI model picker shows `C:/modelscope/...` (forward slashes), while `.bat` files contain `cd /d "C:\Users\..."` (backslashes), `-m "C:/modelscope\models\..."` (mixed), and `--mmproj "C:/modelscope\models\...\mmproj-...gguf"` (mixed); `model_path` in scripts.json is also mixed. The same path appears in three different styles.

**Root cause**: Paths were never unified at the "write point"; four sources coexisted:

1. `QFileDialog` returns forward slashes (Qt-native), stored as-is into app_config.json via Settings;
2. Python `os.path.join / dirname / normpath` returns backslashes on Windows (`find_mmproj` produced `C:/modelscope\models\...` mixed; `replace_bat_dir`'s new_dir was backslashes);
3. Historical one-off scripts joined forward-slash roots with subdirectories via os.path.join → mixed;
4. Hand-edited `.bat` backslashes.

Windows file APIs accept both `/` and `\`, and llama-server can load mixed paths, so everything worked — but it looked inconsistent and string comparison was fragile (a landmine).

**Fix**: Establish **forward slash `/` as the single canonical form** (cross-platform, no JSON escaping, Qt-native, cmd/llama-server compatible), enforced at all write points (v1.9.0):

- New `normalize_path()` in `utils/path_utils.py` (`\` → `/`, idempotent);
- All path setters in `config/config.py` (llamacpp_path / model_path / model_dir / visual_model_path / download_path / llamacpp_install_root) normalize;
- `build_bat_content` normalizes exe_dir / model_path / visual_model_path entries; `find_mmproj` normalizes before returning;
- `replace_bat_dir`'s new_dir normalized (it used to produce backslashes from normpath);
- One-shot migration rewrote all paths in the 17 existing `.bat` + scripts.json + app_config.json to `/`.

**Lesson**: Windows path handling needs one globally unified separator convention, enforced at three points — Qt return values, os.path products, and script/config write points; don't rely on "Windows accepts both" to tolerate mixing. Every new place that writes a path (config setters, script generation, logs) must go through `normalize_path()`.

---

## #17 When a script name contains spaces, `name` ≠ the bat file name (sanitize converts to underscores) — you fix the wrong file without realizing it

**Symptom**: After creating a `Mini CPM5-2B` script (name with a space) and starting it, it reports `invalid argument: --mmap`. The fix edited `data/scripts/Mini CPM5-2B.bat` (the spaced version), and the verification script also read `{name}.bat` — everything "passed", but launching MiniCPM5 from the GUI **still errored**.

**Root cause**: `ScriptEntry.sanitize_filename()` converts spaces to underscores (`Mini CPM5-2B` → `Mini_CPM5-2B`); `ScriptService.get_script_path` / `save_to_file` actually read/write **`Mini_CPM5-2B.bat` (the underscored version)**. The spaced .bat is an orphan (the GUI list also shows it as an extra orphan entry). So:
- the fix edited the spaced orphan → the actually-loaded underscored version kept the old content (with `--mmap`);
- the verification script built the path from `name` → checked the orphan file → falsely reported "consistent".

**Fix** (done together in v1.9.0):
- Synced the underscored bat content to the correct content from scripts.json and deleted the spaced orphan;
- All script-file reads/writes/verification **must go through `ScriptEntry.sanitize_filename(name)`** to get the real file name, never `{name}.bat` directly.

**Lesson**: `name` in scripts.json is the display name; the bat file name is the sanitized safe name. They diverge when the name contains spaces/special characters. Any "locate file by script name" logic (read, write, delete, consistency check) must pass through `sanitize_filename` first; otherwise you get silent "changed but not applied" mismatches, and verification will even confirm it "looks fine".

---

## #18 GitHub release assets upload incrementally — auto-download silently dies when the latest version "only has cudart"

**Symptom**: The version-management page shows a new version (e.g., b10933), but background auto-download never triggers; the version's "build variant" dropdown is empty and can't be downloaded, while the next version (b10932) works fine.

**Root cause**: llama.cpp uploads release assets **incrementally** (create the release first, then upload zips one by one). Fetching via cache/API before uploads complete yields an incomplete snapshot with "only the cudart runtime, no main package". In `plan_auto_download`, `variants = [v for v in assets if not v.startswith("cudart-")]` is then always empty → silently `return []`; and `fetch_releases`' 1h cache TTL returns the old snapshot directly, with GitHub direct-connection timeouts falling back to the stale cache — so the app stays "new version available but never downloading" for a long time.

**Fix** (v1.10.1):
1. `_release_from_api`: drop releases whose assets have no main package (only cudart);
2. `fetch_releases`: when a cache hit is within TTL, check whether the latest release has a main asset — if incomplete, treat it as expired and force a refetch (self-healing);
3. Enhanced `plan_auto_download`: accept the whole releases list, skip incomplete snapshots, and pick the **first downloadable latest recommended version** (single-dict argument stays compatible).

**Lesson**: An external API's "latest entry" may be an in-progress incomplete state (GitHub release assets, cloud storage uploads, CDN origin-backfill — same pattern). Any decision based on "latest" (auto-download, default selection) must validate data completeness and fall back; cache hits must also validate content, not just TTL.

---

## #19 ModelScope file-size metadata is inaccurate — download progress percentages go haywire

**Symptom**: Download progress shows absurd percentages (100% at half way, or out-of-range values like 677%). Measured: MiniCPM5-2B-F16.gguf metadata `file_size` 744MB vs actual 5GB (~7× off); Qwen3.8-27B-UD-IQ4_XS metadata 1.4GB vs actual 14GB.

**Root cause**: `DownloadEntry.progress` computes `downloaded / file_size`, and `file_size` comes from ModelScope's file-list API metadata (some files don't match actual size). During download, `_on_progress` only overwrites `file_size` when HTTP `total > 0` (has Content-Length); without Content-Length it stays on the wrong metadata → percentage exceeds 100 (the QProgressBar built-in text directly shows out-of-range values).

**Fix** (v1.10.1):
1. `_on_progress`: when `total > 0`, overwrite `file_size` with the HTTP full size (HTTP values are more reliable than metadata);
2. `_on_finished`: calibrate `file_size` to the actual file size at completion (`max`), ensuring convergence to 100%;
3. `DownloadEntry.progress`: clamp the percentage to 0~100 (defense against any corrupt input).

**Lesson**: External metadata (file sizes, version numbers) cannot be the trusted baseline for progress/comparison — always use the actual transferred content and clamp at the boundaries; progress UIs must always defend against "numerator > denominator" out-of-range input.

---

## #20 Scheduled-task thread and HTTP handler concurrently read-modify-write the same conversation JSON — updates overwrite each other

**Symptom**: Scheduled task results or chat messages occasionally get lost (the later writer overwrites the earlier one); triggered when the scheduler thread and chat-request thread write the same conversation file almost simultaneously.

**Root cause**: The read-modify-write in `chat/scheduler.py` (list_conversations → modify messages/tasks → write_conv_file) and the same in `chat/handlers.py` run lock-free concurrently. Both threads modify from an old snapshot and atomically replace the file; the later writer wipes out the earlier writer's changes entirely.

**Fix** (v1.11.0 audit fix):
- Added module-level `CONV_LOCK = threading.RLock()` in `chat/repository.py` (reentrant, so scheduler's internal `write_conv_file` re-locking while holding the lock doesn't deadlock); `write_conv_file` is fully wrapped (serialized writes);
- `chat/scheduler.py` success path splits into two lock sections: read conv/task/agent and build the request inside the lock before the network call (up to 120s); after the call, **re-read the latest conv** and write results back (avoiding overwriting concurrent updates from a stale object); `_record_task_error` is also locked.
- Known residual limitation: the handler's "read outside the lock" still has a tiny overwrite window (writes are serialized); under extreme concurrency one update may be lost — recorded as a low-probability known item.

**Lesson**: Multi-threaded + file-stored "read-modify-write" must be serialized as a whole transaction (lock the entire operation); locking only the write function is insufficient. Long network operations must never run inside the lock. And before writing back, re-read the latest state — don't trust the snapshot read before the call.

---

## #21 `set_preset` backfill only fills values, not checkbox states — loading a saved script "looks read but isn't used"

**Symptom**: After v1.15.0 bound scripts to models, selecting a model with a bound script shows values in the form, but some parameter toggle is still grayed out; after save/run the parameter is lost (as if it never loaded).

**Root cause**: `set_preset` in `ui/script_form_widget.py` originally only filled values for keys present in the config, but **never touched QCheckBox checked states**; meanwhile `get_config` only collects parameters that are **checked**. So "value filled in, toggle not checked" → that parameter is skipped on save, and the form-as-truth-source disagrees with the on-disk script (older versions masked this via the "form is the truth source" fallback; it only surfaced once binding to models made loading the norm).

**Fix** (v1.15.0):
- Changed `set_preset`: for every key present in config whose value is non-empty (not None/""/"off"), also set `checkboxes[key].setChecked(True)`. Both parse backfill and `auto_generate_config` auto-generation now truthfully reflect "this parameter is enabled".

**Lesson**: Table/form component "backfill" must sync both the value and the enabled state — neither can be missing. Whatever dimension you filter on during collection (here, the checked state) must be restored during backfill.

---

## #22 Tray "Restart" falsely reports "already running" — single-instance lock retry window too short

**Symptom**: Clicking "Restart" from the tray or version-management page: the new instance pops "LlamaCPP GUI 已在运行" (already running), restart fails.

**Root cause**: `_restart_app` launches the new instance with `subprocess.Popen` and only then `close()`s the old one. When the new instance grabs `app.lock`, the old instance hasn't exited yet (StatusPoller wait 300ms + LogWorker wait 300ms + bridge shutdown + monitor stop, total time varies), and the lock retry window is only 10×0.15s=1.5s — not enough for the old instance to release the lock, so it misjudges single-instance occupancy.

**Fix** (v1.15.0, final):
- `_restart_app` sets env var `LLAMACPP_RESTARTING=1` for the child before Popen;
- `main()` **skips the single-instance lock entirely for restart instances** (`if os.environ.get("LLAMACPP_RESTARTING") != "1":`) — restart is an explicit user intent and shouldn't be blocked by the lock; ordinary first launches remain protected, repeated normal launches do a short retry then prompt.
  (The initial approach "restart uses 40×0.25s long retry waiting for the old instance to release the lock" still mis-blocked when a leftover/zombie instance held the lock, so it was changed to skip the lock completely.)

**Lesson**: In a "restart = start new + quit old" flow, the new instance starts before the old one exits, inherently creating a race window; lengthening lock retries is only mitigation — the most reliable fix is **the restart path bypassing the single-instance lock** (distinguishing restart from normal launch).

**Second optimization** (same v1.15.0) — restart "works but is slow": the old instance's `closeEvent` had `bridge.shutdown()` blocking ~0.5s (HTTPServer serve_forever poll period) + `monitor.stop()` join(2s), slowing the exit and making the new instance wait longer. Changes:
- bridge shutdown moved to a `threading.Thread(daemon)` so it doesn't block the GUI (on process exit the daemon serve_forever is reaped by the OS and the port freed; the new instance rebinds after the old process fully exits — no overlap);
- `monitor_service.MonitorService.stop()` metrics join lowered from 2s to 0.2s (the metrics thread is daemon; reaped on process exit).
- Note: StatusPoller/LogWorker QThread `wait` guards against "destroyed while running" crashes and must not be cut aggressively for speed.

---

## #23 HTTP server shutdown() blocks waiting for the poll cycle (~0.5s)

**Symptom**: Quitting/restarting the GUI, clicking close hangs ~half a second before disappearing.

**Root cause**: `http.server.HTTPServer.shutdown()` blocks until `serve_forever`'s poll cycle ends (default ~0.5s); the bridge (`chat/server.py` using a daemon `serve_forever` thread) was called synchronously in `MainWindow.closeEvent`, stalling the GUI.

**Fix**: Trigger `_bridge_server.shutdown()` in a background daemon thread without blocking; on process exit the daemon serve_forever is reaped and the OS closes the socket and frees the port — the new instance rebinds after the old process exits, no SO_REUSEADDR overlap.

**Lesson**: Any graceful shutdown that "blocks waiting for a poll/round-trip cycle" must not occupy the UI thread; in scenarios where a daemon thread + OS reaping at process exit applies, it can safely be triggered in the background.

---

## #24 After switching models, the form retains the previous model's parameters, polluting the new model's script

**Symptom**: Switching from model A (bound script, with MTP/KV-quant params checked) to model B (no bound script), B's form still has A's params checked; saving directly gives B a script carrying A's config (small model gets a huge ctx, wrong quantization, etc.) — runs abnormally with no error at all.

**Root cause**: `set_preset(config)` in `ui/script_form_widget.py` only backfills fields present in config; uncovered fields keep the previous model's state. `auto_generate_config` only produces base parameters and never reports advanced ones → they persist and get frozen into the .bat on save.

**Fix**: `set_preset` first calls `_reset_to_defaults()` (resets checks and values wholesale per `script_builder.CATEGORIES` factory defaults) before backfilling config.

**Lesson**: "Incremental backfill"-style setters must fully reset first, otherwise the previous call's state becomes this call's implicit input; in a form-as-truth-source architecture, this pollution is written straight into the persisted script.

---

## #25 chat handler layer compound read-modify-write without locks — concurrent updates overwrite each other (extension of trap #20)

**Symptom**: While the scheduler writes a conversation, the user renames tasks / adds or removes them in the Web UI — occasionally "just-saved task disappears" or "title reverts to the old value".

**Root cause**: 4 endpoints in `chat/handlers.py` (POST tasks, PUT tasks/{tid}, PUT conversations/{cid}, DELETE tasks/{tid}) each do "read JSON → modify → write back". Trap #20 only serialized the single repository write; the handler layer's cross-function compound RMW can still interleave, with the later writer overwriting the earlier one.

**Fix**: Wrap the entire read-modify-write segment in `chat/repository.CONV_LOCK` (RLock), building the response outside the lock; inside the lock, use the re-read latest data.

**Lesson**: Lock granularity must cover the complete business transaction (read + compute + write); locking only the "write" can't lock out the "read was already stale" race.

---

## #26 chat.js concurrent replies misplace + polling overwrites in-flight messages

**Symptom**: Sending a message @-ing two agents occasionally returns only one reply, or a reply lands in the wrong agent's bubble; switching conversation and back during streaming, the in-progress reply gets wiped by the full message-list refresh.

**Root cause**: The non-streaming branch pushed then located the message with `state.messages[state.messages.length - 1]`; with two concurrent requests both pushing, each one's "-1" is the same entry. `pollConversation` unconditionally replaces `state.messages` with the polling result, clobbering the local in-progress state.

**Fix**: Record `const msgIdx = state.messages.length` before pushing, and update by index; `pollConversation` guards with `if (state.isSending) return` at the top.

**Lesson**: Any frontend code that "appends then locates by length-1" must record the index before the append — length-1 positioning is unsafe under concurrency; background polling must skip the local in-flight mutation window before writing the list back.

---

## #27 "Clean old versions" plans to delete the version directory of a currently running service

**Symptom**: Clicking "Clean old versions" while a certain version's llama-server is running; after confirming, that version's exe directory is deleted. The process survives but its files are gone — the next service restart fails outright.

**Root cause**: `plan_version_cleanup` in `llamacpp_update_service.py` only excluded "the exe currently in use per config", unaware that other scripts in `pids.json` may be running other version directories.

**Fix**: The UI collects running directories (from pids.json runtime + `cd /d "..."` extracted from scripts) and passes them into `plan_version_cleanup(running_dirs=...)`; running directories are always retained.

**Lesson**: Disk-cleanup planning must take the "process truth" (pids.json + working dirs in .bat) as input — "current config path" is just one consumer.

---

## #28 Lambdas in loops capture loop variables / row-number snapshots — already drifted by click time

**Symptom**: Clicking "Track" on one row in search results refreshes the last row's button state; in the download queue, deleting one row then clicking another row's "Remove" deletes the wrong row (or raises KeyError).

**Root cause**: Python closure late binding — `lambda: self._watch_model(mid, watch_btn)` references loop variables `mid/watch_btn`, and by click time the loop has long finished; queue remove buttons captured the creation-time row number `row`, which shifts when rows are deleted in between.

**Fix**: Default-argument binding captures the current value (`lambda _, e=entry: ...`, `lambda checked, x=mid, b=watch_btn: ...`); don't snapshot row numbers — at click time, look up the row by the `(source, file_path)` stored in UserRole via `indexOf`, then removeRow.

**Lesson**: For callbacks attached to dynamic rows in Qt tables/lists, always "bind values via default args + look up the row by key at click time"; never trust creation-time row numbers or loop variables.

---

## #29 When the conversation/task/agent referenced by a scheduled task is deleted, the scheduler thread spins every second scanning nothing

**Symptom**: After deleting a conversation or its tasks, the log floods "conversation does not exist"/"task does not exist" every second; after deleting an agent, the task never executes, doesn't error, and `next_run_time` doesn't advance, causing retries every round.

**Root cause**: The not-found branch in `chat/scheduler.py` only logs and returns, never rebuilding the index (task_index still holds the deleted entries) → every schedule round misses and does a full scan; the missing-agent branch spins the same way.

**Fix**: conv/task not-found → `rebuild_task_index()` then return (index self-heals); agent not-found → `_record_task_error()` records the error and advances `next_run_time` (prevents spinning).

**Lesson**: A scheduler needs a self-healing path when a referenced entity disappears (rebuild index / advance time / disable); logging without changing state = an infinite loop that floods the screen.

---

## #30 `parse_bat_params` can't parse quoted values with spaces, and short flags match falsely

**Symptom**: Values containing spaces (double-quoted in the .bat, e.g., `--host`) backfill empty/truncated in the form; the reverse-parsed params don't match the .bat's actual values, silently rewriting the script on save.

**Root cause**: The value regex `\S+` doesn't consume spaces inside quotes; short flags (like `-m`) have no word boundary and can hit the prefix of longer flags like `-mmproj`.

**Fix**: `parse_bat_params` in `service/script_builder.py` changes the value regex to `("[^"]*"|\S+)` and adds a `(?<![\w-])` leading word boundary for flags.

**Lesson**: Reverse-parsing generated artifacts (.bat → form) must be symmetric with the generator's (form → .bat) quoting rules, otherwise the roundtrip silently loses data.

---

## #31 GUI-thread blocking ×5: tailscale probe / full cleanup / local model scan / image migration / cleanup planning

**Symptom**: Switching models or on server-ready, the UI occasionally freezes for seconds (tailscale cache expired); clicking "Clean all llama processes" leaves the UI unresponsive until logs appear (worse with multiple instances); switching to the "Model Update Tracker" tab stutters (seconds-level with a large model library); the first few seconds after launching the GUI feel laggy; with many old versions, "Clean old versions" stats phase freezes the GUI.

**Root cause** (variants of traps #5/#11, this time hidden in less obvious paths):
`_update_external_url` → `get_tailscale_ipv4()` runs the tailscale subprocess synchronously on the GUI thread after the 60s cache expires (timeout 3s × 2 candidates); `_cleanup_all_processes` loops tasklist+taskkill on the GUI thread; `merge_local_models` does a full-disk os.walk on the GUI thread (and model_watch_tab still had an old synchronous `refresh()` overriding the background version); `migrate_conversation_images` scans all conversations synchronously at startup; version-cleanup planning/size stats run on the GUI thread.

**Fix**: Moved everything to background QThreads — `TailscaleProbeWorker` (GUI-side 60s cache is read-only, probes return asynchronously), `KillAllLlamaWorker`, `WatchMergeWorker`, `CleanupPlanWorker`/`CleanupExecWorker` (two-phase split so a confirmation dialog can be inserted), image migration to a daemon Thread; also removed the old duplicate `refresh()` in model_watch_tab that overrode the new implementation (see #32).

**Lesson**: "No blocking on the GUI thread" must audit every call chain: recomputation after cache expiry, stats/execution around confirmation dialogs, full scans triggered by tab switches, startup data migrations — all are hotspots for blocking to creep back.

---

## #32 Duplicate method definitions with the same name in a class body — the later one silently overrides the earlier

**Symptom**: After converting `model_watch_tab.refresh()` to a background merge, switching tabs still stutters — the backgrounding "didn't take effect"; two `def refresh` definitions sit ~40 lines apart and are easy to miss in a visual review.

**Root cause**: The refactor added a new `refresh()` (calling `_request_merge`) but the old synchronous `refresh()` (directly calling `merge_local_models`) remained after `_build_ui` — Python executes class bodies in order, the later definition overrides the earlier, and the old implementation silently wins.

**Fix**: Remove the duplicate definition; after renaming/refactoring a method, grep the whole file for `def <method>` to confirm uniqueness.

**Lesson**: When inserting a new implementation in the middle of large method blocks, confirm the old version is fully removed; `grep -n "def name"` is a mandatory final check.

---

## #33 After QComboBox.setEditable, a typed value doesn't change currentIndex/currentData

**Symptom**: After ctx-size became "tier dropdown + manual entry", the user types 50000 and saves, but the .bat writes the previously selected tier value (e.g., 32768) — what you see is not what you get.

**Root cause**: On an editable QComboBox, `setEditText()`/user typing only changes the edit-box text, **not currentIndex**: currentData stays on the previous candidate. The form read logic `get_config` uniformly uses `currentData()` for all QComboBoxes, silently dropping typed values.

**Fix**: In `ui/script_form_widget.py`, `get_config` reads `currentText()` for `isEditable()` combos (tier display text like "128K (131072)" extracts the number in parens via regex; pure numeric typed values pass through unchanged); `set_preset` backfill uses `setEditText()` for values not among the tiers instead of only findData. Also `NoInsert` prevents typed values from mixing into candidates.

**Lesson**: An editable dropdown has two "value" sources (item data vs edit-box text); which to read depends on the interaction: if manual entry is allowed, `currentText` must be authoritative; when tier labels and stored values differ ("128K" vs "131072"), apply the same conversion on both read and write sides.

---

## #35 The chat page's "manually saved address" permanently overrides auto-detection — the user forgot they saved it

**Symptom**: After switching to a model on a different port/host and opening the chat window, the API address always shows an old value like `http://127.0.0.1:8086` (saved manually at some point), no matter whether the current service uses a Tailscale address or 127.0.0.1 or any port — "auto-fetch" never works. Fixing the backend provider (#34) correctly isn't enough — the problem is in the frontend.

**Root cause**: `chat.js` init's priority design: "manually saved (llamaUrlManual=true) → always use the manual value". This "respect the user's config" design has two fatal flaws:
1. The user doesn't remember saving it; the old value in localStorage becomes a ghost config;
2. No UI surface clears the manual state (only F12 + clear localStorage).
The stale manual value thus becomes a permanent deadlock, neutralizing all backend auto-detection.

**Fix**: Priority inverted to **auto-detection first** — `init` becomes `defaultLlmUrl || saved.llamaUrl` (a running service always wins; the manual value only falls back when no service exists); the settings dialog adds an "Auto Detect" button next to the API address (fetch `bridge/info` → fill + test connection, instantly refreshing); the `llamaUrlManual` flag is downgraded to pure history (no longer part of value selection).

**Lesson**: "Respect user manual config"-style designs must answer two questions: how does the user **know** they're in manual mode, and how do they **exit** it? Manual-first without visible state and an exit = ghost-config deadlock. Same for caches: auto values should always beat the last manual snapshot; manual values only as a fallback when no auto value exists.

---

## #36 Models manually tracked on the search page disappear from the tracker — background workers hold an in-memory stale snapshot and overwrite the disk wholesale

**Symptom**: On the model search page, clicking "Track" on a not-yet-downloaded model (prompt says success), then switching to the "Model Update Tracker" tab — it's not there; inspecting `data/model_watchlist.json` shows only entries with `added_by=local` — the manually tracked entries are completely gone (data loss, not just not-displayed).

**Root cause**: The watchlist has multiple write entries (search page `add_manual_model` / tracker page `merge_local_models` and `check_updates` / removal), but background workers all hold the **stale in-memory snapshot** passed from the UI:
1. `ModelWatchTab._request_merge` passes `self._watchlist` (without the just-tracked entry) to WatchMergeWorker;
2. the worker scans and calls `merge_local_models(dir, 旧快照)` → `_save_watchlist(旧快照+local additions)` **overwrites the disk wholesale** — the manual entries just written by `add_manual_model` are wiped;
3. WatchCheckWorker similarly writes the stale snapshot (checking takes 15s+, an even bigger window).

**Fix** (double-layer defense, closed at the service layer):
1. `_save_watchlist` becomes **merge semantics**: before writing, re-read the disk and keep entries the disk has but memory doesn't (dedup by `model_id`, memory wins), and uniformly strip ignored entries (prevents "removed" from being resurrected by stale snapshots); all write paths return the merged full list;
2. workers always re-read from disk (`merge_local_models(dir)` / `check_updates(load_watchlist())`), never trusting the UI snapshot;
3. `remove_model` order adjusted: write ignored first, then delete the entry and write — otherwise the merge semantics pulls the just-deleted entry back from disk.
Added 3 regression tests (stale snapshot keeps manual entries / writes during checking aren't lost / removal isn't resurrected by stale snapshot).

**Lesson**: When multiple entries read-modify-write the same JSON file, any long-running background operation (scan/network check) that "uses a read-time snapshot to overwrite the disk wholesale" is a data-loss source — writes must merge (or lock + re-read); snapshots are for the presentation layer only. Before the UI passes "current state" to a worker, ask: could another entry have already changed the disk during execution?

---

## #34 The chat page can't get the running model's address — the provider's two sources both only query "current selection"

**Symptom**: Running model A, then switching to model B to tune parameters, clicking "Chat Window" opens the chat page and the model API address isn't auto-filled (must be entered manually). Selecting A works fine; switching away breaks it.

**Root cause**: `_current_llm_url` in `ui/app.py` (injected into the bridge service, called by `/bridge/info`) treats "currently selected script" as the only candidate in both layers:
1. Ready-URL layer: `if current_script_name: candidates.append(_server_urls.get(当前名))` — even when it returns `None`, it **doesn't fall back** to other ready URLs (the fallback branch hangs on `elif`, so a non-empty selected name never reaches it);
2. pids.json layer: filters runtime down to only the selected name's record — when the selected model isn't in runtime, the dict becomes empty, the loop body never runs, and it returns `""` overall.
Since v1.15.0 bound scripts to models, "switch model to tune params" is a high-frequency action, and selected name ≠ running model is now the norm. One companion hazard: host resolution synchronously calls `get_tailscale_ipv4()` while the provider runs on the **bridge HTTP thread** — the probe subprocess can block ~6s worst-case and bypasses the GUI-side probe cache (the missed spot of v1.16.0 R12 backgrounding).

**Fix**: Candidate logic extracted to a module-level pure function `_pick_llm_url(current_name, server_urls, runtime, override, ts_cache)`: both layers unify to "selected name first → fall back to any running record"; host resolution `_resolve_llm_host` only reads the manual override / probe cache (plain attribute, read-safe under GIL), never spawning subprocesses or QThreads (cross-thread unsafe). Added 7 regression tests.

**Lesson**: In multi-entity (multi-script/multi-model) scenarios, "filter by current selection" code must always answer "who do we fall back to when the selection doesn't exist"; callbacks injected across threads (HTTP handler → GUI provider) may only do side-effect-free pure reads internally — any "probe/refresh"-type action must stay on the GUI thread.

---

## #37 The download source for a tracked model's "View Files" must be independent of the search source — sharing `self._current_source` downloads wrong URLs

**Symptom** (identified during the v1.17.0 merge refactor, avoided before implementation): the user switches the source to the HF mirror for searching, then in the "Update Tracker" list clicks "View Files" on a model to enter the file list, checks/click-downloads — if the download chain still reads `self._current_source` (the HF mirror), it concatenates hf-mirror URLs with ModelScope model_ids/file paths, and the download necessarily fails.

**Root cause**: The tracker list's model source is always ModelScope (`add_manual_model` / `merge_local_models` both write `source: modelscope`), but the file-list page's download methods `_download_selected` / `_start_single_download` originally uniformly read the UI's `self._current_source` (following the search-source dropdown). After the view merge, the same file-list page has two entry paths (search results / tracker view), splitting the source semantics:
- entering from search results → source should follow the combo;
- entering from the tracker view → source must be forced to ModelScope.
Sharing one variable guarantees one path is wrong.

**Fix**: Introduced an independent `self._filelist_source` — `_show_file_list` gains a `source` parameter (default `_current_source`, the tracker entry explicitly passes `SOURCE_MODELSCOPE`); file-list rendering (`FileListWorker`), `_download_selected`, and `_start_single_download` all read `_filelist_source`. Also the "Back" button no longer hardcodes returning to search results; `self._filelist_back_index` remembers the entry source (0 = tracker view / 1 = search results page).

**Lesson**: When a shared view (the file-list page) is reused by multiple entries with different context parameters (source, return target), UI state must be modeled per "current view" (record context variables when entering the view), not per "global selection" — the latter lets the second entry quietly use the wrong parameters. When merging features, check every **entry** and **consumer** of the merged view (not just the entry itself).

---

## #38 scripts.json has multiple bindings for the same model, and model_path diverges from the .bat's `-m` — running MiniCPM5 shows gemma-4

**Symptom** (fixed in v1.19.0): loading `MiniCPM5-2B-F16.gguf` and running, the "Run Control" and the running-model list show `gemma-4-E4B`. Checking the disk:
- `data/scripts.json` has **3 entries** for the same MiniCPM5 path (`gemma-4-E4B`, `Mini CPM5-2B`, `MiniCPM5-2B-F16_1edbd53f`);
- line 4 of `data/scripts/gemma-4-E4B.bat` is actually `-m "C:/modelscope/MiniCPM5-2B-F16.gguf"` (script name and content don't match);
- 2 other entries have json `model_path` completely mismatched with the .bat's `-m` (e.g., `Qwen3.8-27B`'s model_path points at a gemma path).

**Root cause**:
1. `get_script_for_model` in `service/script_service.py` originally took "**the first match** by normalized path", and the misplaced entry (the 12th, `gemma-4-E4B`) came before the canonical-name entry (the 18th) → returned the wrong script name → running, writing `pids.json`, and the running-model list all show gemma-4-E4B.
2. The write side `_upsert_config_entry` deduped only by `name`, never comparing `model_path` — renaming and saving under the same model **added** an entry instead of merging (the direct source of dirty data, a leftover from the early "editable script name + manual model binding" era).
3. Early versions rewrote json `model_path` and the .bat's `-m` separately when the model path changed in the form, creating **two sources of truth** that necessarily drifted.

**Fix** (closed at three ends, `service/script_service.py`):
- **Read side**: new pure functions `name_model_score(name, model_path)` (ratio of script-name tokens hitting the model file name) and `binding_rank` (fit > pinned > most recently saved); `get_script_for_model` picks the best among all same-path candidates by `binding_rank` (fallback still via `derive_name`).
- **Write side**: `_upsert_config_entry` adds "same normalized `model_path`" to the dedup condition; on rename, clears the old `.bat` with the new `_remove_bat(old_name)` to prevent orphan scripts.
- **Startup migration**: new `migrate_bindings()` one-shot cleanup — corrects json `model_path` from the .bat's `-m` as the **source of truth**; groups by `model_path` keeping only the highest `binding_rank`; moves (not deletes) retired entries' `.bat` to `data/scripts_replaced` (`REPLACED_SCRIPTS_DIR` in `config/__init__.py`, human-recoverable); removes ghost entries whose `.bat` no longer exists; the whole flow is idempotent. `ui/app.py` calls it after `_init_tray` and logs it; `process_service.remap_runtime` syncs `pids.json` keys.
- Measured locally: 18 → 15 entries (2 corrected, 3 cleaned), second run all zeros.

**Lesson**: One fact ("which script does this model use") must have exactly one authoritative source, and **the write side and read side must use the same equivalence test** — read side looks up by `model_path`, write side dedupes by `name`: a textbook "reads one way, writes another" mismatch. Also, the `.bat` is the only source of truth for content; derived fields in json (`model_path`) must be rebuildable from it, or they will inevitably drift.

---

## #39 Unit-test `mkdtemp` never cleaned up: repeated test runs filled the C: drive (489GB)

**Symptom** (fixed incidentally in v1.19.2): `python -m unittest discover -s tests` suddenly failed with `OSError: [Errno 28] No space left on device` (when `tests/test_script_builder.py`'s `_make_model` wrote an 8G fake model). Disk check: only **8.1GB** free on a 953GB C: drive.

**Root cause**:
1. Tests heavily use `tempfile.mkdtemp()` for temp directories **but never delete them** (unittest doesn't auto-reclaim; `mkdtemp` is "borrow, don't return").
2. `test_ctx_tier_by_size` in `tests/test_script_builder.py` **actually writes** 1.5G + 8G fake models each run (`_make_model` uses `seek(size-1)` + writes 1 byte to zero-fill; on NTFS these aren't sparse files, so real disk is consumed) — ~9.5GB leaked per run.
3. This machine accumulated **442 `tmp*` directories, ~489GB total**, draining the disk; the smaller test dirs (a few hundred bytes of .bat/json) are the small-budget version of the same class.

**Fix**:
- Register cleanup immediately after every `tempfile.mkdtemp()`: `self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)` — usable in both `setUp` and test methods, no tearDown or base class needed; covered `tests/test_script_builder.py` (4 spots), `test_script_model_binding.py` (4), `test_script_pin.py`, `test_download_entry.py`, `test_llamacpp_update.py` (2).
- One-shot garbage cleanup: deleted 442 `tmp*` directories containing `.gguf` under `%TEMP%` → freed **478.8GB**, disk restored from 8.1GB to 638GB free.

**Lesson**: Tests that write large files must reclaim their own temp directories, or repeated local runs grow linearly until the disk is full (and the failure looks like "test code error", easily misjudged as a business bug). Criterion: after running unittest, the count of `tmp*` directories containing `.gguf` under `%TEMP%` should be 0 (verified this time).

---

## #40 LogWorker drops the last few lines when a process exits instantly — and the error lines are exactly in the dropped batch

**Symptom**: Running `Ternary-Bonsai-2-27B-PQ2_0.gguf`, the UI log only shows "process started → initializing ... → verbosity = 3 → CORS warning → **process ended**", not a single error; but running the same `.bat` manually in cmd shows `E gguf_init_from_reader: tensor 'output.weight' has invalid ggml type 142` and `E srv llama_server: exiting due to model loading error` (exit code 1) in full.

**Root cause**: `run()` in `ui/workers/log_worker.py` reads only **one line per loop round** (`read_output`), then immediately checks `is_process_alive`, and `break`s if the process is dead. llama-server exits within 0.3~1s on load failure, concentrating the error lines at the end; all unread lines in the pipe buffer are lost. The loop then emits "process ended" as usual, presenting a fake "normal startup then sudden end" log that hides the real failure cause.

**Fix**: Added `_drain_remaining()` — after the process has exited, keep reading the pipe to EOF (`read_output` returning None), capped at `LOG_DRAIN_MAX_LINES = 500` lines, before emitting "process ended"/`finished_signal`. **Only reads when the process is disconnected**: while the process is still alive (e.g., during `stop()` shutdown) no blocking read, to avoid jamming the thread. Regression test `tests/test_log_worker_drain.py` (stub process service: alive N times then dies; asserts leftover lines aren't dropped, order is `…error lines → process ended`, and zero reads while alive).

**Lesson**: In a child-process output loop, "check alive" and "drain the buffer" are two independent conditions — breaking early on liveness is actively discarding data already in the pipe. Anything printed "just before exit" (error lines, exit reasons) lives in the last few lines; shutdown must drain to EOF to see it.

---

## #41 Vendor-private-quantized GGUF: stock llama.cpp refuses to load it (type 142) — you need the vendor fork

**Symptom**: `Ternary-Bonsai-2-27B-PQ2_0.gguf` (ModelScope `prism-ml/Ternary-Bonsai-2-27B-gguf`) fails to load on every llama.cpp version on this machine (b11139, b11149 = current latest):

```
E gguf_init_from_reader: tensor 'output.weight' has invalid ggml type 142. should be in [0, 43)
E llama_model_load: error loading model: failed to load model
E srv llama_server: exiting due to model loading error      (exit code 1)
```

GGUF metadata corroborates a vendor-custom format: `general.architecture = qwen35`, `prism.hadamard.block_size/transform/axis/sign_mode` (rotational-basis Hadamard, needing a paired activation transform at runtime).

**Root cause**: This is PrismML's ternary quantization pack (PQ2_0 / PTQ1_0); tensor type numbers **142/143 exceed mainline llama.cpp's type range (0~42)** — mainline doesn't know the type and necessarily refuses. The model card explicitly says "stock llama.cpp will not run these files; use the PrismML-Eng/llama.cpp fork binaries".

**Measured type numbers** (2026-09-24, HTTP Range to fetch just the GGUF header, per-tensor parse of 851 tensor infos):

| Pack | Size | general.file_type | Tensor type histogram | Stock llama.cpp outcome |
| --- | --- | --- | --- | --- |
| PQ2_0 | 6.71 GB | 142 | 402 tensors of **142**, rest valid | refuses (`invalid ggml type 142`) |
| PTQ1_0 | 5.54 GB | 143 | 402 tensors of **143**, 96 of 30 (BF16), 353 of 0 (F32) | refuses (143 also > 42) |
| F16 | 50.11 GB | 1 | all valid (498 F16 + 353 F32) | **loads but silently hallucinates**: weights are folded into the Hadamard rotation (`general.basename=folded`, `prism.hadamard.*`); stock doesn't recognize this metadata and performs no inverse transform on the activation side |

So "switch to a newer stock llama.cpp" or "use the full-precision pack" are both dead ends — **running this model requires the vendor fork**.

**Fix** (runtime approach, not a bug in this repo):
- Use the vendor fork's prebuilt package: `https://github.com/PrismML-Eng/llama.cpp/releases/latest`; after extracting, point "Path Config → Select llama-server.exe" at that fork's `llama-server.exe` (this app's version management only pulls ggml-org/llama.cpp official releases, doesn't cover fork versions; installing outside `data/llamacpp/` keeps it safe from "clean old versions").
- Fork-side recommended params (model card): `-ngl 99 -fa on -c 32768`, sampling `temp 1.0 / top-p 0.95 / top-k 20` (thinking mode); PQ2_0 behavior on Blackwell/Ada per the model-card table.
- This machine (RTX 5070 Ti Laptop 12GB = Blackwell sm_120): per the model card's pack-selection rule **PQ2_0 is better** (Blackwell/H100/A100 decode faster, prefill faster on all platforms); downloaded; ctx should start at **32768** per the card — 131072 q8_0 KV (~4GB) stacked on 7.2GB weights blows past 12GB VRAM.
- Local note: the `--mmproj` companion is `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf` (optional, image input only).

**Lesson**: "A GGUF of some model" on ModelScope/HF ≠ "any llama.cpp can run it" — vendor-custom quantization (new tensor types, private metadata) requires the vendor's paired runtime. Criterion: when the error contains `invalid ggml type N` with N ≥ 43 (beyond the mainline type table), read the model card's Quickstart/Requirements first — don't waste effort swapping llama.cpp versions; **also be wary of "it loads"** — when types are valid but metadata carries a private transform (like `prism.hadamard.*`), stock builds quietly produce garbage, which is more dangerous than an outright refusal.

---

## #42 After restart, the model dropdown stops at the first item — scanned paths use backslashes, config stores forward slashes

**Symptom**: After the app restarts, the "launch script" model dropdown always stops at the **first item**, while the read-only model path below (and the form params on the right) still show the last-used model — the dropdown and path disagree, making it easy to misjudge "which model is about to run".

**Root cause**: `_reload_model_combo()` in `ui/app.py` locates the current item with `self.model_combo.findData(self.settings.model_path)`, and `findData` is **exact string matching**, with the two sides natively different:
- the dropdown item userData comes from `os.path.join(root_dir, f)` in `service/model_scanner.scan_gguf_files()` → **backslashes** on Windows; and since `root_dir` comes from config (already forward slashes), the actual product is a **mixed** form like `C:/modelscope\MiniCPM5-2B-F16.gguf`;
- `settings.model_path` is normalized by the config setter to **forward slashes** (`C:/modelscope/…`, per the `utils/path_utils` convention).

Match fails → `idx = -1` → `setCurrentIndex` skipped → the dropdown stays at index 0 (first item).

**Fix**: New pure function `combo_index_for(paths, target)` in `ui/app.py` (both sides compared through `normalize_path` + `lower()`; Windows paths are case-insensitive); `_reload_model_combo` uses it, and dropdown userData uniformly stores `normalize_path(f)`. Added a fallback: when the current model isn't in the model directory (deleted/moved), insert "（不在模型目录）<file name>" at the top and select it — guaranteeing the dropdown and path/form **never disagree**, instead of silently stopping at item 0. Regression tests `tests/test_script_model_binding.py::TestComboIndexFor` 4 cases. Measured with real data: old implementation `index = -1`, new `index = 2` (correctly hits `Qwen3.8-27B-UD-IQ2_XXS.gguf`).

**Lesson**: **Whenever "matching UI items by path", both side strings must first pass the same normalization function** — exact comparisons like `findData` / `==` silently fail on Windows due to `\` vs `/` and case differences, and the failure looks like "stuck on item 0" rather than an error, making it extremely hard to spot. Also note: mixed path forms (`C:/a\b`) are themselves a hazard; scanner output should `normalize_path` early. **v1.20.0 supplement**: when local model deletion clears `settings.model_path`, `saved` being empty must still insert a "（未选择模型）" placeholder item and select it — otherwise the dropdown falls back to showing item 0 and disagrees with the empty path (the second trigger path of the same pitfall).

---

## #43 Manually deleting local models leaves dead bindings + a running `.gguf` is locked by Windows

**Symptom**: The user wants to delete an unsuitable local model and can only open Explorer to manually delete it in the folder. Two hazards of manual deletion:
1. The `.gguf` is gone but the launch script pointing at it remains (`data/scripts/*.bat` + `scripts.json` entry) → a "script exists, model doesn't" dead binding, and the ghost stays in the model dropdown / binding lookup;
2. While the model is running, `.gguf` is held by llama-server via mmap, and Windows refuses the delete (`PermissionError [WinError 32]`) — the error message doesn't make the reason intuitive.

**Root cause**: The app previously only offered remote search/download/update-tracking, with no view or deletion entry for local files; and `ScriptService` only had `delete_script` (removed) before v1.19.2, with no "remove binding by model" entry — `migrate_bindings()` only cleans entries whose `.bat` no longer exists, not entries whose model file no longer exists.

**Fix** (v1.20.0):
- New "Local Model" view (`ui/model_tab.py` stack index 3, entry next to the search bar): lists all `.gguf` under the model directory (size/modified time/vision-projection flag), deletable by checkbox or per-row.
- `service/model_scanner.list_local_models` (list) + `service/model_file_service` (`pair_mmproj` pairs vision projections, `delete_model_files` deletes per-item with tolerance).
- `ScriptService.remove_binding_for_model(model_path, names)`: moves `.bat` to `data/scripts_replaced` (not physically deleted, recoverable) + removes the `scripts.json` entry; matching covers three sources — "json entries (including stale ones whose .bat no longer exists) / orphan .bat / derive_name fallback".
- **Blocked while running**: the delete worker first checks `ProcessService.is_running(bound script name)` (that call runs `tasklist`, ~0.6s, so it must live in the worker); if hit, nothing is deleted and the user is told to stop it via "Run Control" first.
- After deletion, the main window cleans up the invalid current selection per the signal (explicit `setText("")` + dropdown refresh, see trap #42).

**Lesson**: **Delete-file features must simultaneously handle "associated data" and "occupancy state"** — deleting only the file leaves dead bindings (more insidious than failing to delete), and reading only `os.remove`'s error turns "file is occupied" into a vague "deletion failed".

---

## #44 Closing a hidden window doesn't trigger app exit — tray "Exit" does nothing, "Restart" leaves the old instance alive

**Symptom**: Tray right-click "Exit" does nothing (process and tray icon remain); clicking "Restart" opens a new instance but **the old one doesn't exit**. When the window is **visible**, exit/restart work perfectly — historical tests always operated with the window open, masking this path; and since the user almost always exits from the tray with the window hidden, it "frequently" reproduced. py-spy evidence (2026-10-08, real machine): the stuck instance's main thread is parked in `main (ui\app.py:1458)` i.e. `sys.exit(app.exec())` — closeEvent cleanup had long finished, **the event loop never returns**. Confusing detail: Task Manager shows **two** pythonw processes in the same launch chain (`.venv\Scripts\pythonw.exe` and the system `pythonw.exe` it spawned) — the venv pythonw is a launcher shell that waits on the child; killing the real child makes the shell exit by itself.

**Root cause**: `_quit_app`/`_restart_app` terminate the process solely via `self.close()`. Qt's `lastWindowClosed` (→ app exit) **only fires when a visible main window is closed**; for an already-hidden (trayed) window, `close()` is delivered normally and closeEvent accepts it (cleanup runs), but the exit chain doesn't trigger — `app.exec()` never returns. A native-platform 2×2 probe confirmed: with the window hidden, close() then exec() hangs in all cases (regardless of running QThreads or the tray icon); with the window visible, it exits instantly. The offscreen platform also hangs (the first probe misjudged "can exit" — an 800ms fallback `app.quit()` was faking success). This pitfall has existed since the tray was introduced in v1.1.0; trap #22 (v1.15.0) fixed the "new instance falsely blocked by the lock" half, while old-instance exit happened to work under visible-window test scenarios and was never exposed.

**Fix** (v1.20.3): at the end of the cleanup branch of `closeEvent` in `ui/app.py`, explicitly call `QApplication.instance().quit()` — semantics hardened to "accepting the close ⇒ the event loop must exit", consistent across tray-hidden / no-tray / visible-window scenarios. Regression tests `tests/test_tray_quit.py` (2 cases, red before the fix / green after): run the real `MainWindow.closeEvent` with dependencies stubbed, asserting ① the forced-exit path requests event-loop exit within the loop (exec returns 0, not the 99 guard fallback); ② the tray-minimize branch must `ignore` and not exit. **Pitfall within the pitfall**: calling `quit()` before `exec()` starts is discarded by Qt (no running loop to quit); tests must use `singleShot(0)` to trigger close inside the loop, matching the real tray-click timing.

**Lesson**:
1. **"close() = quit the app" only holds for visible windows** — tray-style apps' exit paths must explicitly call `QApplication.quit()`/`exit()`, never rely on lastWindowClosed;
2. **Tests validating "app exit"-type behavior must use a distinguishable fallback exit code**, or the fallback fakes "normal exit" (the offscreen probe's initial misjudgment was exactly this).

---

**Status**: #44 fixed in v1.20.3.

---
