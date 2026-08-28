<div align="center">

# nginx-manager

Lightweight web-based nginx manager · Zero third-party dependencies · Ready-to-use single-file exe for Windows

[简体中文](README.md) ｜ English

</div>

---

## Features

- **Config file management**: tree view + online editing; the original file is backed up automatically before saving and validated with `nginx -t`, with one-click rollback on failure
- **Reverse proxy management**: visually switch `proxy_pass` targets (multiple candidates, aliases, search by path/address); changes are written directly to nginx.conf and validated automatically
- **Target address pool**: manages all proxy_pass targets in one place; equivalent spellings (case/default port/trailing slash) are deduplicated automatically
- **System picker dialogs**: a native dialog selects nginx on first launch; every path input in the UI has a "Browse…" button — no manual typing, no typos
- **Movable data directory**: the location of settings.json and backups can be changed in the UI, with automatic migration and restart
- **Preview mode**: explore the UI and test the API without nginx installed; switches to normal mode once configured
- **8 themes**: 4 color schemes × dark/light; the frontend loads zero external resources and works fully offline
- **Security**: binds to `127.0.0.1` only, CSRF protection on write operations, path traversal checks, `shell=False` command execution

## Quick Start

### Windows (recommended)

1. Download `nginx-manager-vX.X.X.exe` from [Releases](../../releases)
2. Run it — your browser opens `http://127.0.0.1:8310` automatically
3. On first launch, use the native dialogs to pick the nginx executable (e.g. `C:\nginx\nginx.exe`) and the config directory (containing `nginx.conf`)

### Run from source

Requires Python 3.10+ (the first-run picker and "Browse…" buttons rely on the standard-library tkinter)

```bash
git clone <this repo>
cd nginx-manager
python backend/server.py             # default port 8310; falls back to a random free port if taken
```

### Command-line options

| Option | Description |
|---|---|
| `--port` | Listen port, default `8310` |
| `--nginx-path` | Path to the nginx executable (skips the first-run dialog) |
| `--conf-dir` | nginx config directory containing nginx.conf (skips the first-run dialog) |
| `--preview` | Preview mode: nginx installation/configuration not required |
| `--data-dir` | Manager data directory (highest priority; the UI lock edit when set) |

## Data Directory

Runtime data (settings.json, backups/, instance lock) is stored by default in:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\nginx-manager` |
| macOS | `~/Library/Application Support/nginx-manager` |
| Linux | `~/.config/nginx-manager` |

You can override it with the `--data-dir` option or the `NGINX_MANAGER_DATA_DIR` environment variable, or change it in the Settings dialog (settings and backups are migrated automatically, then the service restarts).

## Access via an nginx Reverse Proxy

The service listens on `127.0.0.1:8310` only and can be exposed through an nginx reverse proxy under the `/nginx-manager/` prefix (the frontend adapts to the path prefix automatically):

```nginx
location /nginx-manager/ {
    proxy_pass http://127.0.0.1:8310/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

## Building from Source (Windows exe)

```bash
python build.py        # produces dist/nginx-manager-v{version}.exe (single file)
```

Note: the build Python must ship with tcl/tk (build.py fails hard otherwise), otherwise the packaged exe cannot show native picker dialogs.

## Security Notes

The service has no login/authentication (single-user, local-machine scenario). **Do not expose the port to the public internet.** See [SECURITY_AUDIT.md](SECURITY_AUDIT.md) for the security design.

## Documentation

- [API.md](API.md) — frontend/backend contract (single source of truth for the API) (Chinese)
- [VIBE_CODING_GUIDE.md](VIBE_CODING_GUIDE.md) — project conventions and development guide (Chinese)
- [SECURITY_AUDIT.md](SECURITY_AUDIT.md) — security audit (Chinese)
