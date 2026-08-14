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


# ⚠️ RoutineRequest / Exercise / RoutineResponse 는 2026-08-14 에 지웠다.
#    v1 의 `POST /routine` 요청·응답 DTO 였고 `analysis_id`(= /analyze 응답)를
#    참조했는데, analysis 테이블도 그 엔드포인트도 v4 에서 사라졌다.
#    analyze.py · compare.py 를 지울 때 같이 빠졌어야 할 것들이다.
#    F10 은 위의 RoutineGenerate* 를 쓴다. docs/removed-code.md 참고.
