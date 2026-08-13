"""FastAPI 앱 진입점.

⚠️ 이 프로세스는 Sapiens 모델을 로드하지 않는다.
   API와 워커는 별개 프로세스다. 1.5GB 모델은 세그멘테이션 워커만 들고 있어야
   t3.large(8GB)에서 API가 죽지 않는다.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings
from app.errors import ApiError
from app.routes.body_parts import router as body_parts_router
from app.routes.inbody import router as inbody_router
from app.routes.jobs import router as jobs_router
from app.routes.photos import router as photos_router
from app.routes.segmentation import router as segmentation_router
from app.routes.sessions import router as sessions_router
from app.routes.storage import router as storage_router
from app.routes.users import router as users_router

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


# --------------------------------------------------------------------------- #
# 에러 응답 통일 — {"error": {"code", "message", "detail"}}
# --------------------------------------------------------------------------- #


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "요청 형식이 올바르지 않습니다.",
                "detail": {"errors": exc.errors()},
            }
        },
    )


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
    segmentation_router,
    storage_router,
    inbody_router,
):
    app.include_router(_router, prefix=API_PREFIX)
# TODO(B): analysis, routines, workout_logs 라우터

# ⚠️ 구 스캐폴드 라우터(/analyze, /compare, /routine)는 등록하지 않는다.
#    `analysis` 테이블이 v4 스키마에서 제거되어 호출하면 런타임에 실패한다.
#    app/routes/analyze.py · compare.py 파일은 참고용으로 남겨두었고,
#    각 담당이 새 구조로 옮긴 뒤 삭제한다.


@app.get("/health", tags=["health"])
async def health() -> dict[str, object]:
    return {"status": "ok", "mock": settings.use_mock, "version": app.version}
