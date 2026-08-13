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
    created_at: str


class ReferencePhotoResponse(PhotoBase):
    """레퍼런스 업로드 응답.

    ⚠️ 촬영 화면이 실시간 비교의 기준으로 쓰므로 landmarks 를 그대로 돌려준다.
       거울 사진이었다면 **되돌린 뒤의** 값이다.
    """

    job_id: str = Field(description="SEG_REFERENCE 잡. GET /jobs/{job_id} 로 폴링")
    pose_landmarks: list[PoseLandmark]
    signed_url: str
    signed_url_expires_at: str
    segmented: bool = Field(default=False, description="세그멘테이션 완료 여부")


class UserPhotoResponse(PhotoBase):
    """사용자 사진 저장 응답. 임계값을 통과했을 때만 반환된다."""

    job_id: str = Field(description="SEG_USER 잡")
    capture_source: CaptureSource
    pose_similarity: float
    framing_score: float
    multi_person: bool
