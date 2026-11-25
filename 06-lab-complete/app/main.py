"""
Production AI Agent — Part 6 Final Project

  GET  /health  liveness  — luôn 200 khi process còn chạy
  GET  /ready   readiness — 200 khi Redis kết nối được, 503 khi không
  POST /ask     X-API-Key → rate limit (Redis) → cost guard (Redis) → mock LLM

Chạy: uvicorn app.main:app --workers 2   (từ thư mục 06-lab-complete/)
"""
import json
import logging
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask

# TODO (tiêu chí 10 — Structured logging):
#   Mỗi dòng log của app phải là 1 JSON object hợp lệ, vd
#   {"ts": "...", "lvl": "INFO", "event": "request", "path": "/ask", "status": 200, "ms": 12.3}
#   Gợi ý: tự viết logging.Formatter dùng json.dumps.


# ── Structured logging: mỗi dòng log là 1 JSON object ──────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "extra_fields", {}))
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("agent")
    log.handlers = [handler]
    log.setLevel(settings.log_level)
    log.propagate = False
    return log


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"extra_fields": fields})


# ── Lifespan: startup / graceful shutdown ─────────────────────────────────
# uvicorn tự bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
# rồi chạy phần sau `yield` và thoát với exit code 0. Không đăng ký signal
# handler riêng để không đè mất handler của uvicorn.
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ready = True
    if not settings.agent_api_key:
        log_event("config_warning", logging.WARNING,
                  detail="AGENT_API_KEY is empty — every /ask request will get 401")
    log_event("startup", environment=settings.environment, port=settings.port,
              redis_ok=redis_healthy())
    yield
    app.state.ready = False
    log_event("shutdown", reason="SIGTERM/lifespan", detail="in-flight requests drained")


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan,
              docs_url=None if settings.environment == "production" else "/docs",
              redoc_url=None)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    # TODO (tiêu chí 4): GET /health — liveness: luôn trả 200 khi process còn chạy
    #                    (kể cả khi Redis chưa kết nối được)
    # TODO (tiêu chí 5): GET /ready  — readiness: 200 khi sẵn sàng, 503 khi Redis không kết nối được
    user_id: str | None = Field(default=None, max_length=128)


_SAFE_ID = re.compile(r"[A-Za-z0-9-]+")


# ── Middleware: log mỗi request + request id ──────────────────────────────
@app.middleware("http")
async def access_log(request: Request, call_next):
    raw_id = request.headers.get("X-Request-ID", "")
    request_id = raw_id[:64] if _SAFE_ID.fullmatch(raw_id[:64] or "-") and raw_id else uuid.uuid4().hex[:16]
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        log_event("request", method=request.method, path=request.url.path, status=status,
                  ms=round((time.perf_counter() - start) * 1000, 1), request_id=request_id)


# ── Error handling: mã lỗi rõ ràng, không lộ stack trace ──────────────────
@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse({"error": "Invalid request", "details": errors}, status_code=422)


@app.exception_handler(redis.RedisError)
async def redis_error(request: Request, exc: redis.RedisError):
    log_event("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse({"error": "Backing store unavailable, try again later"}, status_code=503,
                        headers={"Retry-After": "5"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log_event("unhandled_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse({"error": "Internal server error"}, status_code=500)


# ── Endpoints ─────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"service": "production-ai-agent", "endpoints": ["/health", "/ready", "POST /ask"]}


@app.get("/health")
def health():
    """Liveness: không phụ thuộc Redis."""
    return {"status": "ok", "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được."""
    if getattr(app.state, "ready", False) and redis_healthy():
        return {"status": "ready", "redis": "ok"}
    return JSONResponse({"status": "not ready", "redis": "unreachable"}, status_code=503)


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    # TODO (tiêu chí 6): bắt buộc header X-API-Key → thêm Depends(verify_api_key)
    # TODO (tiêu chí 7): check_rate_limit(...) → 429 khi vượt giới hạn
    # TODO (tiêu chí 8): check_budget() trước khi gọi LLM, record_cost(...) sau khi gọi → 402 khi hết budget
    check_rate_limit(api_key)
    check_budget()

    answer = llm_ask(body.question)

    input_tokens = estimate_tokens(body.question)
    output_tokens = estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log_event("llm_call", user_id=body.user_id, input_tokens=input_tokens,
              output_tokens=output_tokens, cost_usd=round(cost, 8))
    return {"question": body.question, "answer": answer,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                      "cost_usd": round(cost, 8)}}
# TODO (tiêu chí 11 — Graceful shutdown):
#   Khi nhận SIGTERM: ngừng nhận request mới, xử lý xong request đang chạy rồi thoát (exit code 0).
#   Gợi ý: dùng lifespan của FastAPI để dọn dẹp. Nếu tự đăng ký signal handler cho SIGTERM thì
#   phải gọi lại handler cũ của uvicorn, nếu không process sẽ không bao giờ tắt.
