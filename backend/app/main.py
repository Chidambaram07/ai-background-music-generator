import logging
import uuid
import re
import asyncio
from weakref import WeakValueDictionary
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException

from app.api.routes.jobs import router as jobs_router
from app.api.routes.music import router as music_router
from app.api.routes.specifications import router as specifications_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging, correlation_context
from app.services.job_service import JobService
from app.services.audio_processing import AudioProcessingError
from app.workers.job_worker import JobWorker

configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    service = JobService(settings.database_path)
    worker = JobWorker(service, settings)
    app.state.job_service = service
    app.state.job_worker = worker
    app.state.settings = settings
    app.state.idempotency_locks = WeakValueDictionary()
    await asyncio.to_thread(worker.output_store.initialize)
    await worker.start()
    yield
    await worker.stop()


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="IncuBrix PS1 Music Engine: asynchronous jobs, specification planning, procedural independent stems, regeneration, audio validation, isolated storage and measured manifests. The local audio renderer is not an AI audio model.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key", "X-Correlation-Id"],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    supplied = request.headers.get("X-Correlation-Id", "")
    correlation_id = supplied if re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", supplied) else str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    token = correlation_context.set(correlation_id)
    try:
        try:
            response = await call_next(request)
        except Exception as error:
            response = await unexpected_error_handler(request, error)
        response.headers["X-Correlation-Id"] = correlation_id
        logger.info("HTTP request completed", extra={"correlation_id":correlation_id,"method":request.method,"path":request.url.path,"status":response.status_code})
        return response
    finally:
        correlation_context.reset(token)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable, "details": exc.details}})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    details = {"fields": jsonable_encoder(exc.errors())}
    return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": "The request contains invalid fields.", "retryable": False, "details": details}})


@app.exception_handler(AudioProcessingError)
async def artifact_error_handler(_: Request, exc: AudioProcessingError):
    status = 503 if exc.code.startswith("STORAGE_") else 404 if exc.code == "ARTIFACT_MISSING" else 422
    return JSONResponse(status_code=status, content={"error": {"code": exc.code, "message": str(exc), "retryable": status >= 500, "details": exc.details}})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    logger.exception("Unexpected application error", extra={"correlation_id": getattr(request.state, "correlation_id", None)})
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_SERVER_ERROR", "message": "An unexpected server error occurred.", "retryable": True, "details": {}}})


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException):
    code = {404:"NOT_FOUND",405:"METHOD_NOT_ALLOWED",413:"REQUEST_TOO_LARGE"}.get(exc.status_code, "HTTP_ERROR")
    return JSONResponse(status_code=exc.status_code, headers=exc.headers, content={"error":{"code":code,"message":str(exc.detail),"retryable":exc.status_code >= 500,"details":{}}})


@app.get("/health", include_in_schema=False)
@app.get("/api/v1/health", tags=["system"], summary="Liveness check")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", include_in_schema=False)
@app.get("/api/v1/ready", tags=["system"], summary="Readiness check")
async def ready(request: Request) -> dict[str, str]:
    try:
        request.app.state.job_service.queued_or_processing_jobs()
    except Exception:
        raise AppError("NOT_READY", "Job database is unavailable.", True, {}, 503) from None
    task = request.app.state.job_worker._task
    if task is None or task.done():
        raise AppError("NOT_READY", "Background worker is unavailable.", True, {}, 503)
    await asyncio.to_thread(request.app.state.job_worker.output_store.ready)
    return {"status": "ready"}


app.include_router(music_router)
app.include_router(jobs_router)
app.include_router(specifications_router)


def integration_openapi():
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(title=app.title,version=app.version,description=app.description,routes=app.routes)
    schema["components"]["schemas"]["ErrorEnvelope"] = {
        "type":"object","required":["error"],"properties":{"error":{"type":"object",
        "required":["code","message","retryable","details"],"properties":{
            "code":{"type":"string","pattern":"^[A-Z][A-Z0-9_]*$"},"message":{"type":"string"},
            "retryable":{"type":"boolean"},"details":{"type":"object","additionalProperties":True}}}}}
    binary_types = {
        "/api/v1/music/jobs/{job_id}/mix":"audio/wav",
        "/api/v1/music/jobs/{job_id}/stems/{stem}":"audio/wav",
        "/api/v1/music/jobs/{job_id}/duck":"audio/wav",
        "/api/v1/music/jobs/{job_id}/loop":"audio/wav",
        "/api/v1/music/jobs/{job_id}/mix.mp3":"audio/mpeg",
        "/api/v1/music/files/{filename}":"audio/wav",
        "/api/v1/jobs/{job_id}/download/package":"application/zip",
    }
    for path_name,path in schema["paths"].items():
        for method,operation in path.items():
            if method not in {"get","post","put","patch","delete","options","head"}:
                continue
            parameters = operation.setdefault("parameters",[])
            parameters.append({"name":"X-Correlation-Id","in":"header","required":False,"schema":{"type":"string"},"description":"A safe supplied ID is echoed; invalid IDs are replaced with a UUID."})
            if method == "post":
                parameters[:] = [parameter for parameter in parameters if parameter.get("name") != "Idempotency-Key"]
                parameters.append({"name":"Idempotency-Key","in":"header","required":True,"schema":{"type":"string","minLength":1,"maxLength":255},"description":"Required on every POST. Same key/path/payload replays the original response; changed payload returns409."})
            if method == "get" and path_name in binary_types:
                operation["responses"]["200"] = {"description":"Artifact bytes","content":{binary_types[path_name]:{"schema":{"type":"string","format":"binary"}}}}
            published = path_name in binary_types and not path_name.endswith(("/download/package","/files/{filename}"))
            if method == "get" and (published or path_name.endswith(("/manifest","/artifacts/{artifact}"))):
                operation["responses"]["307"] = {"description":"Short-lived signed S3 artifact download","headers":{"Location":{"required":True,"schema":{"type":"string","format":"uri"}}}}
                if path_name.endswith("/artifacts/{artifact}"):
                    operation["responses"].pop("200",None)
            for status in ("400","404","405","409","413","422","500","503"):
                operation["responses"][status] = {"description":"Structured application error","content":{"application/json":{"schema":{"$ref":"#/components/schemas/ErrorEnvelope"}}}}
            for response in operation["responses"].values():
                response.setdefault("headers",{})["X-Correlation-Id"] = {"schema":{"type":"string"},"description":"Request correlation identifier"}
    app.openapi_schema = schema
    return schema


app.openapi = integration_openapi
