#!/usr/bin/env python3
"""
ITF Junior Pre-Ranking – FastAPI Server
========================================

Pipeline is triggered exclusively by Cloud Scheduler (every 12 h for the
full refresh; every Monday for the expiry sweep).  Output files are written
to GCS so any browser can fetch them directly without hitting this server.

On cold start the server downloads the latest output files from GCS so they
are served immediately via /output/ until the next scheduled pipeline run.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ── Configuration ─────────────────────────────────────────────────────────────

GCS_BUCKET = os.environ.get("GCS_BUCKET", "itf-preranking-data")
CRON_SECRET = os.environ.get("CRON_SECRET", "")

# ── Firestore ─────────────────────────────────────────────────────────────────

_db: Any = None


def _get_db():
    global _db
    if _db is None:
        try:
            from google.cloud import firestore
            _db = firestore.Client()
        except Exception as exc:
            print(f"[server] Firestore unavailable: {exc}")
    return _db


SESSIONS_COL = "itf_sessions"
LOCK_DOC = "_pipeline_lock"
STATUS_DOC = "_pipeline_status"
LOCK_TTL_MINUTES = 90

# ── GCS helpers ───────────────────────────────────────────────────────────────

_OUTPUT_FILES = [
    "latest_merged_rankings.json",
    "latest_points_earned.json",
    "latest_player_breakdowns.json",
]


def _gcs_upload_sync(files: list[str] | None = None) -> None:
    """Upload the specified (or all) latest_*.json output files to GCS (blocking)."""
    if not GCS_BUCKET:
        print("[gcs] GCS_BUCKET not set — skipping upload.")
        return
    upload_files = files if files is not None else _OUTPUT_FILES
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        for fname in upload_files:
            local = Path("output") / fname
            if not local.exists():
                print(f"[gcs] {fname} missing locally — skipping.")
                continue
            blob = bucket.blob(fname)
            blob.upload_from_filename(str(local), content_type="application/json")
            blob.cache_control = "public, max-age=300"
            blob.patch()
            print(f"[gcs] Uploaded {fname}")
    except Exception as e:
        print(f"[gcs] Upload error: {e}")
        raise


def _gcs_download_sync() -> None:
    """Download latest_*.json from GCS into output/ if missing (blocking)."""
    if not GCS_BUCKET:
        return
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        Path("output").mkdir(exist_ok=True)
        for fname in _OUTPUT_FILES:
            local = Path("output") / fname
            if local.exists():
                continue
            blob = bucket.blob(fname)
            try:
                blob.reload()  # raises NotFound if missing
                blob.download_to_filename(str(local))
                print(f"[gcs] Downloaded {fname}")
            except Exception:
                print(f"[gcs] {fname} not in bucket yet — skipping.")
    except Exception as e:
        print(f"[gcs] Download error: {e}")


# ── Pipeline status (Firestore-backed) ────────────────────────────────────────


def _set_status(phase: str, *, error: Optional[str] = None) -> None:
    """Update pipeline phase in Firestore so all instances share the same state."""
    db = _get_db()
    if not db:
        return
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        update: dict = {"phase": phase, "updated_at": now}
        if phase == "main":          # start of a new pipeline run
            update["started_at"] = now
            update["last_error"] = None
        elif phase == "idle":        # successful completion
            update["last_success_at"] = now
            update["last_error"] = None
        if error is not None:        # override phase to "error"
            update["phase"] = "error"
            update["last_error"] = error
        db.collection(SESSIONS_COL).document(STATUS_DOC).set(update, merge=True)
    except Exception as e:
        print(f"[status] Update failed: {e}")


def _get_status() -> dict:
    db = _get_db()
    if not db:
        return {}
    try:
        doc = db.collection(SESSIONS_COL).document(STATUS_DOC).get()
        return doc.to_dict() or {}
    except Exception:
        return {}


# ── Distributed pipeline lock ─────────────────────────────────────────────────


def _try_acquire_lock() -> bool:
    """
    Best-effort Firestore lock.  Returns True if this process may run the
    pipeline, False if another Cloud Run instance beat it.
    Not perfectly atomic, but good enough for Cloud Run single-instance traffic.
    """
    db = _get_db()
    if not db:
        return True  # No Firestore — proceed
    now = datetime.datetime.now(datetime.timezone.utc)
    stale = now - datetime.timedelta(minutes=LOCK_TTL_MINUTES)
    ref = db.collection(SESSIONS_COL).document(LOCK_DOC)
    try:
        doc = ref.get()
        if doc.exists:
            data = doc.to_dict()
            locked_at = data.get("locked_at")
            if locked_at:
                if hasattr(locked_at, "replace"):
                    locked_at = locked_at.replace(tzinfo=datetime.timezone.utc)
                if locked_at > stale:
                    print(
                        f"[server] Pipeline locked by "
                        f"{data.get('instance_id', '?')} since {locked_at} — skipping."
                    )
                    return False
        ref.set({
            "locked_at": now,
            "instance_id": os.environ.get("K_REVISION", "local"),
        })
        return True
    except Exception as exc:
        print(f"[server] Lock error (proceeding anyway): {exc}")
        return True  # Fail-open


def _release_lock() -> None:
    db = _get_db()
    if not db:
        return
    try:
        db.collection(SESSIONS_COL).document(LOCK_DOC).delete()
    except Exception:
        pass


# ── Pipeline state ────────────────────────────────────────────────────────────

_refreshing = False
_sweeping = False


def _week_monday(anchor: datetime.date) -> datetime.date:
    return anchor - datetime.timedelta(days=anchor.weekday())


def _pipeline_date() -> datetime.date:
    """Return the effective UTC date for pipeline week calculations.

    On Monday before 16:00 UTC the new ITF week has technically begun but
    tournament results are not yet available.  Returning Sunday keeps the
    pipeline targeting the previous (still-live) ranking week until the
    afternoon, when real results start appearing.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    d = now.date()
    # Monday grace period: before 16:00 UTC treat Monday as Sunday so the
    # pipeline continues to use last week's Monday as its target.
    if d.weekday() == 0 and now.hour < 16:
        d -= datetime.timedelta(days=1)
    return d


def _output_generated_at() -> Optional[str]:
    try:
        data = json.loads(
            Path("output/latest_merged_rankings.json").read_text(encoding="utf-8")
        )
        return data.get("generated_at")
    except Exception:
        return None


async def _do_refresh() -> None:
    global _refreshing
    if _refreshing:
        print("[refresh] Already running in this process — skipping.")
        return
    if not _try_acquire_lock():
        return
    _refreshing = True
    print("[refresh] Starting data refresh…")
    try:
        from main import run as main_run
        from calculate_rankings import run as calc_run
        from merge_rankings import run as merge_run

        monday = _week_monday(_pipeline_date())

        _set_status("main")
        await main_run(headless=True, week_anchor=monday)
        # Upload latest_points_earned.json immediately after main_run so the
        # bracket tab reflects new results right away.
        await asyncio.to_thread(
            _gcs_upload_sync,
            ["latest_points_earned.json"],
        )

        _set_status("calculate")
        # Full GetRankingPoints fetch only on Mondays; all other days load the
        # cached breakdowns written on Monday and need no browser session at all.
        is_monday = datetime.datetime.now(datetime.timezone.utc).weekday() == 0
        await calc_run(headless=True, week_monday=monday, full_breakdown=is_monday)

        _set_status("merge")
        await merge_run(headless=True, week_monday=monday)

        _set_status("uploading")
        await asyncio.to_thread(_gcs_upload_sync)

        _set_status("idle")
        print("[refresh] Done.")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        _set_status("idle", error=str(exc))
    finally:
        _refreshing = False
        _release_lock()


async def _do_sweep() -> None:
    global _sweeping
    if _sweeping:
        return
    _sweeping = True
    print("[sweep] Starting expiry sweep…")
    try:
        from expiry_sweep import run as sweep_run
        monday = _week_monday(_pipeline_date())
        _set_status("sweep")
        await sweep_run(headless=True, week_anchor=monday)
        _set_status("uploading")
        await asyncio.to_thread(_gcs_upload_sync)
        _set_status("idle")
        print("[sweep] Done.")
    except Exception as exc:
        _set_status("idle", error=str(exc))
        print(f"[sweep] Error: {exc}")
    finally:
        _sweeping = False


# ── FastAPI app ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: download latest data from GCS so files are served immediately."""
    print(f"[server] Starting (revision={os.environ.get('K_REVISION', 'local')})…")
    await asyncio.to_thread(_gcs_download_sync)
    yield


app = FastAPI(lifespan=_lifespan, docs_url=None, redoc_url=None)  # disable auto-docs in prod

Path("output").mkdir(exist_ok=True)
app.mount("/output", StaticFiles(directory="output"), name="output")


# ── Scheduler guard ───────────────────────────────────────────────────────────


def _require_scheduler(request: Request) -> None:
    """Allow only Cloud Scheduler or requests with CRON_SECRET."""
    # Cloud Scheduler always injects this header
    if request.headers.get("X-CloudScheduler-JobName"):
        return
    # Allow manual curl / CI with matching secret
    if CRON_SECRET and request.headers.get("Authorization") == f"Bearer {CRON_SECRET}":
        return
    raise HTTPException(status_code=403, detail="Forbidden: scheduler only")


# ── API ───────────────────────────────────────────────────────────────────────


def _fmt_ts(ts) -> Optional[str]:
    if ts is None:
        return None
    if hasattr(ts, "replace"):   # Firestore Timestamp
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    if hasattr(ts, "isoformat"):
        return ts.isoformat().replace("+00:00", "Z")
    return str(ts)


@app.get("/api/status")
async def api_status():
    ps = _get_status()
    return {
        "refreshing": _refreshing,
        "sweeping":   _sweeping,
        "phase":      ps.get("phase", "idle"),
        "started_at":      _fmt_ts(ps.get("started_at")),
        "last_success_at": _fmt_ts(ps.get("last_success_at")),
        "last_error":      ps.get("last_error"),
        "generated_at":    _output_generated_at(),
        "gcs_url": f"https://storage.googleapis.com/{GCS_BUCKET}" if GCS_BUCKET else None,
    }


@app.post("/api/refresh")
async def api_refresh(request: Request):
    _require_scheduler(request)
    await _do_refresh()
    return {"status": "ok"}


@app.post("/api/sweep")
async def api_sweep(request: Request):
    _require_scheduler(request)
    await _do_sweep()
    return {"status": "ok"}


@app.get("/")
async def root():
    return FileResponse("index.html")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app:app", host="0.0.0.0", port=port, log_level="info")
