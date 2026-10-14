# LlamaCPP GUI

A PyQt6 desktop client built for [llama.cpp](https://github.com/ggml-org/llama.cpp). Manage your local LLM inference servers without touching the command line.

**Windows 10/11 only.**

## Features

| Feature | Description |
|---------|-------------|
| 🚀 **llama-server management** | Configure paths in the UI, generate launch scripts from parameter templates, run/stop with one click; **picking a model auto-generates parameters** (scripts are bound to a model, no standalone script names) + **form-based script editing** (fill-in / dropdown / checkbox, with a collapsible view of the raw `.bat`) + **Reset Parameters** (discard saved tuning and regenerate a default script); **running-model list** — multiple models can run simultaneously, each row shows uptime and an individual "Stop" button, a top-level "Stop All" halts every running model |
| 🔍 **Model search & download** | Integrated with ModelScope and hf-mirror.com — search models, browse GGUF files, resume interrupted downloads; when not searching, the tab defaults to an **"Update Tracker" view** — automatically watches locally installed model series plus manually tracked search results; a background job compares remote `LastUpdatedTime` and highlights rows with newer versions in yellow; each row has a "View Files" button that jumps straight to that model's file list for download; **local model management** — a toggle next to the search bar (default label "Local Model Management" / becomes "← Back" inside the stacked view at index 3), listing every `.gguf` under the model directory (size / modified time / vision-projection flag) with permanent deletion (confirmation dialog lists each file and total size, cleans up the bound launch script by moving the `.bat` into `data/scripts_replaced`, and removes the paired mmproj in the same directory; deletion is blocked while the model is running) |
| 📊 **Performance monitoring** | Compact CPU/RAM/NVIDIA GPU live monitoring embedded in the main control page, plus inference speed t/s (historical samples persisted to `data/history/`, kept 7 days) |
| 💬 **Chat interface** | One click to open the Web chat UI in your browser once the server is ready |
| 🤖 **AI chat backend** | HTTP bridge service with multi-role conversations, long-term memory, and scheduled task dispatch |
| 🛡 **System tray** | Closing the window minimizes to the tray; tray menu shows / exits / toggles autostart |
| 🚀 **Autostart** | One-click register/unregister for startup (HKCU Run; runs `pythonw` from source, the packaged exe after build) |
| 🗂 **Local model indexing** | Recursively scans a configured local directory for `.gguf`, quick-pick a model from a dropdown — no manual selection needed |
| 🌐 **Tailscale remote access** | New scripts can set `--host` to "local only / all interfaces / Tailscale only" (auto-detects the local Tailscale IP); shows the external URL once ready with one-click copy |
| 🔄 **llama.cpp version management** | Update detection (compares local build number against the latest GitHub release), build-variant recommendation based on GPU driver (CUDA 12.4 / 13.x / CPU / Vulkan, etc.), resumable download & install (direct or custom mirror prefix), versioned directories with one-click switch & rollback (automatically rewrites launch-script paths), and silent background download of the latest version on startup (current channel + recommended variant; ready for one-click switch when you open the page) |

### Supported inference parameters

| Category | Parameters |
|----------|-----------|
| General | `--gpu-layers` GPU layers, `--port` port, `--ctx-size` context size (tiered dropdown generated from the model's GGUF metadata cap, 8K~1M; if the cap is <128K the model maximum is the default, ≥128K defaults to 128K; any value can be typed manually), `--alias` alias, `--host` listen mode (local only / all interfaces / Tailscale only) |
| Model | `--mmproj` vision model, `--reasoning off` disable thinking, `--main-gpu` primary GPU, `-ts` multi-GPU split |
| MTP | `--spec-type` prediction type, `--spec-draft-n-max` draft length |
| Quantization | `--cache-type-k`, `--cache-type-v` KV cache quantization |
| MOE | `--n-cpu-moe` CPU threads, `--no-mmap-fallback` disable fallback |

## Quick Start

```bash
git clone https://github.com/kkblank/Llamacpp-gui.git
cd Llamacpp-gui
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Build a Standalone EXE

```powershell
pip install pyinstaller
pyinstaller -D -w --name='Lammacpp启动器' --add-data "ui/chat_webui;data/webui" main.py
```

Output lands in `dist/Lammacpp启动器/`.

## First Use

1. **Configure paths** — on the main control page, pick `llama-server.exe` and a model directory (scans `.gguf` recursively)
2. **Pick a model to generate a script** — selecting a model auto-generates base parameters (the script is bound to the model, no standalone script name; the model alias auto-derives from the file name); fine-tune in the common-parameters form and click "Save" to bind; click "Reset Parameters" to discard saved tuning and restore factory defaults; ctx is pre-set to a sensible tier from the model's cap (switch tiers from the dropdown or type any value); advanced parameters (concurrency / MTP / MOE) live in the collapsible "Advanced Parameters" section at the bottom of the form
3. **Start the server** — click "Run" (runs the script bound to the current model) and watch the log window
4. **Chat** — once the server is ready, click "Chat Window" to open the Web UI in your browser
5. **Search models** — switch to the "Model Search & Download" tab, choose ModelScope or the Hugging Face mirror to search
6. **Monitor** — a compact CPU/GPU/TPS monitor is embedded at the bottom-right of the main control page (side by side with the log)

## Data Files

The app creates its runtime data under `data/`:

| Location | Description |
|----------|-------------|
| `data/app_config.json` | Paths and other settings |
| `data/scripts.json` + `data/scripts/*.bat` | Launch scripts |
| `data/scripts_replaced/` | Backups of `.bat` retired during binding cleanup (not deleted, recoverable) |
| `data/downloads/queue.json` | Download queue state |
| `data/chat/` | Chat data (conversations, agents, memory, scheduled tasks) |
| `data/last_pid.pid` | PID of a running server from older versions (fallback only under the multi-server architecture) |
| `data/history/` | Daily JSONL samples of t/s inference speed (kept 7 days) |
| `data/app.lock` | Single-instance lock (prevents concurrent GUI instances) |
| `data/webui/` | Chat frontend static files |
| `data/llamacpp/` | Default root for llama.cpp version management: one directory per version `{tag}-{variant}/` (configurable to another drive), `zips/` holds the download cache (resumable) |

## Project Structure

```
main.py
├── config/                 config + path constants
├── model/                  pure data models (ScriptEntry, DownloadEntry)
├── service/                business logic layer
│   ├── script_builder.py   launch-script parameter templates and .bat generation
│   ├── script_service.py   script CRUD
│   ├── process_service.py  process lifecycle management
│   ├── tailscale.py        Tailscale IP detection (external access)
│   ├── llamacpp_update_service.py  llama.cpp version management (detect/download/install/switch)
│   ├── modelscope.py       ModelScope API client
│   ├── model_scanner.py    local model scan (.gguf list: size/mtime/mmproj flag)
│   ├── model_file_service.py  local model file deletion (per-item tolerance + paired mmproj)
│   ├── watchlist_service.py   update-tracker watchlist (with ignored-set persistence)
│   ├── download_service.py download manager
│   └── monitor_service.py  system resource sampling
├── chat/                   self-contained chat backend subsystem
├── ui/                     UI layer
│   ├── app.py              main window
│   ├── model_tab.py        model search & download tab (update-tracker default view + local model file management view)
│   ├── model_watch_tab.py  ModelWatchView (embedded in model_tab, shown when not searching)
│   ├── monitor_tab.py      CompactMonitor (embedded in the main control page)
│   ├── update_tab.py       llama.cpp version management tab
│   ├── dialogs/            dialogs
│   └── workers/            background workers (log/status polling/search/version install/update tracking/local model scan & delete/Tailscale probe/cleanup planning/process cleanup)
├── tests/                  smoke tests (unittest, runnable offscreen)
└── utils/                  common utilities (logger, validator, atomic_io, path_utils, semver)
```

## Path Conventions (Key Design Decisions)

- All paths are stored and displayed in a single canonical form using **forward slashes `/`** (`C:/modelscope/...`).
- Why: consistent across platforms (POSIX-native), no JSON escaping, Qt/QFileDialog natively returns forward slashes, and Windows file APIs / cmd / llama-server are fully compatible.
- Normalization is enforced at every **write point**: config setters, script generation (`script_builder`), and version switching (`replace_bat_dir`) all go through `utils/path_utils.normalize_path()`; anything produced by `os.path.join` etc. (which yields `\` on Windows) must be normalized before it is stored.
- Scripts are bound to models: `ScriptEntry.derive_name(model_path)` derives the script name from the model path (file name + short path hash, so same-name models in different directories don't collide); `script_service.get_script_for_model` looks up by normalized model path. Any "locate file by script" logic must use the real file name from `ScriptEntry.derive_name` / `sanitize_filename` (see traps #17).
- **One model binds exactly one script** (invariant, enforced since v1.19.0): on the read side, `get_script_for_model` picks among multiple historical candidates for the same `model_path` by `name_model_score` (token overlap between script name and model file name) > pinned > most recently saved; on the write side, `_upsert_config_entry` treats the same normalized `model_path` as the same entry (renaming a saved script merges it and cleans up the old `.bat`); on startup, `migrate_bindings()` uses the `.bat`'s `-m` as the source of truth to correct `model_path`, merges duplicate bindings (moving retired `.bat` files to `data/scripts_replaced`), removes ghost entries, and is idempotent (see traps #38). **The `.bat` is the only source of truth for script content**; derived fields in `scripts.json` must be rebuildable from it.

## Tests

```powershell
.venv\Scripts\python.exe -m unittest discover tests
```

## System Requirements

- Windows 10/11
- Python 3.10+
- Dependencies: PyQt6, psutil, nvidia-ml-py, Pillow

## License

MIT License
