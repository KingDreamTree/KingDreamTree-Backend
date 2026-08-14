"""F12 코치 대화 DTO.

⚠️ 대화는 stateless — 서버가 저장하지 않고 클라이언트가 messages 를 왕복시킨다.
   설계 근거: services/coach_chat.py 모듈 주석 §1.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CoachChatRequest(BaseModel):
    """POST /sessions/{id}/coach-chat — 대화 1턴.

    messages 는 직전 응답의 messages 를 그대로 + 새 user 발화를 붙여 보낸다.
    첫 턴은 user 발화 1개(또는 빈 배열 — 코치가 먼저 인사)로 시작한다.
    """

    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=64)

    model_config = {
        "json_schema_extra": {
            "example": {"messages": [{"role": "user", "content": "스쿼트 할 때 무릎이 좀 아팠어"}]}
        }
    }


class FinalizedCard(BaseModel):
    """대화 마지막 요약 카드. 이게 오면 프론트는 [적용]/[그대로 둘게요]를 띄운다."""

    summary: str
    changes: list[dict[str, str]] = Field(default_factory=list)


class CoachChatResponse(BaseModel):
    reply: str
    #: 다음 요청에 그대로 되돌려 보낼 전체 히스토리
    messages: list[dict[str, Any]]
    #: 이번 턴에 검증을 통과한 도구 호출 (UI 배지: "무릎 → 주의 부위 등록됨")
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    finalized: FinalizedCard | None = None
    turn: int
    max_turns: int


class CoachApplyRequest(BaseModel):
    """POST /sessions/{id}/coach-chat/apply — [적용] 버튼.

    messages 전체를 다시 보낸다. 서버가 도구 호출을 **재수집·재검증**해 적용하므로
    히스토리가 조작돼도 규칙 밖 변경은 나갈 수 없다.
    """

    messages: list[dict[str, Any]] = Field(min_length=1, max_length=64)


class CoachApplyResponse(BaseModel):
    month_routine_id: UUID
    version: int
    applied_changes: list[dict[str, Any]] = Field(default_factory=list)
    contraindications_added: list[dict[str, Any]] = Field(default_factory=list)
    #: 변경이 하나도 없으면 새 버전을 만들지 않는다 — 그 사실을 알린다
    no_change: bool = False
