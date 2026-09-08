"""사진 업로드 응답 스키마 (F04 / F05).

⚠️ 요청은 multipart/form-data 라 pydantic 모델로 받지 않는다.
   폼 필드 정의는 app/routes/photos.py 에 있다.
"""

from pydantic import BaseModel, Field

from app.schemas.enums import CaptureSource, PhotoKind, PoseScaleBasis


class PoseLandmark(BaseModel):
    """MediaPipe Pose 랜드마크 하나. 좌표는 0~1 정규화 값.

    ⚠️ 반전되지 않은 카메라 원본 기준이다. 거울 사진은 서버가 저장 전에 되돌린다.
    """

    index: int = Field(description="0~32. MediaPipe Pose 랜드마크 번호")
    x: float
    y: float
    z: float = 0.0
    visibility: float = 0.0


class PhotoBase(BaseModel):
    photo_id: str
    kind: PhotoKind
    width: int | None = None
    height: int | None = None
    pose_scale_basis: PoseScaleBasis | None = None
    was_mirrored: bool = Field(
        default=False,
        description="거울 촬영으로 접수돼 서버가 좌우를 되돌려 저장했는지",
    )
    #: 팟 경로(사진 미저장)에서 팟이 세그 전에 적용한 크롭. 프론트는 기기 원본을
    #  flipped 면 좌우 반전한 뒤 이 박스로 잘라 그 위에 맵을 얹는다.
    #  종전 경로(사진 저장)에서는 null — signed_url 의 크롭본을 그대로 쓴다.
    crop_box: dict | None = Field(
        default=None,
        description="{x, y, w, h, source_width, source_height, flipped} — 비반전 원본 픽셀 좌표",
    )
    created_at: str


class ReferencePhotoResponse(PhotoBase):
    """레퍼런스 업로드 응답.

    ⚠️ 촬영 화면이 실시간 비교의 기준으로 쓰므로 landmarks 를 그대로 돌려준다.
       거울 사진이었다면 **되돌린 뒤의** 값이다.
    """

    #: ⚠️ 조회 시점에 잡이 하나도 없으면 null 이다. 예전에는 빈 문자열("")을 넣었는데,
    #  프론트가 그걸 유효한 id 로 보고 GET /jobs/ 를 호출하는 사고가 난다.
    job_id: str | None = Field(
        default=None, description="SEG_REFERENCE 잡. GET /jobs/{job_id} 로 폴링. 없으면 null"
    )
    pose_landmarks: list[PoseLandmark]
    #: ⚠️ 팟 경로(PHOTO_PIPELINE=pod)에서는 null — 서버에 사진이 없다. 화면은 기기
    #  원본을 쓴다 (crop_box 참고).
    signed_url: str | None = None
    signed_url_expires_at: str | None = None
    segmented: bool = Field(default=False, description="세그멘테이션 완료 여부")


class UploadTokenResponse(BaseModel):
    """POST /sessions/{id}/upload-token — 팟 업로드용 일회용 토큰.

    ⚠️ 팟 주소는 여기 없다. 프론트 빌드에 고정한다 (services/upload_token.py 주석).
    ⚠️ 토큰은 팟이 **검증하는 순간** 소모된다 — 그 뒤 응답이 422(재촬영)·503·409 여도
       같은 토큰은 401 이다. 프론트는 어떤 응답이든 재시도 전에 여기서 새로 받는다.
    """

    token: str
    expires_at: int = Field(description="만료 epoch 초 (발급 후 약 2분)")
    session_id: str


class UserPhotoResponse(PhotoBase):
    """사용자 사진 저장 응답. 임계값을 통과했을 때만 반환된다."""

    #: ⚠️ 퀵 파이프라인(pipeline=quick)은 세그 잡을 걸지 않으므로 null 이다.
    job_id: str | None = Field(default=None, description="SEG_USER 잡 (quick 이면 null)")
    capture_source: CaptureSource
    pose_similarity: float
    framing_score: float
    multi_person: bool
