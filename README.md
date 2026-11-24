# X Timeline Extractor (Playwright Implementation)
It extracts posts from **one or more X accounts** within a specified date range. 
It consists of a Python (FastAPI) backend, a React (Vite) frontend, and **a single extraction mechanism: Playwright**.
An authenticated browser instance navigates to the search page (or the profile timeline) and collects rendered content directly.
The React application enforces **protected routes**: all pages except `/login` require an active session, and any `401 Unauthorized` response from the API redirects the user to the login screen.

> **Disclaimer:** 
This is an educational project. 
Automated data extraction violates X's Terms of Service and may result in account limitations or suspension. 
Operate this tool exclusively on a dedicated test account, maintain low request volumes, and avoid using it with primary or sensitive accounts.

---

## Quick Start (macOS, Linux, WSL)

```bash
./run.sh setup      # Create venv, install dependencies, install Chromium, initialize backend/.env, run npm install
./run.sh session    # Authenticate with X once to save the browser profile
./run.sh start      # Launch the application at [http://127.0.0.1:8000](http://127.0.0.1:8000)
```

The `run.sh` script serves as a convenience wrapper around the manual commands documented below. 

|------------------|----------------------------------------------------------------------------------------------------|
| Command          | Description                                                                                        |
|------------------|----------------------------------------------------------------------------------------------------|
| ./run.sh setup   | Creates backend/.venv, installs backend and frontend dependencies, and copies .env.example to .env.|
| ./run.sh session | Launches a browser window to authenticate with X and saves the session to .x_profile/.             |
| ./run.sh start   | Builds frontend/dist if absent and serves the application on port 8000.                            |
| ./run.sh dev     | Runs the backend and Vite development server concurrently (Ctrl-C terminates both).                |
| ./run.sh test    | Executes backend tests, the frontend production build, and browser smoke tests.                    |
| ./run.sh mongo   | Reports the status and contents of the history database (check_mongo.py).                          |
|------------------|----------------------------------------------------------------------------------------------------|

---

## Setup on Windows

### 1. Backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Frontend (Separate Terminal)

```powershell
cd frontend
npm install
```

---

## Configuration: `.env` File
Credentials should be stored in a dedicated configuration file rather than entered in shell history. 
Copy the template file once:

```powershell
cd backend
copy .env.example .env        # cp .env.example .env on macOS / Linux
```

Edit `backend/.env` to specify the application password, MongoDB URI, and operational parameters:

```ini
XTIMELINE_PASSWORD=something-only-you-know
XTIMELINE_MONGO_URI=mongodb+srv://user:pass@cluster0.xxxxx.mongodb.net/xtimeline?retryWrites=true
XTIMELINE_STORE=auto
XTIMELINE_SCROLL_DELAY_MS=1200
```

Upon startup, `run.py` logs the configuration status on the first line:

```
[env] using .env (11 variable(s) applied)
```

---

## Running the Application

### Step 1 — Save an Authenticated Session (One-Time Setup)

```powershell
cd backend
.venv\Scripts\activate
python save_session.py
```

A browser window will open. 
Sign in to X, wait until your timeline is visible, and then press `Enter` in the terminal. 
This saves `.x_profile/` (the persistent browser profile) and `x_state.json` (session cookies). 
Re-execute this script whenever the application encounters an authentication wall.

### Step 2 — Start the Backend

```powershell
cd backend
python run.py
```

The server listens on <http://127.0.0.1:8000> and logs a warning if a custom password has not been configured.

### Step 3 — Start the Frontend (Two Options)

**Development Mode** (with hot module replacement; Vite proxies `/api` requests to port 8000):

```powershell
cd frontend
npm run dev
```

Access the application at <http://localhost:5173>.

**Single-Origin Production Mode** (the backend serves the compiled frontend, ensuring client-side routes such as `/history` persist across page refreshes):

```powershell
cd frontend
npm run build
```

Access the application directly at <http://127.0.0.1:8000>. 
Both frontend and backend operate on a single port without a proxy.

### Step 4 — Sign In

The login password corresponds to `XTIMELINE_PASSWORD`. 
If unset, it defaults to `changeme` (a startup warning is logged in this case).

```powershell
$env:XTIMELINE_PASSWORD = "something-only-you-know"
python run.py
```

After authenticating, enter a handle into the **Username** field and press `Enter` (or click **Add**) to add it to **Accounts to extract**. 
Multiple handles may be added individually or pasted as a comma-separated list. 
Select the extraction strategy, specify a date range and a **Max posts / account** limit, and initiate the extraction. 
Monitor per-account progress in real time, then inspect the collected posts or select **Download JSON**.

The list of accounts is persisted in `localStorage` for subsequent sessions.

---

## User Interface

The interface is styled as a printed technical instrument rather than a conventional dashboard, featuring a near-white background, hairline dividers instead of cards, monospace typography for labels and metrics, italic serif type for annotations, and a single blue accent color.

**Dashboard** (`/dashboard`) displays aggregate archive data rather than a single job:

- **Posts per day, by account** — A zero-based stacked bar chart supporting `7d`, `30d`, and `All` time windows. Days without posts are represented as axis ticks rather than empty bars.
- **Store & session** and **Accounts extracted** — Two summary panels in the right column. The first reports the active extractor, history store, database cluster, age of the saved X session, and authentication token expiration, accompanied by a one-line status summary. The second lists all queried usernames alongside their total run counts, becoming scrollable beyond approximately five entries.
- **Post history** — Includes per-account tabs, a weekly post volume chart (covering the last 9 weeks), a likes-versus-views scatter plot (one point per post), sorting controls (by date, likes, or views in either direction, plus From/To date filters), and full X-style post cards.

Aggregate counts are retrieved from **`GET /api/stats`**, which computes totals server-side across both active in-memory jobs and MongoDB records. Individual post cards are populated from the 20 most recent runs; a footnote on the page clarifies this distinction.

Adding a handle triggers an asynchronous verification request (`POST /api/accounts/check`) to confirm whether the account exists on X. Unrecognized accounts are struck through in red with the corresponding reason logged in the outbox, preventing unnecessary browser execution. Because verification involves a profile visit, it runs asynchronously without blocking user input.

**Workbench** (`/`) is organized into a three-column layout: the `NEW EXTRACTION` form, the `ACCOUNTS` ledger (constrained to 415px height with scrolling to preserve page layout), and the `OUTBOX` terminal. 
The outbox logs execution events—including session age, account progress, errors, and recommended remediation—and persists above the route hierarchy so navigating to Results or History does not clear the log. 
Beneath the deck, a status bar displays progress, a **Cancel** control during active runs, and a **Results** button that appears once data has been collected.

![Workbench](screenshots/workbench.png)

**Results** (`/jobs/<id>`) is a dedicated view featuring `POST TYPE` filters, `DOWNLOAD CSV`, job metadata, and `ACCOUNTS / PICK ANY` in the left sidebar, with extracted posts listed on the right. 
Each entry displays the avatar, display name, verification badge, handle, post text with active links, attached media, timestamp, and a bordered metrics table (replies, reposts, likes, saved, views) alongside an `OPEN ON X ↗` link. 
Metrics are read-only; CSV export includes only the currently filtered posts.

![Result](screenshots/result.png)


**History** presents a job ledger with dotted rows, failed runs highlighted in red, and an `OPEN` column to revisit any previous job's results.

A **Day / Night** theme toggle is located in the masthead and persisted across sessions. The outbox remains dark in both themes.

---

## Architecture and Mechanism

```
/login  ──password──▶  POST /api/auth/login  ──▶  signed bearer token (12 h)
                                                     │  localStorage
        ┌────────────────────────────────────────────┘
        ▼
  <ProtectedRoute>  ──no/invalid token──▶  redirect /login  (remembers the URL)
        │ token ok
        ▼
  POST /api/scrape ──▶ FastAPI ──▶ Playwright: search page ──▶ harvest loop
        ▲                             │  one browser per job,
        └── SSE progress ◀───────────┘  one page per account
```

X **virtualizes** its timeline: only a small subset of posts exists in the DOM at any given moment, and DOM nodes are recycled during scrolling. 
Consequently, the extractor cannot retrieve all posts from a single page load. 
Instead, it scrolls incrementally and collects rendered posts—deduplicated by post ID—until it passes the start of the target date range or reaches the per-account post limit.


### Extraction Strategies

| `strategy` | Target Page | Recommended Use Case |
| --- | --- | --- |
| `search` (default) | `x.com/search?q=from:handle since:… until:…&f=live` | Fastest and most accurate; X performs date filtering server-side. |
| `profile` | `x.com/handle`, scrolled chronologically backwards | Useful when search behaves inconsistently, or when an exact representation of the profile timeline is required. |
| `auto` | Attempts `search`, falling back to `profile` if no results render | Provides automated fallback redundancy. |

---

## Protected Routes

Route protection is enforced by `frontend/src/auth/ProtectedRoute.jsx`, with authentication state managed in `AuthContext`:

- `/login` is publicly accessible; all other routes are wrapped in `<ProtectedRoute>`.
- Requests without a valid session redirect via `<Navigate to="/login" state={{ from: location }}>`, preserving the intended destination URL for post-login redirection.
- While a stored token is being verified, a brief session-check indicator is displayed to prevent flickering of the login page on page reload.
- Any API response returning `401 Unauthorized` dispatches `xtimeline:unauthorized`, prompting `AuthContext` to clear the token and redirect the user. Expired tokens therefore trigger logout upon the next API request.
- Because `EventSource` (SSE) and `<a download>` cannot attach custom HTTP headers, these endpoints transmit the token via a query parameter (`?token=…`). All other requests use the `Authorization: Bearer` header.

On the backend, `require_auth` accepts either token delivery method and validates both the HMAC-SHA256 signature and expiration timestamp. The signing key is sourced from `XTIMELINE_SECRET` or generated automatically into `backend/.auth_secret` so tokens remain valid across server restarts. Failed authentication attempts are rate-limited to 5 per IP address per minute.

---

## API Reference

All endpoints require authentication except `/api/health`, `/api/auth/login`, and `/api/auth/logout`.

|--------|------------------------------|--------------------------------------------------------------------------|
| Method | Path                         | Description                                                              |
|--------|------------------------------|--------------------------------------------------------------------------|
| POST   | /api/auth/login              | Accepts {password} and returns {token, expires_at}.                      |
| GET    | /api/auth/me                 | Verifies whether the current token is valid.                             |
| POST   | /api/auth/logout             | No-op endpoint (tokens are stateless and signed, not stored server-side).|
| POST   | /api/scrape                  | Initiates an extraction job and returns {job_id}.                        |
| GET    | /api/jobs                    | Lists recent jobs in reverse chronological order                         |
| GET    | /api/jobs/{id}               | Returns a status snapshot for a single job.                              |
| POST   | /api/jobs/{id}/cancel        | Terminates an active job while retaining posts collected thus far.       |
| GET    | /api/jobs/{id}/result        | Returns the complete list of extracted posts for a job.                  |
| GET    | /api/jobs/{id}/download      | Downloads the job result as a JSON file.                                 |
| GET    | /api/jobs/{id}/events        | Streams real-time job progress via Server-Sent Events (SSE).             |
| GET    | /api/jobs/{id}/debug         | Returns a diagnostic screenshot captured by the browser.                 |
| GET    | /api/session                 | Reports whether a saved X session exists and its age.                    |
| GET    | /api/health                  | Public liveness probe.                                                   |
|--------|------------------------------|--------------------------------------------------------------------------|


### Scrape Request Payload

```json
{
  "handles": ["EconomicTimes", "Reuters", "bbcworld"],
  "start": "2026-10-13T00:00:00Z",
  "end": "2026-10-20T23:59:59Z",
  "strategy": "search",
  "limit": 200
}
```
|-------------------|------------------------------------------------------------------------------------------|
| Field             | Description                                                                              |
|-------------------|------------------------------------------------------------------------------------------|
| handles           | One or more target accounts (array of strings, or a comma/space-delimited string).       |
| start / end       | Inclusive date range in ISO 8601 format.                                                 |
| strategy          | "search" (default), "profile", or "auto".                                                |
| limit             | Maximum posts retained per account (default 500; 0 disables the limit).                  |
| max_scrolls       | Maximum scroll iterations per account (default 60).                                      |
| headed            | Launches a visible browser window to facilitate debugging.                               |
| include_retweets  | Retains reposts of other users' content (default false).                                 |
|-------------------|------------------------------------------------------------------------------------------|


Supplying the deprecated `method` field (formerly used for `twscrape`) is **rejected** with a `422 Unprocessable Entity` response rather than silently ignored.

### Post Object Schema

```json
{
  "id": "1234567890",
  "datetime": "2026-02-11T09:14:00+00:00",
  "text": "...",
  "url": "[https://x.com/handle/status/1234567890](https://x.com/handle/status/1234567890)",
  "author": "@handle",
  "account": "handle",
  "pinned": false,
  "is_repost": false,
  "metrics": { "replies": 4, "reposts": 12, "likes": 240, "bookmarks": 7 }
}
```

The `account` field records the requested handle associated with each post, enabling multi-account exports to be partitioned cleanly.

### Multi-Account Execution

Accounts are processed sequentially within a **single browser instance** , separated by a randomized pause. 
If an individual account encounters an error (e.g., expired session, protected status, rate limiting), 
the error is recorded for that account and execution proceeds to the next. 
The overall job is marked as failed only if *all* accounts fail. 
The `limit` parameter applies individually to each account rather than to the combined batch.

---

## Persistence with MongoDB

Active jobs reside in memory to serve the SSE stream and accommodate real-time updates from the browser task. 
Every state transition is simultaneously **upserted into MongoDB**, 
ensuring that job history persists across restarts and past results remain accessible at `/jobs/<id>`.

```
mongod                      # Start a local instance, or configure a remote server
python run.py
```

The startup log indicates the active storage backend, which is also exposed via `/api/health`:

```
{"ok":true, ..., "store":"mongo"}
```
|----------------------|-----------------------------------|--------------------------------------------------|
| Variable             | Default                           | Description                                      |
|----------------------|-----------------------------------|--------------------------------------------------|
| XTIMELINE_MONGO_URI  | mongodb://127.0.0.1:27017         | MongoDB connection string.                       |
| XTIMELINE_MONGO_DB   | xtimeline                         | Target database name.                            |
| XTIMELINE_STORE      | auto                              | Storage mode: auto | mongo | memory.             |
|----------------------|-----------------------------------|--------------------------------------------------|

In `auto` mode, the application attempts to connect to MongoDB and falls back to in-memory storage with a warning if unavailable. 
Setting `mongo` enforces database availability as a startup requirement; `memory` bypasses MongoDB entirely.
Both local `mongod` instances and MongoDB Atlas clusters are supported by adjusting the connection string. 

For Atlas, 
provision a free M0 cluster, configure a database user, allowlist your IP address under Network Access, and place the `mongodb+srv://…` URI in `backend/.env`. 
Run `python check_mongo.py` to verify connectivity and inspect stored records. 

Two collections are maintained:

```
jobs     One document per job — id, handles, strategy, status, progress, collected, message, error, note, per_account, timestamps
tweets   One document per job — {job_id, tweets:[...]}
```

Posts are stored separately from job metadata to avoid approaching MongoDB's 16 MB document size limit during large multi-account runs.

API persistence behavior:
- `GET /api/jobs` merges active in-memory jobs with stored MongoDB records, sorted newest first and capped at 100 entries.
- `GET /api/jobs/{id}` and `/result` fall back to MongoDB when a job is absent from memory (e.g., after a server restart).
- `/download` regenerates the JSON file from MongoDB if `output/` has been cleared.
- `/events` emits a single snapshot and closes the stream for archived jobs, ensuring uniform behavior across live and historical job views.
- Attempting to cancel an archived job returns `409 Conflict`.

Database credentials within the URI are redacted prior to logging.

---

## Tuning Parameters

All parameters below are configured via environment variables in `backend/.env` 
 Default values are intentionally conservative.
|-----------------------------------------|----------------------------------|------------------------------------------------------------------|
| Variable                                | Default                          | Description                                                      |
|-----------------------------------------|----------------------------------|------------------------------------------------------------------|
| XTIMELINE_PASSWORD                      | changeme                         | Application login password.                                      |
| XTIMELINE_SECRET                        | Auto-generated file              | HMAC token signing key.                                          |
| XTIMELINE_TOKEN_TTL_MIN                 | 720                              | Token validity duration in minutes.                              |
| XTIMELINE_HOST / XTIMELINE_PORT         | 127.0.0.1 / 8000                 | API bind address and port.                                       |
| XTIMELINE_CHROME_PATH                   | Playwright Chromium              | Path to a custom Chrome or Edge executable.                      |
| XTIMELINE_USER_AGENT                    | Desktop Chrome UA                | Custom User-Agent header override.                               |
| XTIMELINE_MONGO_URI / _DB               | Local mongod / xtimeline         | MongoDB connection URI and database name.                        |
| XTIMELINE_STORE                         | auto                             | Storage backend mode (auto, mongo, or memory).                   |
| XTIMELINE_NO_CHANNEL                    | Disabled                         | Set to 1 to force the bundled headless shell.                    |
| XTIMELINE_NO_SANDBOX                    | Disabled                         | Set to 1 in containerized environments requiring --no-sandbox.   |
| XTIMELINE_SCROLL_DELAY_MS               | 1200                             | Delay in milliseconds after each scroll (increase if rate-       |
|                                         |                                  | limited).                                                        |
| XTIMELINE_NAV_TIMEOUT_MS                | 60000                            | Page navigation timeout; slow loads are treated as non-fatal.    |
| XTIMELINE_WAIT_TWEETS_MS                | 20000                            | Maximum wait time for the timeline DOM to render.                |
| XTIMELINE_ACCOUNT_PAUSE_MIN_MS / _MAX_MS| 1500 / 3500                      | Randomized delay range between sequential accounts.              |
|-----------------------------------------|----------------------------------|------------------------------------------------------------------|


**Adjusting post volume:** Configure **Max posts / account** (`limit`); a value of `0` removes the cap. When using the `profile` strategy, consider increasing **Max scrolls** as well. The loop terminates automatically once it encounters consecutive timestamps preceding the start date or reaches the per-account limit.

---

## Project Layout

```
xtimeline-playwright/
├── backend/
│   ├── app/
│   │   ├── main.py         FastAPI application: auth, /scrape, jobs, SSE, download, SPA serving
│   │   ├── auth.py         Signed tokens, require_auth dependency, login rate-limiting
│   │   ├── scraper.py      Playwright extractor and harvest loop
│   │   ├── jobs.py         In-memory active job store
│   │   ├── persistence.py  MongoDB write-through layer for job history
│   │   └── models.py       Request and response schemas
│   ├── save_session.py     One-time login utility -> .x_profile/ + x_state.json
│   ├── check_mongo.py      Diagnostic script for MongoDB connectivity and contents
│   ├── check_session.py    Diagnostic script to verify saved session validity
│   ├── run.py              Uvicorn launcher configured with a Playwright-compatible event loop
│   ├── tests/              58 automated tests (API, auth, DOM harvest, full browser execution)
│   └── requirements.txt
└── frontend/
    └── src/
        ├── App.jsx         Route configuration (public /login, protected application routes)
        ├── auth/           AuthContext and ProtectedRoute
        ├── pages/          Login, Dashboard, History, Job
        ├── components/     Masthead, Layout, Workbench, Accounts, Log,
        │                   Results, Entry, VolumeChart, StoreBox,
        │                   AccountsBox, PostHistory
        ├── outbox/         OutboxContext (persistent log above route hierarchy)
        ├── lib/            Formatting utilities, CSV export, dashboard statistics, archive fetch hook
        ├── theme/          ThemeContext (Day / Night theme management)
        └── api.js          Fetch wrapper that attaches authentication tokens
```

---

## Testing

```powershell
cd backend
pip install -r requirements-dev.txt   # Installs pytest, pytest-asyncio, httpx
python -m pytest                      # Runs 58 unit/integration tests (no X login required)
python tests/test_session_race.py     # Tests session verification against redirect race conditions
python tests/smoke_ui.py              # Runs 35 browser UI checks (requires `npm run build` first)
```

`requirements-dev.txt` extends `requirements.txt` with test-only dependencies, keeping the production dependency list minimal.

- `test_auth.py` — Verifies route protection, token validation, rejection of tampered or expired tokens, `?token=` query authentication, and login throttling.
- `test_jobs.py` — Tests the job lifecycle with a stubbed browser: multi-account merging, per-account error isolation, limits, cancellation, downloads, history, and input validation.
- `test_harvest.py` — Validates the DOM harvest loop against a local mock of X's markup: date boundaries, pinned posts, reposts, advertisements, metrics parsing, deduplication, and caps.
- `test_extractor.py` — Exercises the full Playwright pipeline against a local mock server: browser launch, navigation, scrolling, harvesting, login-wall detection, cancellation, and missing-session handling.
- `tests/test_session_race.py` — Verifies `save_session.py` against mid-probe page redirects (addressing the "Execution context was destroyed" error).
- `tests/smoke_ui.py` — Tests the compiled frontend in a real browser: protected-route redirects, authentication success/failure, deep-link preservation, page reloads, SSE progress, job permalinks, tampered tokens, and sign-out behavior.

---

## Troubleshooting

Known failure modes are surfaced per account within the user interface:

| Symptom | Cause | Resolution |
| --- | --- | --- |
| `SessionExpired` / login wall | The saved X session is no longer valid. | Re-run `python save_session.py`. |
| "Confirm your account" | X has issued an interactive verification challenge. | Re-run `save_session.py` and complete the challenge. |
| `RateLimited` / "Something went wrong. Try reloading." | X is throttling requests. | Wait before retrying, increase `XTIMELINE_SCROLL_DELAY_MS`, and reduce volume. |
| `AccountUnavailable` | The account is protected, suspended, or misspelled. | Verify the handle spelling and account visibility. |
| 0 posts, no error | The page rendered without posts. | Select **View debug screenshot** and verify the date range. |

- **`Execution context was destroyed, most likely because of a navigation`** (in `save_session.py` immediately after pressing `Enter`) — X was mid-redirect when the script inspected the DOM, invalidating the execution context. This represents a timing race rather than an invalid session. The script now waits for redirects to settle, retries the check, and saves the session regardless if navigation does not stabilize. If encountered on an older version, re-run `save_session.py`.
- **Zero posts with the note "nothing rendered on the search page"** — The page loaded successfully, but X did not mount a timeline, typically due to headless browser detection. The extractor now applies a standard desktop Chrome User-Agent and prefers Chromium's new headless mode over the legacy `HeadlessChrome` shell. If the issue persists, enable **Show browser window** for the run.
- **Full tracebacks** — Complete stack traces for any failure are printed to the terminal running `run.py` and should be consulted first during diagnosis.
- **Blank white page / empty title** — X returned an empty shell due to browser fingerprinting. The extractor reuses the persistent profile created by `save_session.py`; if this proves insufficient, enable **Show browser window** and retry.
- **`NotImplementedError` from `_make_subprocess_transport` (Windows)** — The backend is executing on a `SelectorEventLoop`, which does not support spawning Playwright's subprocess driver. Always start the server using `python run.py` rather than `uvicorn app.main:app`, and do not pass `--reload`.
- **Pinned posts** — Pinned posts appear out of chronological order at the top of a timeline. They are identified via `data-testid="socialContext"` and excluded from date-boundary checks so they do not prematurely terminate extraction.
- **Discrepancies in metrics** — Engagement metrics are parsed from button `aria-label` attributes (e.g., `"42 replies, 10 reposts, 300 likes"`) rather than abbreviated display text.
- **Standalone session verification** — Run `python check_session.py` to launch a visible browser, print a session validity verdict, and save a diagnostic screenshot to `output/session_check.png`.

---

## Additional Notes

- `x_state.json` and `.x_profile/` contain active authentication credentials and must be treated as sensitive secrets. Both are listed in `.gitignore` and should never be committed to version control.
- Active jobs are maintained in memory and mirrored to MongoDB (see [Persistence with MongoDB](#persistence-with-mongodb)) so job history survives restarts. Without a database, the application operates normally in memory-only mode, which is noted in the outbox.
- `backend/output/` stores exported job JSON files and diagnostic screenshots.
