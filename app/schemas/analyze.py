from pydantic import BaseModel, Field


class SegmentationResult(BaseModel):
    """단일 이미지 세그멘테이션 출력."""

    keypoints: dict = Field(
        default_factory=dict,
        example={
            "shoulder_width": 42.5,
            "hip_width": 38.0,
            "waist_width": 30.2,
            "height_px": 512,
        },
    )
    mask_url: str | None = Field(
        default=None,
        example="https://example.supabase.co/storage/v1/object/public/overlays/mask_abc.jpg",
    )


class AnalyzeResponse(BaseModel):
    """POST /analyze 응답."""

    model_config = {
        "json_schema_extra": {
            "example": {
                "analysis_id": "a1b2c3d4-0000-0000-0000-000000000000",
                "user_seg": {
                    "keypoints": {
                        "shoulder_width": 42.5,
                        "hip_width": 38.0,
                        "waist_width": 30.2,
                        "height_px": 512,
                    },
                    "mask_url": "https://example.supabase.co/storage/v1/object/public/overlays/user_mask.jpg",
                },
                "ref_seg": {
                    "keypoints": {
                        "shoulder_width": 45.0,
                        "hip_width": 36.5,
                        "waist_width": 28.8,
                        "height_px": 512,
                    },
                    "mask_url": "https://example.supabase.co/storage/v1/object/public/overlays/ref_mask.jpg",
                },
            }
        }
    }

    analysis_id: str = Field(description="DB에 저장된 분석 레코드 UUID")
    user_seg: SegmentationResult = Field(description="사용자 이미지 세그멘테이션 결과")
    ref_seg: SegmentationResult = Field(description="레퍼런스 이미지 세그멘테이션 결과")
