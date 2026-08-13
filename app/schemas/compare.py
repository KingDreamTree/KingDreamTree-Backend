from pydantic import BaseModel, Field

from app.schemas.analyze import SegmentationResult


class CompareRequest(BaseModel):
    """POST /compare 요청."""

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
                    "mask_url": None,
                },
                "ref_seg": {
                    "keypoints": {
                        "shoulder_width": 45.0,
                        "hip_width": 36.5,
                        "waist_width": 28.8,
                        "height_px": 512,
                    },
                    "mask_url": None,
                },
            }
        }
    }

    analysis_id: str = Field(description="/analyze 응답의 analysis_id")
    user_seg: SegmentationResult
    ref_seg: SegmentationResult


class CompareResponse(BaseModel):
    """POST /compare 응답."""

    model_config = {
        "json_schema_extra": {
            "example": {
                "analysis_id": "a1b2c3d4-0000-0000-0000-000000000000",
                "comparison": {
                    "summary": "어깨 너비가 레퍼런스 대비 5.9% 좁고, 허리 너비는 비슷한 수준입니다.",
                    "differences": {"shoulder_width": -2.5, "hip_width": 1.5, "waist_width": 1.4},
                    "body_type": "역삼각형",
                    "overlay_url": "https://example.supabase.co/storage/v1/object/public/overlays/overlay_abc.jpg",
                },
            }
        }
    }

    analysis_id: str
    comparison: dict = Field(description="Claude Call1 체형 비교 분석 결과 JSON")
