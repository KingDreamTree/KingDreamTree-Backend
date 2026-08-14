from typing import Any

from pydantic import BaseModel, Field

# ── F10 — 루틴 생성 ──────────────────────────────────────────────────────────


class InbodySnapshot(BaseModel):
    """루틴 생성에 쓰이는 인바디 수치 (선택). 전체 inbody 행이 아니라 필요한 필드만.

    ⚠️ **필드명이 DB 컬럼과 일부러 다르다** (프롬프트 가독성용).
       그래서 DB에서 채울 때는 반드시 `from_inbody()` 를 쓴다. 손으로 dict를
       만들면 이름 하나만 어긋나도 조용히 None이 들어가 프롬프트에서 수치가
       통째로 빠진다 — 에러 없이 루틴 품질만 나빠지는 종류의 사고다.

       DTO            ← DB
       skeletal_muscle_kg ← skeletal_muscle_mass
       body_fat_pct       ← body_fat_percentage
       visceral_fat_level ← raw_ocr (컬럼 아님. db-design-v4 §7 항등식 전용 항목)
    """

    weight_kg: float | None = None
    skeletal_muscle_kg: float | None = None
    body_fat_pct: float | None = None
    bmi: float | None = None
    smi: float | None = None
    visceral_fat_level: int | None = None
    segmental_muscle: dict[str, float | None] | None = None

    @classmethod
    def from_inbody(
        cls,
        row: dict[str, Any],
        segments: list[dict[str, Any]] | None = None,
        smi: float | None = None,
    ) -> "InbodySnapshot":
        """inbody 행(+ inbody_segment 행들) → 스냅샷. **DB→DTO 매핑은 여기 한 곳뿐.**

        Args:
            row: inbody 테이블 행
            segments: inbody_segment 행들 (segment/lean_mass)
            smi: ocr.calc_smi(row) 결과. ⚠️ DB 컬럼이 아니라 파생값이라 주입받는다.
        """
        raw = row.get("raw_ocr") or {}
        return cls(
            weight_kg=row.get("weight"),
            skeletal_muscle_kg=row.get("skeletal_muscle_mass"),
            body_fat_pct=row.get("body_fat_percentage"),
            bmi=row.get("bmi"),
            smi=smi,
            # 컬럼으로 만들지 않기로 한 항목 — raw_ocr 에서만 읽는다.
            visceral_fat_level=raw.get("visceral_fat_level"),
            segmental_muscle=(
                {s["segment"]: s.get("lean_mass") for s in segments} if segments else None
            ),
        )


class RoutineGenerateRequest(BaseModel):
    """POST /routines 요청 — 4주 루틴 생성."""

    model_config = {
        "json_schema_extra": {
            "example": {
                "exercise_days_per_week": 3,
                "overall_diagnosis": {
                    "body_type": "역삼각형",
                    "overall_score": 72,
                    "priority_improvements": ["Left_Upper_Arm", "Right_Upper_Arm", "Torso"],
                    "weak_points": ["어깨 전면 근력 부족", "코어 안정성"],
                    "strong_points": ["하체 균형"],
                    "summary": "어깨 너비가 레퍼런스 대비 좁고 코어 안정성이 부족합니다.",
                },
                "inbody": {
                    "weight_kg": 72.5,
                    "skeletal_muscle_kg": 33.2,
                    "body_fat_pct": 18.4,
                    "bmi": 22.1,
                },
            }
        }
    }

    exercise_days_per_week: int = Field(ge=1, le=7, description="주당 운동 가능 일수 (1~7)")
    overall_diagnosis: dict[str, Any] = Field(
        description="F09 종합 진단 결과 (VLM_OVERALL job.result)"
    )
    inbody: InbodySnapshot | None = Field(
        default=None, description="인바디 데이터. 없으면 세그 데이터만으로 루틴 생성"
    )


class RoutineGenerateResponse(BaseModel):
    """POST /routines 응답."""

    job_id: str = Field(description="ROUTINE_GEN 잡 ID. GET /jobs/{job_id}로 폴링")
    month_routine_id: str | None = Field(
        default=None, description="생성 완료 시 채워짐 (동기 처리 시)"
    )


# ── F10 일수 변경 ─────────────────────────────────────────────────────────────


class RoutineDaysChangeRequest(BaseModel):
    """POST /routines/{id}/change-days — 운동 일수 변경 → 새 버전 생성."""

    exercise_days_per_week: int = Field(ge=1, le=7)


# ── F11 오늘의 루틴 ───────────────────────────────────────────────────────────


class DayRoutineExercise(BaseModel):
    order_index: int
    name: str
    equipment: str | None
    target_muscle: str | None
    sets: int
    reps: int | None
    weight_kg: float | None
    rest_sec: int | None
    note: str | None


class DayRoutineResponse(BaseModel):
    day_number: int
    is_rest: bool
    title: str | None
    estimated_duration_min: int | None
    exercises: list[DayRoutineExercise]
    disclaimer: str = "weight_kg는 LLM 추정치입니다. 본인의 체력에 맞게 조정하세요."


# ── 구 스캐폴드 (참고용, 라우터 미등록) ───────────────────────────────────────


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
