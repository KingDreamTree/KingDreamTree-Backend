"""FastAPI 앱 진입점.

⚠️ 이 프로세스는 Sapiens 모델을 로드하지 않는다.
   API와 워커는 별개 프로세스다. 1.5GB 모델은 세그멘테이션 워커만 들고 있어야
   t3.large(8GB)에서 API가 죽지 않는다.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.errors import ApiError, internal_error
from app.routes.analysis import router as analysis_router
from app.routes.body_parts import router as body_parts_router
from app.routes.inbody import router as inbody_router
from app.routes.jobs import router as jobs_router
from app.routes.photos import router as photos_router
from app.routes.pose_criteria import router as pose_criteria_router
from app.routes.routines import router as routines_router
from app.routes.segmentation import router as segmentation_router
from app.routes.sessions import router as sessions_router
from app.routes.storage import router as storage_router
from app.routes.users import router as users_router
from app.routes.workout_logs import router as workout_logs_router

app = FastAPI(
    title="KingDreamTree Backend",
    description=(
        "레퍼런스 이미지 기반 체형 비교 분석 + 개인화 운동 루틴 생성.\n\n"
        "**인증 없음** — 모든 요청에 `X-User-Id` 헤더가 필요합니다 "
        "(`POST /users`, `GET /body-parts` 제외).\n\n"
        "무거운 작업은 202 + `job_id`를 반환하고 `GET /jobs/{job_id}` 폴링으로 확인합니다.\n\n"
        "명세: `docs/api-spec-v2.md`"
    ),
    version="0.2.0",
)

log = logging.getLogger("app")


# --------------------------------------------------------------------------- #
# CORS — 프론트가 다른 오리진에서 붙는다
# --------------------------------------------------------------------------- #

# ⚠️ 이게 없으면 **브라우저에서 오는 요청이 전부 막힌다.** 서버는 정상인데
#    프론트 콘솔에만 CORS 오류가 뜨므로 원인을 찾는 데 시간이 오래 걸린다.
#    (curl·Postman 으로는 잘 되는데 화면에서만 안 되는 상태)
#
# ⚠️ allow_credentials 는 켜지 않는다. 우리는 쿠키를 안 쓰고 X-User-Id 헤더로만
#    식별한다. 켜면 allow_origins="*" 를 못 쓰게 되기도 한다.
#
# ⚠️ **CORS 는 우리 데이터를 지켜주지 않는다.** user_id 를 아는 사람은 어느
#    오리진에서든 그 사용자의 데이터를 읽을 수 있다(로그인이 없으므로).
#    오리진 제한은 "우리 프론트만 붙게" 하는 운영 편의일 뿐이다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# 에러 응답 통일 — {"error": {"code", "message", "detail"}}
# --------------------------------------------------------------------------- #


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
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


_HTTP_CODES = {
    401: "MISSING_USER_ID",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "TOO_MANY_REQUESTS",
}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
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


# --------------------------------------------------------------------------- #
# 라우터
# --------------------------------------------------------------------------- #

# ⚠️ 접두사는 여기 한 곳에서만 붙인다. 라우터마다 prefix="/api/v1"을 적으면
#    한 군데만 빠져도 프론트가 404를 맞고, 버전을 올릴 때 전부 고쳐야 한다.
#    (Phase 0 라우터들이 실제로 접두사 없이 등록돼 명세의 Base URL과 어긋나 있었다)
API_PREFIX = "/api/v1"

for _router in (
    body_parts_router,
    jobs_router,
    users_router,
    sessions_router,
    photos_router,
    pose_criteria_router,
    segmentation_router,
    storage_router,
    inbody_router,
    analysis_router,
    routines_router,
    workout_logs_router,
):
    app.include_router(_router, prefix=API_PREFIX)

# 구 스캐폴드(/analyze, /compare)는 F08·F09 가 새 구조로 대체해 삭제했다.
# 관련 DTO(schemas/analyze.py, schemas/compare.py)도 함께 제거됐다.
# ⚠️ 되살려 참고하지 말 것 — 폭 몇 개(shoulder_width 등)를 재는 방식이라
#    지금의 부위 단위 설계와 맞지 않는다. docs/removed-code.md 참고.


@app.get("/health", tags=["health"])
async def health() -> dict[str, object]:
    return {"status": "ok", "mock": settings.use_mock, "version": app.version}
