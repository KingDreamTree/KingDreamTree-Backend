"""FastAPI 앱 진입점.

⚠️ 이 프로세스는 Sapiens 모델을 로드하지 않는다.
   API와 워커는 별개 프로세스다. 1.5GB 모델은 세그멘테이션 워커만 들고 있어야
   t3.large(8GB)에서 API가 죽지 않는다.
"""

import logging
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.exception_handlers import register_exception_handlers
from app.services.db import get_client
from app.routes.analysis import router as analysis_router
from app.routes.body_parts import router as body_parts_router
from app.routes.coach_chat import router as coach_chat_router
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

# ⚠️ pod 모드 기동 점검 — 팟(app/pod/main.py)에는 있는데 API 에는 없어서, 비밀이 비면
#    첫 upload-token 호출이 500 으로 터졌다 (2026-09-09 검사). 여기서 바로 죽는다.
if settings.photo_pipeline == "pod" and not settings.pod_upload_secret:
    raise RuntimeError(
        "PHOTO_PIPELINE=pod 인데 POD_UPLOAD_SECRET 이 비어 있습니다 — 팟과 같은 값을 .env 에 넣으세요."
    )


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
#   핸들러 본문은 app/exception_handlers.py — GPU 팟 서버(app/pod/server.py)와 공유한다.
# --------------------------------------------------------------------------- #

register_exception_handlers(app)


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
    coach_chat_router,
):
    app.include_router(_router, prefix=API_PREFIX)

# 구 스캐폴드(/analyze, /compare)는 F08·F09 가 새 구조로 대체해 삭제했다.
# 관련 DTO(schemas/analyze.py, schemas/compare.py)도 함께 제거됐다.
# ⚠️ 되살려 참고하지 말 것 — 폭 몇 개(shoulder_width 등)를 재는 방식이라
#    지금의 부위 단위 설계와 맞지 않는다. docs/removed-code.md 참고.


@app.get("/health", tags=["health"])
async def health() -> dict[str, object]:
    """⚠️ #114 — 예전엔 프로세스 생존만 봤다. Supabase 연결이 끊기거나 워커가
    전멸해도 200을 그대로 돌려줘서, 배포 실패·의존성 장애를 못 잡았다.

    ⚠️ 워커 무활동은 **status를 안 내린다.** 트래픽이 없으면 정상적으로
       조용하다 — 그걸 장애로 보고하면 오탐이다(§queue.is_stalled 와 같은
       교훈). worker_last_seen_sec_ago 는 진단용 정보로만 얹는다.
    """
    body: dict[str, object] = {"status": "ok", "mock": settings.use_mock, "version": app.version}
    try:
        get_client().table("job").select("job_id").limit(1).execute()
        body["db"] = "ok"
    except Exception:  # noqa: BLE001 — 원인이 뭐든 결론은 같다: DB에 못 닿는다
        log.exception("헬스체크 — Supabase 연결 실패")
        body["db"] = "unreachable"
        body["status"] = "degraded"

    try:
        last = (
            get_client()
            .table("job")
            .select("started_at")
            .not_.is_("started_at", "null")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        if last and last[0].get("started_at"):
            at = datetime.fromisoformat(str(last[0]["started_at"]).replace("Z", "+00:00"))
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            body["worker_last_seen_sec_ago"] = round(
                (datetime.now(timezone.utc) - at).total_seconds()
            )
        else:
            body["worker_last_seen_sec_ago"] = None
    except Exception:  # noqa: BLE001 — 진단용 부가 정보라 실패해도 헬스체크 자체는 죽이지 않는다
        body["worker_last_seen_sec_ago"] = "unknown"

    return body
