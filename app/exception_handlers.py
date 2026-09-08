"""에러 응답 통일 — {"error": {"code", "message", "detail"}}.

API 서버(app/main.py)와 GPU 팟 서버(app/pod/server.py)가 **같은 규약**으로 응답해야
프론트가 두 곳의 에러를 한 코드로 처리한다. 그래서 핸들러를 여기 한 곳에 두고
두 앱이 같이 붙인다. (종전에는 main.py 안에 있었다 — 2026-09-09 분리)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import ApiError, internal_error

log = logging.getLogger("app")

_HTTP_CODES = {
    401: "MISSING_USER_ID",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "TOO_MANY_REQUESTS",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # ⚠️ jsonable_encoder 를 거쳐야 한다. pydantic v2 의 errors() 는 ctx 안에
        #    예외 객체를 담아 오는 경우가 있어, 그대로 넣으면 직렬화에서 터진다.
        #    그러면 400 이어야 할 응답이 500 으로 바뀐다.
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "요청 형식이 올바르지 않습니다.",
                    "detail": {"errors": jsonable_encoder(exc.errors())},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """프레임워크가 직접 내는 에러도 우리 형태로 바꾼다.

        ⚠️ 없는 경로(404)·허용 안 된 메서드(405)는 FastAPI 가 {"detail": "..."} 로
           응답한다. 우리 규약은 {"error": {...}} 라, 프론트가 error.code 를 읽다가
           터진다. **에러 형태가 두 종류인 게 가장 나쁘다** — 잘 되다가 URL 을 하나
           틀린 순간에만 깨지므로 늦게 발견된다.
        """
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail)}},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """예상 못 한 오류. 형태를 지키고 원인은 로그에만 남긴다.

        ⚠️ 이게 없으면 500 응답이 본문 없는 "Internal Server Error" 평문으로 나간다.
           JSON 이 아니라 프론트의 응답 파싱 자체가 실패한다.
        ⚠️ message 에 예외 내용을 넣지 않는다 — 쿼리·경로·키가 화면으로 샌다.
        """
        log.exception("처리되지 않은 오류: %s %s", request.method, request.url.path)
        err = internal_error()
        return JSONResponse(status_code=err.status_code, content=err.to_dict())
