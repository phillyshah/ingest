from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from moveai_planner.approval import PlanError


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={**detail, "request_id": _rid(request)})

    @app.exception_handler(PlanError)
    async def plan_exc(request: Request, exc: PlanError):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, "request_id": _rid(request)})

    @app.exception_handler(RequestValidationError)
    async def val_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"code": "validation_error", "message": "request does not match schema",
                                                     "details": exc.errors(), "request_id": _rid(request)})
