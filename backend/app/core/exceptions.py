class AppError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, details: dict | None = None, status_code: int = 400):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)


class JobNotFoundError(AppError):
    def __init__(self, job_id: str):
        super().__init__("JOB_NOT_FOUND", f"Job '{job_id}' was not found.", False, {"job_id": job_id}, 404)


class IdempotencyConflictError(AppError):
    def __init__(self):
        super().__init__("IDEMPOTENCY_CONFLICT", "The Idempotency-Key was already used with a different request.", False, {}, 409)


class InvalidStateTransitionError(AppError):
    def __init__(self, current: str, target: str):
        super().__init__("INVALID_STATE_TRANSITION", f"Cannot transition job from '{current}' to '{target}'.", False, {"current": current, "target": target}, 409)
