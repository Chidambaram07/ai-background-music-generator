from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(pattern=r"^[A-Z0-9_]+$")
    message: str
    retryable: bool
    details: dict = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
