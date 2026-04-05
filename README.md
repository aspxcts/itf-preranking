# ITF Tennis Rankings Pipeline

Automated pipeline for scraping, calculating, and serving ITF tennis rankings with a modern web UI.

## Architecture

### Frontend
- **`index.html`**: Real-time rankings dashboard with live pipeline status
- Fetches data from GCS, auto-updates on pipeline completion
- Shows running phase, error states, last update timestamp

### Backend
- **`app.py`**: FastAPI server on Google Cloud Run
  - Pipeline orchestration (4 phases: main → calculate → merge → upload)
  - GCS integration for persistent storage and public CDN
  - Firestore-backed status tracking and distributed locking
  - Scheduler guard: only Cloud Scheduler can trigger refresh/sweep
- **`src/browser.py`**: Headless Playwright browser
  - Bypasses Incapsula bot protection via warm-up login
  - Firestore cookie caching for session relay

### Pipeline Scripts
- **`main.py`**: Scrapes ITF APIs (rankings, draw sheets, events)
- **`calculate_rankings.py`**: Calculates player ratings and breakdowns
- **`merge_rankings.py`**: Merges and sorts rankings, outputs final JSON
- **`expiry_sweep.py`**: Removes expired player records

### Storage
- **GCS bucket** (`itf-preranking-data`): Public-read JSON files, 5-min cache
- **Firestore**:
  - `itf_sessions/_pipeline_lock`: Distributed lock
  - `itf_sessions/_pipeline_status`: Phase tracking, timestamps, errors

### Automation
- **Cloud Scheduler**:
  - `itf-refresh`: Every 12 hours → full refresh + GCS upload
  - `itf-expiry-sweep`: Every Monday 00:05 UTC → clean expired + upload

## Quick Start

### Local Development

1. Clone and set up:
   ```bash
   git clone https://github.com/aspxcts/itf-preranking
   cd itf-preranking
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   ```

2. Set environment variables:
   ```bash
   export FIRESTORE_PROJECT_ID=itf-live-rankings
   export FIRESTORE_EMULATOR_HOST=localhost:8080  # optional, for local testing
   export ITF_EMAIL=<your-email>              # optional, for authenticated warm-up
   export ITF_PASSWORD=<your-password>        # optional
   ```

3. Run the pipeline steps individually:
   ```bash
   python main.py
   python calculate_rankings.py
   python merge_rankings.py
   ```

4. Start the web server:
   ```bash
   python app.py
   # Visit http://localhost:8000
   ```

### Cloud Deployment

Deploy to Cloud Run:
```bash
gcloud run deploy itf-preranking \
  --project itf-live-rankings \
  --region us-central1 \
  --source . \
  --set-env-vars GCS_BUCKET=itf-preranking-data,FIRESTORE_PROJECT_ID=itf-live-rankings
```

Scheduler jobs trigger automatically every 12h (refresh) and Monday (sweep).

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/` | GET | None | Web UI |
| `/api/status` | GET | None | Pipeline status (phase, timestamp, GCS URL) |
| `/api/refresh` | POST | Scheduler | Trigger full refresh |
| `/api/sweep` | POST | Scheduler | Trigger expiry sweep |
| `/output/*` | GET | None | Local output files (dev fallback) |

## Status Format

```json
{
  "refreshing": false,
  "sweeping": false,
  "phase": "idle",
  "started_at": "2026-04-05T01:56:08Z",
  "last_success_at": "2026-04-05T01:57:58Z",
  "last_error": null,
  "generated_at": "2026-04-05T01:57:58Z",
  "gcs_url": "https://storage.googleapis.com/itf-preranking-data"
}
```

## Files

- **Core pipeline**: `main.py`, `calculate_rankings.py`, `merge_rankings.py`, `expiry_sweep.py`
- **Web server**: `app.py`, `index.html`
- **Browser automation**: `src/browser.py`
- **Configuration**: `requirements.txt`, `Dockerfile`, `.env` (local only)
- **API scrapers**: `getPlayerRankings.js`, `getEvent.js`, `getDrawsheet.js`, `getCalendar.js` (reference)

## Notes

- The pipeline runs entirely server-side; users never trigger it manually
- Output files are served from GCS (public, cached 5 min)
- Firestore stores lock/state; GCS stores rankings data
- All timestamps in UTC
- Incapsula bypass requires `ITF_EMAIL`/`ITF_PASSWORD` on Cloud Run

## License

(Add your license here)
