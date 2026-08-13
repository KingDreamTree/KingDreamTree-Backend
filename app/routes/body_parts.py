"""부위 마스터 조회 (F06 일부).

⚠️ X-User-Id 불필요 — 유저 데이터가 아니다.
   프론트는 오버레이 범례와 색 팔레트를, 워커는 SKIN_CLASSES 대신 이걸 쓴다.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.schemas.enums import InbodySegment, PartGroup
from app.services import db

router = APIRouter(tags=["body-parts"])


class BodyPart(BaseModel):
    class_name: str
    name_ko: str
    part_group: PartGroup
    inbody_segment: InbodySegment | None = None
    is_comparable: bool
    color_hex: str | None = None
    display_order: int


class BodyPartListResponse(BaseModel):
    items: list[BodyPart]


@router.get(
    "/body-parts",
    response_model=BodyPartListResponse,
    summary="부위 마스터 목록",
    description=(
        "Sapiens 전체 클래스와 표시용 한글 라벨·오버레이 색을 반환합니다. "
        "⚠️ 프론트는 색을 하드코딩하지 말고 이 응답의 color_hex를 사용하세요. "
        "color_hex가 null인 클래스(배경·옷 등)는 칠하지 않습니다."
    ),
)
async def list_body_parts() -> BodyPartListResponse:
    return BodyPartListResponse(items=[BodyPart(**r) for r in db.list_body_parts()])
