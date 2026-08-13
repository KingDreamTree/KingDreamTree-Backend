from pydantic import BaseModel, Field


class RoutineRequest(BaseModel):
    """POST /routine 요청."""

    model_config = {
        "json_schema_extra": {
            "example": {
                "analysis_id": "a1b2c3d4-0000-0000-0000-000000000000",
                "comparison": {
                    "summary": "어깨 너비가 레퍼런스 대비 5.9% 좁습니다.",
                    "differences": {"shoulder_width": -2.5},
                    "body_type": "역삼각형",
                },
            }
        }
    }

    analysis_id: str = Field(description="/analyze 응답의 analysis_id")
    comparison: dict = Field(description="/compare 응답의 comparison 필드")


class Exercise(BaseModel):
    name: str = Field(example="덤벨 숄더 프레스")
    sets: int = Field(example=3)
    reps: str = Field(example="10-12")
    reason: str = Field(example="어깨 너비 강화를 위해 전면·측면 삼각근 활성화")


class RoutineResponse(BaseModel):
    """POST /routine 응답."""

    model_config = {
        "json_schema_extra": {
            "example": {
                "analysis_id": "a1b2c3d4-0000-0000-0000-000000000000",
                "routine": {
                    "goal": "어깨 라인 보완 및 상체 균형 개선",
                    "frequency": "주 3회",
                    "exercises": [
                        {
                            "name": "덤벨 숄더 프레스",
                            "sets": 3,
                            "reps": "10-12",
                            "reason": "전면·측면 삼각근 활성화",
                        },
                        {
                            "name": "사이드 레터럴 레이즈",
                            "sets": 3,
                            "reps": "12-15",
                            "reason": "측면 삼각근 발달로 어깨 너비 강화",
                        },
                        {
                            "name": "페이스 풀",
                            "sets": 3,
                            "reps": "15",
                            "reason": "후면 삼각근 및 회전근개 안정화",
                        },
                    ],
                },
            }
        }
    }

    analysis_id: str
    routine: dict = Field(description="Claude Call2 개인화 운동 루틴 JSON")
