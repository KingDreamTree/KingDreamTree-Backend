from fastapi import APIRouter

from app.schemas.compare import CompareRequest, CompareResponse
from app.schemas.routine import RoutineRequest, RoutineResponse
from app.services import db, vlm

router = APIRouter(tags=["compare"])


@router.post(
    "/compare",
    response_model=CompareResponse,
    summary="체형 비교 분석",
    description="세그멘테이션 결과를 받아 Claude Call1로 체형 차이를 분석합니다.",
)
async def compare(body: CompareRequest) -> CompareResponse:
    comparison = await vlm.compare_body(
        body.user_seg.model_dump(),
        body.ref_seg.model_dump(),
    )
    await db.update_analysis(body.analysis_id, analysis_json=comparison)
    return CompareResponse(analysis_id=body.analysis_id, comparison=comparison)


@router.post(
    "/routine",
    response_model=RoutineResponse,
    summary="개인화 운동 루틴 생성",
    description="비교 분석 결과를 받아 Claude Call2로 개인화 운동 루틴을 생성합니다.",
    tags=["routine"],
)
async def routine(body: RoutineRequest) -> RoutineResponse:
    routine_data = await vlm.generate_routine(body.comparison)
    await db.update_analysis(body.analysis_id, routine_json=routine_data)
    return RoutineResponse(analysis_id=body.analysis_id, routine=routine_data)
