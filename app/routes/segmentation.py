"""F06 — 부위별 세그멘테이션 조회 + 시각화용 팔레트.

⚠️ 이 파일이 하는 일의 핵심은 **팔레트 조립**이다.
   맵 PNG는 픽셀에 숫자만 들어있어 그 자체로는 아무 의미가 없다.
   숫자 ↔ 부위명 ↔ 색 ↔ 유효성 ↔ 통계를 여기서 한 덩어리로 합쳐서 내려준다.

   프론트가 label_map / body_part 마스터 / 통계를 따로 받아 맞추게 두면,
   부위가 추가되거나 모델이 바뀔 때 색과 라벨이 어긋난다. 그것도 조용히.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.config import settings
from app.deps import OwnedPhoto, OwnedSession
from app.errors import not_found
from app.schemas.enums import InvalidReason, PhotoKind
from app.schemas.segmentation import (
    BBox,
    ComparableSummary,
    ExcludedPart,
    ModelInfo,
    PaletteEntry,
    SegmentationResponse,
    SessionSegmentationResponse,
)
from app.services import db, storage

router = APIRouter(tags=["segmentation"])

#: invalid_reason → 사용자에게 보여줄 문구. 재촬영 안내를 만드는 데 쓴다.
#  ⚠️ "왜 빠졌는지"를 말해주지 못하면 사용자는 뭘 고쳐야 할지 모른다.
#  ⚠️ 조사는 {이가} {은는} {을를} 로 쓴다. "몸통이" / "왼쪽 허벅지가" 처럼
#     받침에 따라 골라 넣는다 — "이(가)"를 그대로 노출하면 읽기 나쁘다.
REASON_MESSAGE: dict[str, str] = {
    InvalidReason.TOO_SMALL: "{name}{이가} 거의 보이지 않습니다. 옷에 가려졌을 수 있어요.",
    InvalidReason.TOO_SMALL_RATIO: "{name}{이가} 너무 작게 나왔습니다. 조금 더 가까이 서주세요.",
    # ⚠️ 지금은 아무도 TRUNCATED 를 invalid_reason 으로 넣지 않는다. 잘림은 무효 사유가
    #    아니라 별도 표시(PaletteEntry.is_truncated)로 나간다 — 전신 사진은 발이
    #    바닥 경계에 닿는 게 정상이라 무효로 치면 너무 많이 빠진다.
    #    기준을 바꾸게 되면 여기 문구가 그대로 쓰인다.
    InvalidReason.TRUNCATED: "{name}{이가} 화면에서 잘렸습니다. 전신이 나오게 찍어주세요.",
    InvalidReason.NOT_COMPARABLE: "{name}{은는} 비교 대상이 아닙니다.",
}
DEFAULT_MESSAGE = "{name}{을를} 비교할 수 없습니다."


def _has_final_consonant(word: str) -> bool:
    """마지막 글자에 받침이 있는지. 한글이 아니면 없는 것으로 본다."""
    if not word:
        return False
    last = word[-1]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - ord("가")) % 28 != 0


def _message_for(name_ko: str, reason: str | None) -> str:
    template = REASON_MESSAGE.get(reason or "", DEFAULT_MESSAGE)
    final = _has_final_consonant(name_ko)
    return template.format(
        name=name_ko,
        이가="이" if final else "가",
        은는="은" if final else "는",
        을를="을" if final else "를",
    )


def _build(
    photo: dict[str, Any],
    master: dict[str, dict[str, Any]] | None = None,
) -> SegmentationResponse | None:
    """photo 행 하나 → 팔레트가 조립된 응답. 세그가 아직이면 None.

    ⚠️ master 를 받으면 다시 조회하지 않는다. 세션 조회는 이 함수를 두 번 부르는데,
       부위 마스터는 두 사진에 대해 같은 값이라 두 번 읽을 이유가 없다.
    """
    photo_id = UUID(str(photo["photo_id"]))
    segmentation = db.get_segmentation(photo_id)
    if segmentation is None:
        return None

    if master is None:
        master = {row["class_name"]: row for row in db.list_body_parts()}
    parts = db.list_part_segments(UUID(str(segmentation["segmentation_id"])))

    palette: list[PaletteEntry] = []
    for part in parts:
        meta = master.get(part["class_name"])
        if meta is None:
            # 마스터에 없는 클래스 — 워커가 label_map 검증에서 걸러내므로 정상 경로에서는 없다.
            continue
        palette.append(
            PaletteEntry(
                label_value=part["label_value"],
                class_name=part["class_name"],
                name_ko=meta["name_ko"],
                color_hex=meta["color_hex"],
                is_comparable=meta["is_comparable"],
                is_valid=part["is_valid"],
                invalid_reason=part["invalid_reason"],
                pixel_count=part["pixel_count"],
                area_ratio=part["area_ratio"],
                bbox=BBox(x=part["bbox_x"], y=part["bbox_y"], w=part["bbox_w"], h=part["bbox_h"]),
                is_truncated=bool(part.get("is_truncated")),
            )
        )

    # 범례 정렬을 서버가 정한다. 프론트가 각자 정렬하면 화면마다 순서가 달라진다.
    palette.sort(key=lambda p: (master[p.class_name]["display_order"], p.class_name))

    map_url, expires_at = storage.signed_url(
        segmentation["storage_bucket"], segmentation["map_path"]
    )
    photo_url, _ = storage.signed_url(photo["storage_bucket"], photo["storage_path"])

    return SegmentationResponse(
        segmentation_id=str(segmentation["segmentation_id"]),
        photo_id=str(photo_id),
        kind=photo["kind"],
        map_url=map_url,
        map_width=segmentation["map_width"],
        map_height=segmentation["map_height"],
        photo_url=photo_url,
        photo_width=photo["width"],
        photo_height=photo["height"],
        model=ModelInfo(name=segmentation["model_name"], version=segmentation["model_version"]),
        person_area_ratio=segmentation["person_area_ratio"],
        palette=palette,
        signed_url_expires_at=expires_at.isoformat(),
    )


@router.get(
    "/photos/{photo_id}/segmentation",
    response_model=SegmentationResponse,
    summary="라벨 맵 + 팔레트 + 부위별 통계",
)
async def get_photo_segmentation(photo: OwnedPhoto) -> SegmentationResponse:
    result = _build(photo)
    if result is None:
        raise not_found("세그멘테이션 결과")
    return result


def _valid_comparable(result: SegmentationResponse | None) -> set[str]:
    if result is None:
        return set()
    return {p.class_name for p in result.palette if p.is_valid and p.is_comparable}


def _excluded(
    result: SegmentationResponse | None, side: str, compared: set[str]
) -> list[ExcludedPart]:
    """비교 대상인데 이쪽에서 유효하지 않아 비교에서 빠진 부위들.

    ⚠️ 제외 기준은 "실제로 비교된 부위(compared)에 없는가" 하나다.
       한쪽에서만 유효한 경우가 제일 중요하다 — 레퍼런스엔 오른팔이 잘 나왔는데
       사용자 사진에서 옷에 가려졌다면, 그게 바로 재촬영 안내를 내야 할 상황이다.
    """
    if result is None:
        return []
    return [
        ExcludedPart(
            class_name=p.class_name,
            name_ko=p.name_ko,
            side=side,
            reason=p.invalid_reason,
            message=_message_for(p.name_ko, p.invalid_reason),
        )
        for p in result.palette
        if p.is_comparable and not p.is_valid and p.class_name not in compared
    ]


@router.get(
    "/sessions/{session_id}/segmentation",
    response_model=SessionSegmentationResponse,
    summary="레퍼런스·사용자 두 장 + 비교 가능 부위",
)
async def get_session_segmentation(session: OwnedSession) -> SessionSegmentationResponse:
    """좌우 비교 화면을 한 번에 그릴 수 있게 묶는다.

    ⚠️ 세그가 아직 안 끝난 쪽은 null 로 내려간다. 404를 주면 한쪽만 끝난 상태에서
       화면을 아예 못 그린다. 진행 상태는 GET /jobs/{id} 가 소스다.
    """
    session_id = UUID(str(session["session_id"]))

    reference_photo = db.get_photo(session_id, PhotoKind.REFERENCE)
    user_photo = db.get_photo(session_id, PhotoKind.USER)

    # 두 사진이 같은 마스터를 쓴다 — 한 번만 읽는다.
    master = {row["class_name"]: row for row in db.list_body_parts()}
    reference = _build(reference_photo, master) if reference_photo else None
    user = _build(user_photo, master) if user_photo else None

    ref_valid = _valid_comparable(reference)
    user_valid = _valid_comparable(user)
    both = sorted(ref_valid & user_valid)

    # 양쪽에서 빠진 부위는 한 번만 알려준다 — 같은 안내가 두 줄 나오면 지저분하다.
    compared = set(both)
    ref_excluded = _excluded(reference, "REFERENCE", compared)
    user_excluded = _excluded(user, "USER", compared)

    seen: set[str] = set()
    merged: list[ExcludedPart] = []
    for item in ref_excluded + user_excluded:
        if item.class_name in seen:
            # 양쪽 모두에서 빠졌다면 side 를 BOTH 로 올린다
            for existing in merged:
                if existing.class_name == item.class_name:
                    existing.side = "BOTH"
            continue
        seen.add(item.class_name)
        merged.append(item)

    return SessionSegmentationResponse(
        reference=reference,
        user=user,
        comparable=ComparableSummary(
            class_names=both,
            count=len(both),
            sufficient=len(both) >= settings.min_comparable_parts,
            min_required=settings.min_comparable_parts,
            reference_only=sorted(ref_valid - user_valid),
            user_only=sorted(user_valid - ref_valid),
            excluded=merged,
        ),
    )
