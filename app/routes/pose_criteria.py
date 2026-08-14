"""1차 검사 기준 조회 — 프론트가 실시간 화면에서 쓸 임계값.

⚠️ **프론트에 임계값을 하드코딩하면 안 된다.** 서버에서 조정한 순간 어긋나서,
   화면에서는 통과인데 저장이 거부되는 상황이 생긴다. 실시간 촬영이 "자동으로
   1차를 통과"한다는 전제가 그때 깨진다.

산식과 각 값의 근거는 docs/pose-scoring.md 에 있다.
"""

from fastapi import APIRouter

from app.services import pose

router = APIRouter(tags=["pose"])


@router.get(
    "/pose-criteria",
    summary="자세 판정 기준 (헤더 불필요)",
)
async def get_pose_criteria() -> dict[str, float | int]:
    """촬영 화면이 매 프레임 쓰는 값들. 앱 시작 시 한 번 받아두면 된다."""
    return pose.criteria()
