import asyncio
import hashlib
import json

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from app.core.exceptions import AppError
from app.services.audio_processing import AudioProcessingError


class IntegrationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            if request.method != "POST":
                return await original(request)
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key or len(key) > 255:
                raise AppError("INVALID_IDEMPOTENCY_KEY", "Every POST requires an Idempotency-Key of 1-255 characters.", False, {}, 400)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > request.app.state.settings.max_request_bytes:
                    raise AppError("REQUEST_TOO_LARGE", "Request body exceeds the configured size limit.", False, {}, 413)
            request._body = bytes(body)
            content_type = request.headers.get("content-type", "").split(";")[0]
            payload = hashlib.sha256(body).hexdigest()
            if content_type == "application/json":
                try:
                    payload = json.dumps(json.loads(body), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                except ValueError:
                    pass
            elif content_type == "multipart/form-data":
                fields = []
                for name, value in (await request.form()).multi_items():
                    if isinstance(value, UploadFile):
                        fields.append((name, value.filename, value.content_type, hashlib.sha256(await value.read()).hexdigest()))
                        await value.seek(0)
                    else:
                        fields.append((name, str(value)))
                payload = json.dumps(fields, separators=(",", ":"))
            fingerprint = hashlib.sha256(json.dumps([request.method, request.url.path, request.url.query, content_type, payload]).encode()).hexdigest()
            locks = request.app.state.idempotency_locks
            lock = locks.setdefault(key, asyncio.Lock())
            async with lock:
                service = request.app.state.job_service
                claim = service.claim_http_request(key, fingerprint)
                if not claim["claimed"]:
                    await request.close()
                    if claim["status"] is None:
                        raise AppError("IDEMPOTENCY_IN_PROGRESS", "The original operation is pending; retry this key, not a new key.", True, {}, 409)
                    return Response(claim["body"], status_code=claim["status"], headers={"Content-Type":claim["content_type"], "X-Idempotency-Replayed":"true"})
                try:
                    response = await original(request)
                except (AppError, AudioProcessingError, RequestValidationError, HTTPException) as error:
                    handler = next((request.app.exception_handlers[error_type] for error_type in type(error).__mro__ if error_type in request.app.exception_handlers), None)
                    if handler is None:
                        raise
                    response = await handler(request, error)
                if not hasattr(response, "body"):
                    raise RuntimeError("POST responses must be materialized for safe replay")
                if response.status_code < 300:
                    try:
                        accepted = json.loads(response.body)
                    except ValueError:
                        accepted = {}
                    if isinstance(accepted, dict) and accepted.get("job_id"):
                        service.attach_correlation(accepted["job_id"], request.state.correlation_id)
                service.finish_http_request(key, response.status_code, response.body, response.headers.get("content-type", "application/json"))
                return response
        return handle