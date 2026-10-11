# Sample React + Python Project

A reusable full-stack template with a React/Vite frontend and FastAPI backend.
The example application is a persistent click counter:

- **Click** sends a request to Python, which increments the count atomically.
- The count is stored in SQLite, so closing the browser or stopping the server
  does not erase it.
- **Clear count** resets the stored value to zero.
- The frontend reloads the current value whenever it starts.

## Quick start

Windows:

```powershell
.\setup.bat
.\run.bat
```

Linux/macOS:

```bash
./setup.sh
./run.sh
```

If a Python virtual environment or Conda environment is already active, setup
installs into that environment and run uses the same interpreter. Otherwise,
the scripts create and reuse `.venv` in this project.

The default backend and frontend ports are `8000` and `5173`. The runner checks
both ports before launch and advances to the next available port when needed.
It prints the actual URLs selected for the session.

## LAN access

The development runner and portable Windows Release build listen on `0.0.0.0`
by default, making the frontend and API available on the host's network
interfaces. Start either launcher and open a printed `LAN` frontend URL on
another device connected to the same network. The Debug build defaults to
`127.0.0.1` for local-only access:

```powershell
.\run.bat
# Or, after building:
.\dist\Release\run.bat --frontend 4000 --backend 9000
```

Startup lists each detected IPv4 address with the selected ports, for example:

```text
Starting Sample React + Python Full-Stack Services
Backend:  http://127.0.0.1:9000 (API docs: http://127.0.0.1:9000/docs)
Frontend: http://localhost:4000
LAN:      http://192.168.1.156:4000
LAN API:  http://192.168.1.156:9000/docs
LAN:      http://192.168.56.1:4000
LAN API:  http://192.168.56.1:9000/docs
Open a LAN URL on another device connected to the same network.
```

Use the address of the adapter connected to your other device; virtual-machine
adapters may also appear. The frontend forwards `/api` requests to the backend
on the host, so remote browsers work without IP-specific frontend configuration.
If Windows Firewall blocks access, allow the running Python interpreter or
`server.exe` on your private network. Wi-Fi guest networks or client isolation
can also prevent devices from reaching each other.

For local-only access, use `--host 127.0.0.1` with either launcher or set
`BACKEND_HOST=127.0.0.1` in the relevant `.env`. To bind a specific adapter,
use `--host 192.168.1.156` (replace it with your host's address). Command-line
`--host` takes precedence over `BACKEND_HOST`. Existing `.env` files retain
their settings; change an existing loopback value to `0.0.0.0` to enable LAN.

## Configuration

Copy `.env.example` to `.env` to customize the preferred ports, bind address,
or database path. Environment variables already defined in the terminal take
precedence over values in `.env`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKEND_PORT` | `8000` | First backend port to try |
| `FRONTEND_PORT` | `5173` | First frontend port to try |
| `BACKEND_HOST` | `0.0.0.0` | Bind address for both frontend and backend |
| `COUNTER_DB_PATH` | `data/counter.db` | Persistent SQLite database |

## API

| Method | Path | Behavior |
| --- | --- | --- |
| `GET` | `/api/health` | Backend health check |
| `GET` | `/api/counter` | Read the saved count |
| `POST` | `/api/counter/click` | Increment and return the count |
| `DELETE` | `/api/counter` | Reset and return the count |

Interactive API documentation is available at the backend `/docs` URL.

## Tests and build

Build a portable Windows distribution (requires Python 3.10+ and Node.js/npm
on the build machine):

```powershell
.\build.bat
# Equivalent: python build.py
```

The build installs dependencies in the active Python environment, or creates
and uses the project `.venv`, then builds React and packages FastAPI with
PyInstaller. Use `build.bat --skip-install` to reuse installed dependencies.

```text
dist/
  Debug/                  Local version (default host: 127.0.0.1)
    public/               Built frontend HTML, JavaScript, and CSS
    server.exe            Standalone backend and frontend static server
    run.bat               Distribution launcher
    runtime.json          Generated default bind address
  Release/                LAN version (default host: 0.0.0.0)
    public/
    server.exe
    run.bat
    runtime.json
```

Both folders contain the same packaged application, with different default
bind addresses selected by their generated `runtime.json` files. Copy the entire
chosen `Debug` or `Release` folder to another Windows machine; Python and Node.js
are not required there. Start it with:

```powershell
.\dist\Debug\run.bat
.\dist\Release\run.bat
.\dist\Release\run.bat --frontend 4000 --backend 9000
```

Both distributions default to frontend port `3000` and backend port `8000`.
Debug binds to `127.0.0.1` for local access; Release binds to `0.0.0.0` for LAN
access.
`--frontend-port` and `--backend-port` are also accepted. Ports must be distinct,
available integers between 1 and 65535. Open the printed frontend URL, and
press Ctrl+C in the launcher terminal to stop both services.

The executable serves `public` and forwards frontend `/api` requests to the
selected backend port, so changing ports does not require rebuilding React.
An optional `.env` beside each `server.exe` supports `FRONTEND_PORT`,
`BACKEND_PORT`, `BACKEND_HOST`, and `COUNTER_DB_PATH`; command-line ports take
precedence. For the bind address, `--host` takes precedence over `BACKEND_HOST`,
which takes precedence over the generated `runtime.json` default. The counter
database defaults to `data/counter.db` in each distribution folder. Relative
database paths resolve beside `server.exe`, regardless of the launcher's working
directory. Each folder has separate settings and counter data. Rebuilding
replaces `public`, `server.exe`, `run.bat`, and `runtime.json` while preserving
each folder's existing data and `.env`.

Development checks:

```powershell
python -m unittest discover -s backend\tests -v
cd frontend
npm run typecheck
npm run build
cd ..\e2e
npm run typecheck
npm test
```

The Playwright suite starts both FastAPI and Vite automatically, then exercises
the live API through the frontend proxy using an isolated test database. It
uses Microsoft Edge by default. On macOS it uses Playwright's WebKit-based
Desktop Safari profile, which is the supported automation equivalent for
Safari rendering.

On a fresh macOS checkout, install the WebKit browser once before running the
tests:

```bash
cd e2e
npm run install:webkit
```

## Project layout

```text
backend/                  FastAPI application, SQLite store, and unit tests
frontend/                 TypeScript React/Vite application
e2e/                      Standalone TypeScript Playwright end-to-end tests
data/                     Runtime database location (database is ignored)
setup.py / setup.*        Cross-platform dependency setup
run.py / run.*            Combined backend/frontend runner
build.py / build.bat      Portable Windows distribution builder
```
