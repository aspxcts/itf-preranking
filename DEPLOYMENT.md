# ITF Preranking — Deployment Guide

## Current Status

**Latest Deployment**: `itf-preranking-00033-b9f` (April 5, 2026)

- **Project**: `itf-live-rankings` (Google Cloud)
- **Region**: `us-central1`
- **GCS Bucket**: `itf-preranking-data` (public, CORS enabled)
- **Firestore**: `itf_sessions` collection
- **Scheduler Jobs**: 2 active jobs (12h refresh + weekly sweep)

---

## Prerequisites

### Local Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set up Google Cloud SDK
gcloud init
gcloud auth login
gcloud config set project itf-live-rankings
```

### GCP Resources (One-time Setup)

- ✅ **Cloud Run service**: `itf-preranking`
- ✅ **GCS bucket**: `itf-preranking-data` (public, CORS)
- ✅ **Firestore database**: `itf_sessions` collection
- ✅ **Service account**: `itf-scheduler` (Cloud Scheduler invoker)
- ✅ **Cloud Scheduler jobs**: `itf-refresh`, `itf-expiry-sweep`

---

## Environment Variables

### Cloud Run Service

```bash
gcloud run services describe itf-preranking --region us-central1 --format="value(spec.template.spec.containers[0].env)"
```

**Required env vars**:

```
GCS_BUCKET=itf-preranking-data
FIRESTORE_PROJECT_ID=itf-live-rankings
```

**Optional env vars** (set if Incapsula warm-up needed):

```
ITF_EMAIL=your-itf-email@example.com
ITF_PASSWORD=your-itf-password
CRON_SECRET=random-bearer-token  (for additional security)
```

**Update environment variables**:

```bash
gcloud run services update itf-preranking \
  --project itf-live-rankings \
  --region us-central1 \
  --set-env-vars "ITF_EMAIL=...,ITF_PASSWORD=..."
```

---

## Deployment Process

### Option 1: Deploy from Source (Recommended)

```bash
cd d:\bolts\itf_preranking

# Ensure all changes are committed to git
git status

# Deploy to Cloud Run
gcloud run deploy itf-preranking \
  --project itf-live-rankings \
  --region us-central1 \
  --source . \
  --set-env-vars "GCS_BUCKET=itf-preranking-data,FIRESTORE_PROJECT_ID=itf-live-rankings" \
  --quiet
```

**What happens**:

1. Builds Docker image (from `Dockerfile`)
2. Pushes to Google Container Registry
3. Deploys as new Cloud Run revision
4. Routes 100% traffic to new revision
5. Keeps previous revisions available for rollback

### Option 2: Deploy from Container Registry

```bash
# Build and push manually
gcloud builds submit --tag gcr.io/itf-live-rankings/itf-preranking:latest

# Deploy the image
gcloud run deploy itf-preranking \
  --project itf-live-rankings \
  --region us-central1 \
  --image gcr.io/itf-live-rankings/itf-preranking:latest
```

### Verify Deployment

```bash
# Check service status
gcloud run services describe itf-preranking --region us-central1

# Check recent revisions
gcloud run revisions list --service itf-preranking --region us-central1

# Test the service
curl https://itf-preranking-609418294401.us-central1.run.app/api/status
```

---

## Cloud Scheduler Configuration

### Job 1: Full Refresh (Every 12 Hours)

**Name**: `itf-refresh`

```bash
gcloud scheduler jobs describe itf-refresh --location us-central1
```

**Configuration**:

- Schedule: `0 */12 * * *` (every 12 hours, UTC)
- HTTP method: POST
- URI: `https://itf-preranking-609418294401.us-central1.run.app/api/refresh`
- Service account: `itf-scheduler@itf-live-rankings.iam.gserviceaccount.com`
- OIDC token audience: `https://itf-preranking-609418294401.us-central1.run.app/`
- Attempt deadline: 1800s (30 minutes)

**Test run**:

```bash
gcloud scheduler jobs run itf-refresh --location us-central1

# Check status
gcloud scheduler jobs describe itf-refresh --location us-central1 --format="value(status,lastAttemptTime)"
```

### Job 2: Expiry Sweep (Weekly, Monday)

**Name**: `itf-expiry-sweep`

```bash
gcloud scheduler jobs describe itf-expiry-sweep --location us-central1
```

**Configuration**:

- Schedule: `0 5 * * 1` (every Monday 05:00 UTC)
- HTTP method: POST
- URI: `https://itf-preranking-609418294401.us-central1.run.app/api/sweep`
- Service account: `itf-scheduler@itf-live-rankings.iam.gserviceaccount.com`
- OIDC token audience: `https://itf-preranking-609418294401.us-central1.run.app/`
- Attempt deadline: 1800s (30 minutes)

**Test run**:

```bash
gcloud scheduler jobs run itf-expiry-sweep --location us-central1
```

### Modify Scheduler Job

```bash
# Update schedule
gcloud scheduler jobs update http itf-refresh \
  --location us-central1 \
  --schedule "0 */6 * * *"  # Change to every 6 hours

# Update URI
gcloud scheduler jobs update http itf-refresh \
  --location us-central1 \
  --uri "https://new-endpoint.run.app/api/refresh"

# Disable job (don't delete, keeps history)
gcloud scheduler jobs pause itf-refresh --location us-central1

# Re-enable job
gcloud scheduler jobs resume itf-refresh --location us-central1
```

---

## GCS Bucket Configuration

### Verify Public Access

```bash
gcloud storage buckets describe gs://itf-preranking-data --format="value(iamConfiguration)"

# Should show: allUsers with "roles/storage.objectViewer"
```

### View CORS Configuration

```bash
gsutil cors get gs://itf-preranking-data/
```

**Expected output**:

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

### Update Cache Control on Files

```bash
# Set 5-min cache on new uploads
gsutil -h "Cache-Control:public, max-age=300" cp output/latest_merged_rankings.json gs://itf-preranking-data/

# Verify
gsutil stat gs://itf-preranking-data/latest_merged_rankings.json
```

---

## Firestore Configuration

### Check Firestore Location

```bash
gcloud firestore databases describe --project itf-live-rankings
```

### View Pipeline State

```bash
# Get current pipeline status
gcloud firestore documents get itf_sessions/_pipeline_status --project=itf-live-rankings

# Get lock state
gcloud firestore documents get itf_sessions/_pipeline_lock --project=itf-live-rankings
```

### Manual State Reset (Emergency)

```bash
# Release stuck lock (if pipeline crashed mid-run)
gcloud firestore documents delete itf_sessions/_pipeline_lock --project=itf-live-rankings

# Reset status to idle
gcloud firestore documents patch itf_sessions/_pipeline_status \
  --project=itf-live-rankings \
  --update "phase=idle,last_error=null"
```

---

## Troubleshooting

### Pipeline Stuck in "Uploading" Phase

**Cause**: Likely crashed trying to upload to GCS

**Solution**:

1. Check Cloud Logging: `gcloud logging read "resource.labels.service_name=itf-preranking" --limit=50`
2. Release lock: `gcloud firestore documents delete itf_sessions/_pipeline_lock --project=itf-live-rankings`
3. Trigger refresh again: `gcloud scheduler jobs run itf-refresh --location us-central1`

### "Repository not found" on GCS Upload

**Cause**: Service account lacks GCS write permissions OR bucket doesn't exist

**Solution**:

```bash
# Verify bucket exists
gcloud storage buckets list --project=itf-live-rankings

# Verify Cloud Run service account has permissions
gcloud storage buckets get-iam-policy gs://itf-preranking-data \
  --project=itf-live-rankings
```

### Browser Warm-up Failing (Incapsula)

**Cause**: `ITF_EMAIL` and `ITF_PASSWORD` not set OR credentials invalid

**Solution**:

```bash
# Check if credentials are set
gcloud run services describe itf-preranking --region us-central1 \
  --format="value(spec.template.spec.containers[0].env[name=ITF_EMAIL])"

# Update credentials
gcloud run services update itf-preranking \
  --region us-central1 \
  --set-env-vars "ITF_EMAIL=your-email,ITF_PASSWORD=your-password"
```

### Scheduler Job Returns 403

**Cause**: Service account lost Cloud Run Invoker permission

**Solution**:

```bash
gcloud run services add-iam-policy-binding itf-preranking \
  --region us-central1 \
  --member "serviceAccount:itf-scheduler@itf-live-rankings.iam.gserviceaccount.com" \
  --role "roles/run.invoker"
```

---

## Rollback to Previous Revision

### List Available Revisions

```bash
gcloud run revisions list --service itf-preranking --region us-central1
```

### Deploy Previous Revision

```bash
# If revision itf-preranking-00032-abc is known-good:
gcloud run deploy itf-preranking \
  --region us-central1 \
  --image gcr.io/itf-live-rankings/itf-preranking:00032-abc
```

Or use Cloud Console: Cloud Run → itf-preranking → Revisions → Select previous → Set traffic to 100%

---

## Local Development

### Run Pipeline Locally

```bash
# Test individual steps
python main.py           # Scrape ITF APIs
python calculate_rankings.py  # Calculate ratings
python merge_rankings.py      # Finalize JSON

# Run entire pipeline
python app.py            # Start FastAPI server
# Visit http://localhost:8000
```

### Environment Variables (Local)

```bash
export FIRESTORE_PROJECT_ID=itf-live-rankings
export GCS_BUCKET=itf-preranking-data
export ITF_EMAIL=your-email
export ITF_PASSWORD=your-password
```

### Test Firestore Locally (Emulator)

```bash
# Install emulator
gcloud components install cloud-firestore-emulator

# Start emulator
gcloud beta emulators firestore start

# In another terminal, set env var
export FIRESTORE_EMULATOR_HOST=localhost:8080

# Now app.py uses local Firestore
python app.py
```

---

## Monitoring

### View Recent Logs

```bash
# Last 50 entries
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=itf-preranking" \
  --limit=50 --format=json

# Errors only
gcloud logging read "resource.type=cloud_run_revision AND severity=ERROR AND resource.labels.service_name=itf-preranking" \
  --limit=20 --format=text
```

### Check GCS Upload Timestamp

```bash
# See when files were last updated
gsutil stat gs://itf-preranking-data/latest_merged_rankings.json

# Download and inspect
gsutil cp gs://itf-preranking-data/latest_merged_rankings.json - | jq '.generated_at'
```

### Pipeline Status

```bash
curl https://itf-preranking-609418294401.us-central1.run.app/api/status | python -m json.tool
```

---

## Checklist Before Travel

- [ ] All code committed to GitHub (`aspxcts/itf-preranking`)
- [ ] Latest revision deployed to Cloud Run
- [ ] Scheduler jobs enabled and next run times visible in Cloud Console
- [ ] GCS bucket public and CORS enabled
- [ ] Firestore permissions verified
- [ ] Cloud Run service account has Cloud Logging access
- [ ] Recent test run succeeded (check `/api/status`)
- [ ] `ITF_EMAIL`/`ITF_PASSWORD` env vars set on Cloud Run
- [ ] README.md and ARCHITECTURE.md committed to repo

---

## After Travel Checklist

1. Clone repo on new device: `git clone https://github.com/aspxcts/itf-preranking.git`
2. Check Cloud Console for any failed scheduler jobs
3. Review logs: `gcloud logging read ... --after-time "2 hours ago"`
4. Verify latest data in GCS: `gsutil stat gs://itf-preranking-data/latest_merged_rankings.json`
5. Test status endpoint: `curl https://itf-preranking-609418294401.us-central1.run.app/api/status`
