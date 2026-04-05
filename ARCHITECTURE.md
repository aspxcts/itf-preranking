# ITF Preranking Pipeline — Architecture

## Overview

This is a fully automated **ITF tennis rankings pipeline** deployed on Google Cloud Run with GCS storage, Firestore state management, and Cloud Scheduler automation. The pipeline scrapes live ITF data, calculates player rankings, and serves results via a real-time web UI.

---

## System Components

### 1. Frontend (Web UI)
**File**: `index.html`

- Real-time rankings dashboard with live pipeline status
- Displays:
  - Current ITF rankings (sortable, searchable)
  - Player breakdowns (points composition, recent tournaments)
  - Points earned this week

**Auto-refresh behavior**:
- Fetches `/api/status` on load
- If pipeline is running: shows phase (main/calculate/merge/uploading) and polls every 15s
- If idle: displays data from GCS or local fallback
- Auto-updates when pipeline completes

**Data source**:
- Primary: `https://storage.googleapis.com/itf-preranking-data/` (GCS public URL)
- Fallback: `/output/` (local, for development)

---

### 2. Backend (FastAPI Server)
**File**: `app.py`

Deployed on **Google Cloud Run** (`itf-preranking`, us-central1 region)

#### Core Features:

**Pipeline Orchestration** (4-phase, atomic execution)
1. `main` → Scrape ITF APIs via headless browser
2. `calculate` → Process rankings, compute breakdowns
3. `merge` → Sort and finalize output
4. `uploading` → Push to GCS with 5-min cache headers
5. `idle` → Ready for next run

**State Management** (Firestore-backed)
- `_pipeline_lock`: Distributed lock (prevents concurrent runs)
- `_pipeline_status`: Phase tracking, timestamps, error messages
- Collection: `itf_sessions`

**GCS Integration** (Persistent storage)
- Bucket: `itf-preranking-data` (public-read)
- Files:
  - `latest_merged_rankings.json`
  - `latest_points_earned.json`
  - `latest_player_breakdowns.json`
- Cache-Control: `public, max-age=300` (5 minutes)
- Downloads from GCS on cold start (ephemeral filesystem recovery)

**Scheduler Guard** (Security)
- `_require_scheduler(request)` validates requests
- Accepts: `X-CloudScheduler-JobName` header (set by Cloud Scheduler)
- Alternative: Bearer token with `CRON_SECRET` env var
- Blocks: Direct HTTP calls from users/browsers
- Response: 403 Forbidden if unauthorized

**API Endpoints**:
| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/` | GET | None | Serve index.html |
| `/api/status` | GET | None | Pipeline status (phase, timestamps, GCS URL, running flags) |
| `/api/refresh` | POST | Scheduler | Trigger full data refresh (locked, guarded) |
| `/api/sweep` | POST | Scheduler | Trigger expiry sweep (locked, guarded) |
| `/output/*` | GET | None | Local output files (dev only) |

---

### 3. Browser Automation (Headless Bot)
**File**: `src/browser.py`

Uses **Playwright** to bypass ITF's Incapsula bot protection.

**Warm-up flow**:
1. Authenticate with `ITF_EMAIL`/`ITF_PASSWORD` env vars (server-side only)
2. Relay cookies through Firestore cache for reuse
3. Session persists across cold restarts via cookie cache
4. Anonymous fallback if credentials not set (limited but works)

**APIs scraped**:
- `/tennis/api/PlayerRankApi/GetRankingPoints` → Player rankings
- `/tennis/api/Tournament/GetDrawsheet` → Event draw data
- `/tennis/api/Event/Get` → Event details
- `/tennis/api/Calendar/GetCalendar` → Tournament calendar

**Output**: 3 JSON responses cached for pipeline processing

---

### 4. Pipeline Scripts (Data Processing)
**Files**: `main.py`, `calculate_rankings.py`, `merge_rankings.py`, `expiry_sweep.py`

**Execution flow**:
1. `main.py`: Browser automation + JSON scraping
2. `calculate_rankings.py`: Rating calculations, point aggregation per player
3. `merge_rankings.py`: Sort, finalize, output 3 JSON files to `output/`
4. `expiry_sweep.py`: Remove inactive players (called weekly)

**Output structure** (JSON):
```json
{
  "week_start": "2026-03-30",
  "generated_at": "2026-04-05T01:57:58Z",
  "players": {
    "800707189": {
      "name": "Player Name",
      "nationality": "RSA",
      "gender": "B",
      "current_rank": 105,
      "current_points": 562.5,
      "current_week_singles": [30.0],
      "current_week_doubles": [45.0],
      "singles_countable": [...],
      "singles_non_countable": [...],
      "doubles_countable": [...],
      "doubles_non_countable": [...]
    }
  }
}
```

---

## Deployment Architecture

### Cloud Infrastructure
- **Compute**: Google Cloud Run (managed containers)
  - Service: `itf-preranking`
  - Region: `us-central1`
  - Latest revision: `itf-preranking-00033-b9f`
  - Memory: 512MB (default)
  - Timeout: 3600s (1h)
  
- **Storage**:
  - **GCS Bucket**: `itf-preranking-data` (public, CORS enabled)
  - **Firestore**: `itf_sessions` collection
  - **Ephemeral disk**: Pipeline temp files (/output, /tmp)

- **Automation**: Google Cloud Scheduler
  - `itf-refresh`: Every 12 hours (`0 */12 * * * UTC`)
  - `itf-expiry-sweep`: Every Monday 00:05 UTC (`0 5 * * 1`)
  - Both use OIDC tokens (service account: `itf-scheduler`)
  - Attempt deadline: 30 minutes

### Environment Variables (Cloud Run)
```
GCS_BUCKET=itf-preranking-data
FIRESTORE_PROJECT_ID=itf-live-rankings
ITF_EMAIL=<your-itf-email>           (optional, for authenticated warm-up)
ITF_PASSWORD=<your-itf-password>     (optional)
CRON_SECRET=<random-token>           (optional, additional security)
```

### CORS Configuration (GCS)
```json
[
  {
    "origin": ["*"],
    "method": ["GET"],
    "responseHeader": ["Content-Type", "Cache-Control"],
    "maxAgeSeconds": 3600
  }
]
```

---

## Data Flow Diagram

```
┌─────────────────────┐
│  Cloud Scheduler    │ (every 12h + Monday)
│   (itf-refresh)     │
└──────────┬──────────┘
           │ POST /api/refresh (with OIDC token)
           ▼
┌─────────────────────────────────────────────────┐
│        Google Cloud Run (itf-preranking)        │
├─────────────────────────────────────────────────┤
│  app.py (FastAPI)                               │
│  ├─ _require_scheduler()  [Guard: X-CloudScheduler-JobName]
│  ├─ _do_refresh()         [Orchestrate 4 phases]
│  └─ _gcs_upload_sync()    [Push to GCS]
│                                                 │
│  Firestore (itf_sessions)                       │
│  ├─ _pipeline_lock        [Distributed lock]    │
│  └─ _pipeline_status      [Phase tracking]      │
│                                                 │
│  src/browser.py (Playwright)                    │
│  └─ Warm-up login + cookie relay [Incapsula bypass]
│                                                 │
│  Pipeline scripts                               │
│  ├─ main.py               [Scrape ITF APIs]
│  ├─ calculate_rankings.py [Compute ratings]
│  └─ merge_rankings.py     [Finalize output]
└────────┬──────────────────────────────────────┘
         │
         ▼
    ┌─────────────────────┐
    │  GCS Bucket         │ (public-read, 5-min cache)
    │ itf-preranking-data │
    │                     │
    │ • latest_merged_rankings.json
    │ • latest_points_earned.json
    │ • latest_player_breakdowns.json
    └────────┬────────────┘
             │ HTTPS GET (public URL)
             ▼
    ┌──────────────────────┐
    │  Browser (Frontend)  │
    │  index.html          │
    └──────────────────────┘
```

---

## Security Model

### Authentication
- **Pipeline triggers**: OIDC service account tokens (Cloud Scheduler only)
- **Public endpoints**: `/api/status`, `/`, `/output/` (anonymous, read-only)
- **Frontend**: No login required (anonymous)

### Authorization
- **Scheduler endpoints** (`/api/refresh`, `/api/sweep`): 
  - Guarded by `_require_scheduler(request)`
  - Checks: `X-CloudScheduler-JobName` header OR Bearer `{CRON_SECRET}`
  - Blocks: All direct HTTP calls (403 Forbidden)

- **GCS bucket**: Public-read for rankings data, no write from browser

- **Firestore**: 
  - Cloud Run service account has read/write
  - No direct client access (backend-only)

### Network
- Cloud Run service is publicly accessible (HTTP)
- GCS bucket is public (CORS enabled for `https://` origins)
- Firestore access is backend-only (Cloud Run's service account)

---

## Key Design Decisions

### 1. Why GCS instead of Firestore for output?
- **No 1MB doc size limit**: Firestore documents limited to 1MB; GCS can handle multi-MB JSON
- **Simpler public sharing**: GCS public URLs require one-time setup; Firestore requires custom auth
- **Cost-effective**: Storage is cheaper than Firestore read/write ops at scale
- **HTTP caching**: GCS handles ETags, Cache-Control headers natively

### 2. Why background tasks (`asyncio.create_task`)?
- Scheduler timeout is 30 min; pipeline may take longer
- Return 200 immediately so scheduler doesn't retry
- Frontend polls `/api/status` to track progress (no long polling)

### 3. Why distributed lock (Firestore)?
- Prevents concurrent pipeline runs if Cloud Run auto-scales or restarts mid-run
- Ensures data consistency (no overlapping writes to GCS)
- Simple to implement with Firestore compare-and-set semantics

### 4. Why no user login?
- Rankings are public data (ITF publishes openly)
- Server-side browser automation authenticates once (env vars)
- Simplifies frontend (no session management, CSRF, TokenRefresh)
- Reduces attack surface (no password storage, token leakage)

### 5. Why Playwright headless browser?
- ITF uses Incapsula bot protection; direct API calls blocked
- Browser automation + warm-up cookies bypass it reliably
- Login credentials stored server-side (not exposed to frontend)
- Can be replaced with different strategy later if ITF changes

---

## Monitoring & Debugging

### Status Endpoint (`GET /api/status`)
Response example:
```json
{
  "refreshing": false,
  "sweeping": false,
  "phase": "idle",
  "started_at": "2026-04-05T01:56:08.933917Z",
  "last_success_at": "2026-04-05T01:57:58.670542Z",
  "last_error": null,
  "generated_at": "2026-04-05T01:57:58.034071Z",
  "gcs_url": "https://storage.googleapis.com/itf-preranking-data"
}
```

### Logs
- **Cloud Logging**: `gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=itf-preranking"`
- **Local logs**: `python app.py` outputs to stdout (FastAPI + print statements)

### GCS File Verification
```bash
gcloud storage ls gs://itf-preranking-data/
gcloud storage cat gs://itf-preranking-data/latest_merged_rankings.json | head -50
```

### Firestore State
```bash
gcloud firestore documents get itf_sessions/_pipeline_status --project=itf-live-rankings
```

---

## Failure Recovery

### Pipeline Fails Mid-Run
1. Distributed lock prevents concurrent restart
2. `_pipeline_status.phase` = "error" with `last_error` msg
3. Frontend displays warning banner
4. Next scheduler job retries in 12 hours (or manually via Cloud Console)

### GCS Unavailable
1. `_gcs_upload_sync()` fails → phase stays "error"
2. Frontend falls back to local `/output/` (if cold-start downloaded successfully)
3. Error logged to Firestore + Cloud Logging

### Cold Start (Ephemeral Disk Lost)
1. `_lifespan()` runs on startup
2. Downloads 3 JSONs from GCS → repopulate `/output/`
3. Ready to serve frontend immediately

### Scheduler Job Fails
1. Retry policy: exponential backoff (5s min, 3600s max)
2. Max 5 doublings before giving up
3. Error logged to Cloud Logging
4. Frontend shows last error in status banner

---

## Future Improvements

1. **Email notifications** on pipeline success/failure (Cloud Tasks + Cloud Run POST)
2. **Metric dashboards** (Cloud Monitoring for pipeline duration, errors, data freshness)
3. **Data versioning** (archive old rankings snapshots by date in GCS)
4. **Incremental updates** (only recalculate changed tournaments, not full refresh)
5. **API key protection** (if opening `/api/refresh` to programmatic consumers)
6. **Database** (structured rankings history instead of JSON snapshots)
7. **Multi-region deployment** (replicated GCS buckets for latency)

---

## References

- **ITF Tennis**: https://www.itftennis.com
- **Google Cloud Run**: https://cloud.google.com/run
- **Google Cloud Storage**: https://cloud.google.com/storage
- **Google Cloud Firestore**: https://cloud.google.com/firestore
- **Google Cloud Scheduler**: https://cloud.google.com/scheduler
- **Playwright**: https://playwright.dev
