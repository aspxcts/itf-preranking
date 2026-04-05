# Key Decisions & Rationale

This document captures major architectural and implementation decisions made during the refactor to GCS + Cloud Scheduler automation.

---

## Decision 1: Remove Login Functionality

**Decided**: April 2026  
**Context**: Migrating from ephemeral per-user sessions to server-side automation

### ✅ Removed
- Frontend login modal and `/api/login`, `/api/logout` endpoints
- Session middleware and cookie-based auth
- User session management in Firestore (`_session_set`, `_session_get`, `_session_delete`)
- UUID-based session tracking

### 🔄 Moved to Server-Side
- ITF authentication: now handled by `src/browser.py` using env var credentials (`ITF_EMAIL`, `ITF_PASSWORD`)
- Warm-up login happens once per Cold Start, credentials cached in Firestore
- No frontend login needed; rankings are public data

### Rationale
✓ Rankings are public (ITF publishes openly)  
✓ Eliminates password storage, session token leakage risk  
✓ Simpler frontend (no CSRF, token refresh, auth state management)  
✓ Server-side warm-up more reliable (persistent Incapsula bypass)  
✓ Removes bottleneck of per-user session creation  

---

## Decision 2: Use GCS Instead of Firestore for Output Files

**Decided**: April 2026  
**Compared**: Firestore vs GCS for storing rankings JSON

### Why GCS Won
| Aspect | Firestore | GCS |
|--------|-----------|-----|
| **Max doc size** | 1 MB (hard limit) | Unlimited |
| **Public sharing** | Requires custom auth flow | Native public URLs |
| **HTTP caching** | Manual implementation | Native ETags, Cache-Control |
| **Cost at scale** | $0.06 per 100k writes | $0.020 per GB storage + $0.04 per 1M GET reqs |
| **Cold start recovery** | Query entire doc | Download file atomic restore |
| **CDN integration** | Custom caching headers | Works out-of-box |

### Architecture Consequences
- Frontend fetches from `https://storage.googleapis.com/itf-preranking-data/` (direct CDN)
- Fallback to `/output/` for local dev
- Cache-Control: `public, max-age=300` (5-minute cache)
- Firestore now only stores: lock + pipeline status (not data files)

---

## Decision 3: Cloud Scheduler vs Alternative Automation

**Decided**: April 2026  
**Options Considered**: Cloud Functions with PubSub, Compute Engine with cron, App Engine cron, Cloud Scheduler

### Why Cloud Scheduler
✓ **Zero management**: No VMs to maintain, no container orchestration  
✓ **OIDC native**: Built-in token generation (no manual JWT signing)  
✓ **HTTP first**: Works directly with Cloud Run (no PubSub broker)  
✓ **Timezone aware**: Native UTC, daylight savings handling  
✓ **Retry policy**: Exponential backoff, max retries configurable  
✓ **Cron syntax**: Standard Unix cron (familiar to ops)  

### Job Configuration
**Refresh**: `0 */12 * * *` (every 12 hours UTC)  
**Sweep**: `0 5 * * 1` (Monday 05:00 UTC)

- Service account: `itf-scheduler` with `roles/run.invoker`
- Attempt deadline: 30 minutes (Cloud Scheduler max)
- Retry: exponential backoff up to 5 doublings

### Why 30-Min Deadline is Safe
- Endpoints return 200 immediately → fire-and-forget pattern
- Background tasks run via `asyncio.create_task()`
- Frontend polls `/api/status` for progress (no blocking)
- Full pipeline typically takes 5-15 min (well under deadline)

---

## Decision 4: Distributed Lock via Firestore

**Decided**: April 2026  
**Problem**: Prevent concurrent pipeline runs if Cloud Run auto-scales or crashes mid-run

### Lock Implementation
```python
_pipeline_lock (Firestore document)
├── owner: <instance_id>
├── acquired_at: <timestamp>
└── expires_at: <timestamp>  # 10 min expiry for crash recovery
```

### Compare-and-Set Pattern
```python
# Acquire: only succeeds if lock doesn't exist
firestore.set(_pipeline_lock, data)  # If exists, raises exception

# Release: delete on success
firestore.delete(_pipeline_lock)
```

### Rationale
✓ **Atomicity**: Firestore transactions ensure no race conditions  
✓ **Crash recovery**: 10-min expiry auto-releases stuck lock  
✓ **No SPOF**: Works across multiple Cloud Run instances  
✓ **Simple**: Single document, no complex state machine  

### Not Considered
✗ Redis lock: Extra service to manage, overkill for single job  
✗ Database advisory locks: Requires managed DB  
✗ In-memory check: Doesn't survive instance restarts  

---

## Decision 5: Background Task Pattern for Pipeline

**Decided**: April 2026  
**Pattern**: `asyncio.create_task()` vs blocking execution vs separate job queue

### Implementation
```python
@app.post("/api/refresh")
async def api_refresh(request: Request):
    _require_scheduler(request)  # Fail if not authenticated
    asyncio.create_task(_do_refresh())  # Fire and forget
    return {"status": "refresh started"}  # Return 200 immediately
```

### Rationale
✓ **Timeout safe**: Scheduler deadline is 30 min (more than safe)  
✓ **Status tracking**: Frontend polls `/api/status` (no blocking)  
✓ **Don't require job queue**: Papermill, Celery would be overkill  
✓ **Cost efficient**: No extra services, runs in Cloud Run memory  

### Frontend Polling
```javascript
// If running, poll every 15s
if (status.refreshing || status.sweeping) {
  setTimeout(checkForUpdates, 15000);
}
```

---

## Decision 6: Status Tracking in Firestore

**Decided**: April 2026  
**Problem**: Frontend needs to know if pipeline is running, what phase, and any errors

### Status Document Schema
```json
{
  "phase": "idle|main|calculate|merge|uploading|error",
  "refreshing": true,
  "sweeping": false,
  "started_at": "2026-04-05T01:56:08Z",
  "last_success_at": "2026-04-05T01:57:58Z",
  "last_error": null,
  "generated_at": "2026-04-05T01:57:58Z"
}
```

### Phase Flow
```
main → calculate → merge → uploading → idle
                                     ↓
                                   error (backward)
```

### Frontend UI Impact
- **Running**: "⚙ Pipeline running: calculating..." (blue badge, 15s poll)
- **Error**: "⚠ Last run failed: GCS upload timeout" (red badge)
- **Idle+Success**: "✅ Data updated April 5, 01:57 UTC" (green badge)

---

## Decision 7: Keep Headless Browser (Don't Use Direct API)

**Decided**: April 2026  
**Rationale**: ITF.Tennis uses Incapsula bot protection

### Why Playwright Necessary
✗ Direct HTTP to ITF APIs: Blocked by Incapsula 403 (even from GCP IPs)  
✓ Headless browser: Passes Incapsula checks, mimics human user  
✓ Login warm-up: Persistent cookies cached in Firestore  

### Warm-Up Flow
1. First Cold Start: Playwright logs in with `ITF_EMAIL`/`ITF_PASSWORD`
2. Extract 11 cookies → save to Firestore relay cache
3. Subsequent requests: Use cached cookies (no re-login)
4. Firestore cache invalidated if 404 detected from ITF

### Cost
- Playwright startup: ~3-5 sec per cold start
- Warm-up login: ~10-15 sec total
- Amortized over 12-hour runs: Negligible

---

## Decision 8: Ephemeral Filesystem with GCS Restore

**Decided**: April 2026  
**Problem**: Cloud Run ephemeral disk lost on instance shutdown

### Solution
**On startup** (`_lifespan()`):
```python
async def _lifespan(app: FastAPI):
    # Cold start: recover from GCS
    await asyncio.to_thread(_gcs_download_sync)
    
    # Download to /output/ if not exists
    # Then frontend queries local /output/ or GCS
```

**On pipeline complete**:
```python
_set_status("uploading")
await asyncio.to_thread(_gcs_upload_sync)  # 3 files to GCS
_set_status("idle")
```

### Why This Works
✓ Frontend can fetch from GCS or `/output/` (both work)  
✓ Cold starts lazy-load from GCS (no data loss)  
✓ Multiple instances don't compete (read-only from GCS)  
✓ Cheap: GCS storage $0.020/GB vs compute for dedicated persistence  

---

## Decision 9: Public GCS Bucket (No Auth)

**Decided**: April 2026  
**Rationale**: Rankings are public data, no authentication needed

### Configuration
- IAM: `allUsers` → `roles/storage.objectViewer`
- CORS: `origin: ["*"]`, `method: ["GET"]`
- Bucket policy: No signed URLs needed

### Security Implications
✓ Rankings are public (published on ITF.tennis openly)  
✓ No sensitive data (just player names, rankings, points)  
✓ Read-only (frontend can't write)  
✗ Anyone can download bulk data (acceptable)  

### If Needed to Restrict Later
- Require API key for frontend: Add Cloud Run authentication
- Use Signed URLs: Cloud Run signs short-lived download links
- Move to private bucket: Frontend fetches from Cloud Run `/output/`

---

## Decision 10: Remove `_auth_endpoint.py` and `server.js`

**Decided**: April 2026  
**Reason**: Vestigial Node.js/Firebase auth infrastructure

### Removed
- `server.js`: Old Node backend (never used)
- `_auth_endpoint.py`: Firebase auth stub
- Frontend auth JS: Login modal, user menu, session state

### Kept
- `app.py`: Single FastAPI server (all logic)
- `index.html`: Frontend (rankings UI + status banner)
- Git history: Commits preserved (can rollback if needed)

### Migration Path
If reverting to user sessions: 
1. Re-add `_session_middleware` and `_LoginBody`
2. Restore login modal to HTML
3. Create new auth endpoints

---

## Decision 11: Comprehensive Status UI for Users

**Decided**: April 2026  
**Feature**: Real-time pipeline progress visible to users

### Frontend Status Banner
Added `#pipeline-status` div in header:
- **Running**: "⚙ Pipeline running: [phase]" → blue badge, 15s poll
- **Error**: "⚠ Last run failed: [error msg]" → red badge
- **Success**: "✅ Data updated [time]" → green badge
- **Stale**: "🕑 Data [days old]" → amber badge

### Data Flow
```
/api/status (Firestore _pipeline_status)
    ↓
Frontend _updateStatusBanner()
    ↓
Display phase, timestamps, GCS URL
```

### User Expectation
- No "refresh button" (scheduler-only)
- See when data was last updated
- Understand why data might be stale
- Know pipeline is running (progress indication)

---

## Decision 12: GitHub Repository Public

**Decided**: April 2026  
**Rationale**: No sensitive data, enables collaboration

### What's in Repo
✓ Source code (app.py, main.py, calculate_rankings.py, etc.)  
✓ Configuration (requirements.txt, Dockerfile, .gitignore)  
✓ Documentation (README.md, ARCHITECTURE.md, DEPLOYMENT.md)  
✓ Git history (all commits preserved)  

### What's NOT in Repo
✗ Credentials (.env, service account keys)  
✗ Output data (/output/, latest JSONs)  
✗ node_modules/ (ignored)  

### CI/CD Opportunity
- GitHub Actions can run tests on push
- Auto-deploy to Cloud Run on tag/merge
- (Not yet implemented, future enhancement)

---

## Future Decisions to Make

1. **Incremental vs Full Refresh**: Currently full recalc every 12h (could cache tournaments)
2. **Data Versioning**: Archive rankings by date (requires DB or dated GCS objects)
3. **Alerts**: Email/Slack on pipeline failure (requires Cloud Run → Cloud Tasks)
4. **API Keys**: If opening /api/refresh to external consumers (Google Cloud API Gateway)
5. **Database**: Move from JSON snapshots to proper database (BigQuery, Postgres)
6. **Multi-Region**: Replicate GCS bucket to eu-central1 for latency (future scale)
7. **Client SDKs**: Document API for integrations (JS, Python libraries)

---

## Lessons Learned

1. **Firestore is great for state** (lock, status), **GCS is great for bulk data** (JSON files)
2. **Background tasks + polling** beats blocking for scheduler patterns
3. **Headless browser warm-up is essential** for Incapsula bypass (no good alternatives)
4. **Public GCS is simpler** than signed URLs for public data
5. **Cloud Scheduler is free for the first 3 jobs** (up to 1.9M invocations/month)
6. **Documentation is critical** when leaving project (ARCHITECTURE.md, DEPLOYMENT.md)
