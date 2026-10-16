
import json
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from threading import Lock

from fastapi import FastAPI, Query, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import Counter, CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware


EMAIL = os.getenv(
    "EXAM_EMAIL",
    "23f2003924@ds.study.iitm.ac.in",
)

app = FastAPI()

# Prometheus counter: exported as http_requests_total.
REQUEST_COUNTER = Counter(
    "http_requests",
    "Total number of HTTP requests received",
)

START_TIME = time.monotonic()

# Keep the most recent 1,000 structured log entries in memory.
LOG_ENTRIES = deque(maxlen=1000)
LOG_LOCK = Lock()


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Increment for every request, regardless of endpoint.
        REQUEST_COUNTER.inc()

        started = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            elapsed = max(0.0, time.perf_counter() - started)

            entry = {
                "level": "INFO" if status_code < 500 else "ERROR",
                "ts": datetime.now(timezone.utc).isoformat(),
                "path": request.url.path,
                "request_id": request_id,
                "method": request.method,
                "status_code": status_code,
                "duration_s": round(elapsed, 6),
            }

            # Save the log entry and also emit structured JSON to stdout.
            with LOG_LOCK:
                LOG_ENTRIES.append(entry)

            print(json.dumps(entry, separators=(",", ":")), flush=True)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{elapsed:.6f}"
        return response


app.add_middleware(ObservabilityMiddleware)


@app.get("/work")
async def work(
    n: int = Query(..., ge=0, le=1_000_000),
):
    # Perform n simple units of work.
    result = 0
    for i in range(n):
        result += i

    return {
        "email": EMAIL,
        "done": n,
    }


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(
        generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/healthz")
async def healthz():
    uptime = max(0.0, time.monotonic() - START_TIME)
    return {
        "status": "ok",
        "uptime_s": float(uptime),
    }


@app.get("/logs/tail")
async def logs_tail(
    limit: int = Query(100, ge=0, le=1000),
):
    with LOG_LOCK:
        entries = list(LOG_ENTRIES)

    if limit == 0:
        return []

    return entries[-limit:]
