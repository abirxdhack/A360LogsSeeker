# 🔍 A360LogsSeeker

An ultra-fast async credential & log search API built with **FastAPI**, **uvloop**, and **ripgrep**. Searches massive log databases in milliseconds — extracts combos, credentials, and raw lines with zero blocking I/O.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Latest-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![uvloop](https://img.shields.io/badge/uvloop-Powered-FF6B35?style=for-the-badge)](https://github.com/MagicStack/uvloop)
[![ripgrep](https://img.shields.io/badge/ripgrep-Search-red?style=for-the-badge)](https://github.com/BurntSushi/ripgrep)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

---

## ✨ Features

- ⚡ **uvloop + httptools** — Fastest possible async event loop, beats standard asyncio by 2-4x
- 🔎 **ripgrep-powered search** — Searches gigabytes of log files in under a second
- 🧵 **Non-blocking I/O** — True async subprocess execution, zero thread blocking
- 🧹 **Smart deduplication** — Case-insensitive dedup with sorted output every response
- 🔌 **Auto-loading plugin system** — Drop a `.py` in `/plugins`, it registers itself automatically
- 🌐 **Real IP detection** — Prints actual server IP and port on boot, not just `0.0.0.0`
- 📦 **Lifespan-based startup** — No deprecated `on_event`, clean modern FastAPI lifespan
- 🛡️ **Global error shielding** — All unhandled errors return clean JSON, never raw tracebacks
- 📊 **Unified response metadata** — Every endpoint returns `api_owner`, `api_dev`, `api_version`, `time_taken`, `total_lines`, `duplicates_removed`

---

## 🧰 Requirements

- Python **3.11+**
- **ripgrep** installed on the server
- See `requirements.txt` for Python dependencies

---

## ⚙️ System Setup

```bash
sudo apt update
sudo apt install ripgrep -y
```

Verify ripgrep is working:

```bash
rg --version
```

---

## 📦 Installation

```bash
git clone https://github.com/abirxdhack/A360LogsSeeker
cd A360LogsSeeker
python3 -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt
```

---

## 📁 Add Your Data

Place your `.txt` log files inside the `/data` directory:

```bash
mkdir -p data
cp yourlogfile.txt data/
```

The API auto-discovers all `.txt` files in `/data` at request time — no restart needed after adding files.

---

## ▶️ Run Server

```bash
python3 main.py
```

Or with uvicorn directly:

```bash
uvicorn main:application --host 0.0.0.0 --port 8000
```

Custom port:

```bash
PORT=9000 python3 main.py
```

Multi-worker mode (production):

```bash
PORT=8000 WORKERS=4 python3 main.py
```

Hot reload (development only):

```bash
RELOAD=true python3 main.py
```

Server boots and prints:

```
A360LogsSeek v2.3.68 — live at http://192.168.x.x:8000
API docs — http://192.168.x.x:8000/docs
```

---

## 🗂️ Project Structure

```
A360LogsSeeker/
├── main.py                  — App entry point, lifespan, middleware, error handlers
├── requirements.txt         — Python dependencies
├── Procfile                 — Deployment process file
│
├── utils/
│   ├── __init__.py          — Re-exports all engine symbols
│   └── engine.py            — Core engine: thread pool, ripgrep runner, dedup, response builder
│
├── plugins/
│   ├── __init__.py          — Package marker
│   ├── cmb.py               — /cmb  endpoint logic
│   ├── extr.py              — /extr endpoint logic
│   └── ulp.py               — /ulp  endpoint logic
│
├── data/
│   └── *.txt                — Your log files go here
│
└── static/
    └── index.html           — Served at GET /
```

---

## 🔌 API Endpoints

### 🏠 Homepage

```
GET /
```

Serves `static/index.html`.

---

### 🔑 Combo Search

Searches for `user:pass` or `email:pass` combos matching a site keyword.

```
GET /cmb?site=example.com
```

**Query Parameters:**

| Parameter | Type   | Required | Description                   |
|-----------|--------|----------|-------------------------------|
| `site`    | string | ✅        | Domain or keyword to search   |

**Response:**

```json
{
  "site": "example.com",
  "combos": [
    "user@example.com:password123",
    "john@example.com:securepass"
  ],
  "api_owner": "@ISmartCoder",
  "api_dev": "@abirxdhackz",
  "api_version": "2.3.68",
  "time_taken": "84.21ms",
  "total_lines": 2,
  "duplicates_removed": 5
}
```

---

### 🧲 Pattern Extraction

Extracts structured data from logs by format type.

```
GET /extr?site=example.com&format=mailpass
```

**Query Parameters:**

| Parameter | Type   | Required | Description                                           |
|-----------|--------|----------|-------------------------------------------------------|
| `site`    | string | ✅        | Domain or keyword to search                           |
| `format`  | string | ✅        | Output format — see supported values below            |

**Supported Formats:**

| Format      | Description                                      | Example Output                        |
|-------------|--------------------------------------------------|---------------------------------------|
| `mailpass`  | Email + password pairs                           | `user@site.com:pass123`               |
| `userpass`  | Username + password pairs                        | `john_doe:pass123`                    |
| `num_pass`  | Phone number + password pairs                    | `+1234567890:pass123`                 |
| `domain`    | Bare domain names                                | `example.com`                         |
| `url`       | Full HTTP/HTTPS URLs                             | `https://example.com/login`           |

**Response:**

```json
{
  "site": "example.com",
  "format": "mailpass",
  "matches": [
    "admin@example.com:admin123",
    "user@example.com:qwerty"
  ],
  "api_owner": "@ISmartCoder",
  "api_dev": "@abirxdhackz",
  "api_version": "2.3.68",
  "time_taken": "61.08ms",
  "total_lines": 2,
  "duplicates_removed": 3
}
```

---

### 📄 ULP Raw Line Search

Returns full raw log lines matching a site keyword — URL, login, and password together.

```
GET /ulp?site=example.com
```

**Query Parameters:**

| Parameter | Type   | Required | Description                   |
|-----------|--------|----------|-------------------------------|
| `site`    | string | ✅        | Domain or keyword to search   |

**Response:**

```json
{
  "site": "example.com",
  "lines": [
    "https://example.com/login:user@mail.com:password1",
    "https://example.com/login:john:secretpass"
  ],
  "api_owner": "@ISmartCoder",
  "api_dev": "@abirxdhackz",
  "api_version": "2.3.68",
  "time_taken": "38.55ms",
  "total_lines": 2,
  "duplicates_removed": 1
}
```

---

## 📖 Interactive Docs

FastAPI auto-generates full interactive documentation:

```
http://localhost:8000/docs       — Swagger UI
http://localhost:8000/redoc      — ReDoc UI
```

---

## 🌍 Environment Variables

| Variable  | Default  | Description                                    |
|-----------|----------|------------------------------------------------|
| `PORT`    | `8000`   | Port the server binds to                       |
| `WORKERS` | `1`      | Number of uvicorn worker processes             |
| `RELOAD`  | `false`  | Enable hot reload — forces 1 worker when true  |

---

## ⚠️ Notes

- All `.txt` files inside `/data` are searched automatically — just drop files in and query
- ripgrep **must** be installed system-wide (`apt install ripgrep`)
- `WORKERS > 1` is recommended for production with large datasets
- Hot reload (`RELOAD=true`) is for development only — never use in production
- The `/data` directory is not included in the repo — you supply your own log files

---

## 👤 Credits

- 👨‍💻 API Owner: **@ISmartCoder**
- 🛠️ API Dev: **@abirxdhackz**
- 📢 Updates Channel: [TheSmartDev](https://t.me/TheSmartDev)