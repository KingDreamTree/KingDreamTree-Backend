from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.routes.analyze import router as analyze_router
from app.routes.compare import router as compare_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.use_mock:
        from app.services.segmenter import load_model

        load_model()
    yield


app = FastAPI(
    title="Body Analysis API",
    description=(
        "레퍼런스 이미지 기반 체형 비교 분석 + 개인화 운동 루틴 생성. "
        "호출 순서: /analyze → /compare → /routine. "
        "USE_MOCK=true 환경변수로 API 키·모델 없이 mock 응답 확인 가능."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(analyze_router)
app.include_router(compare_router)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "mock": settings.use_mock}
