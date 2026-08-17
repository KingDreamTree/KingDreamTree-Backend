"""F06 — 세그멘테이션 조회 스키마.

⚠️ 이 응답의 핵심은 `palette` 다. label_map + body_part 마스터 + 부위별 통계를
   **한 덩어리로 합쳐서** 내려준다. 프론트가 세 군데를 따로 조회해 맞추게 두면
   부위가 추가되거나 이름이 바뀔 때 색과 라벨이 어긋난다.
"""

from pydantic import BaseModel, Field

from app.schemas.enums import PhotoKind


class BBox(BaseModel):
    """⚠️ **맵 좌표계** 기준이다. 원본 위에 그리려면 photo_width / map_width 배율로 스케일할 것."""

    x: int
    y: int
    w: int
    h: int


class PaletteEntry(BaseModel):
    """맵의 픽셀 값 하나에 대한 모든 것 — 이름·색·유효성·통계."""

    label_value: int = Field(description="맵 PNG의 픽셀 값. ⚠️ 조인 키로 쓰지 말 것")
    class_name: str = Field(description="조인·비교에 쓰는 식별자")
    name_ko: str
    color_hex: str | None = Field(description="NULL이면 칠하지 않는다 (배경·옷·머리 등)")
    is_comparable: bool
    is_valid: bool
    invalid_reason: str | None = None
    pixel_count: int
    area_ratio: float = Field(description="segmentation.person_pixel_count 대비 비율")
    bbox: BBox
    #: ⚠️ 화면 가장자리에 닿아 잘렸을 수 있다는 표시. **무효 처리하지는 않는다** —
    #  전신 사진은 발이 바닥 경계에 닿는 게 정상이라, 잘림만으로 빼면 너무 많이 빠진다.
    #  대신 굵기 값의 신뢰도가 낮다는 신호로 쓴다. 저장은 하고 있었는데 응답에는
    #  안 나가고 있어서 프론트가 알 방법이 없었다.
    is_truncated: bool = False


class ModelInfo(BaseModel):
    name: str
    version: str


class SegmentationResponse(BaseModel):
    segmentation_id: str
    photo_id: str
    kind: PhotoKind

    map_url: str = Field(description="8-bit 그레이스케일 PNG. 픽셀 값 = label_value")
    merged_map_url: str | None = Field(
        default=None,
        description=(
            "오버레이용 **병합 맵**. 옷 픽셀이 인접 부위로 흡수된 상태라 palette 통계와"
            " 일치한다. 하이라이트는 이걸 쓰세요 — map_url(원본)을 쓰면 옷 부분이"
            " 빠져 듬성듬성 칠해진다. 옛 세션은 null 이므로 map_url 로 폴백할 것."
        ),
    )
    map_width: int
    map_height: int

    photo_url: str
    photo_width: int | None = None
    photo_height: int | None = None

    model: ModelInfo
    person_area_ratio: float
    palette: list[PaletteEntry]

    signed_url_expires_at: str


class ExcludedPart(BaseModel):
    """비교에서 빠진 부위 — **왜 빠졌는지 설명하기 위해** 존재한다.

    이게 없으면 결과 화면에서 "왼팔은 왜 없지?"에 답할 수 없다.
    """

    class_name: str
    name_ko: str
    side: str = Field(description="REFERENCE | USER | BOTH")
    reason: str | None = None
    message: str = Field(description="사용자에게 그대로 보여줘도 되는 문구")


class ComparableSummary(BaseModel):
    class_names: list[str] = Field(description="레퍼런스·사용자 양쪽에서 유효한 비교 대상")
    count: int
    sufficient: bool
    min_required: int
    reference_only: list[str] = Field(default_factory=list)
    user_only: list[str] = Field(default_factory=list)
    excluded: list[ExcludedPart] = Field(default_factory=list)


class SessionSegmentationResponse(BaseModel):
    """좌우 비교 화면을 한 번에 그릴 수 있게 묶은 응답."""

    reference: SegmentationResponse | None = None
    user: SegmentationResponse | None = None
    comparable: ComparableSummary
