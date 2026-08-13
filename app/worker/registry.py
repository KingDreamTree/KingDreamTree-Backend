"""잡 kind → 핸들러 등록소.

⚠️ **왜 run.py 가 아니라 별도 모듈인가**

    워커는 `python -m app.worker.run` 으로 띄운다. 이때 run.py 는 `__main__`
    이라는 이름으로 로드된다. 그런데 핸들러가 `from app.worker.run import register`
    를 하면, 파이썬은 run.py 를 **`app.worker.run` 이라는 두 번째 모듈로 다시
    로드**한다. 같은 파일이지만 서로 다른 모듈 객체이고, 전역 변수도 별개다.

    그러면 등록은 사본 쪽 HANDLERS 에 들어가고 `__main__` 쪽 HANDLERS 는
    비어 있는 채로 남아, 워커가 "핸들러가 없는 kind" 로 죽는다.
    import 순서만 보면 멀쩡해 보여서 원인을 찾기 어렵다.

    등록소를 여기 한 곳에 두면 누가 어느 이름으로 로드되든 같은 딕셔너리를 본다.

핸들러 추가하는 법 (담당 A·B 공통)

    from app.worker.registry import register

    def _handle(job: dict) -> dict:
        ...

    register(JobKind.OCR_INBODY, _handle)
"""

from __future__ import annotations

from typing import Callable

from app.schemas.enums import JobKind

#: kind → 핸들러. 담당별로 app/worker/handlers/ 아래에서 등록한다.
HANDLERS: dict[JobKind, Callable[[dict], dict | None]] = {}

#: 기동 전 점검. 실패하면 워커가 아예 안 뜬다.
#: ⚠️ 설정이 틀렸다는 걸 **잡을 몇 번 말아먹은 뒤에** 알게 되면 안 된다.
#:    모델 경로나 API 키처럼 "없으면 어차피 다 실패할" 것들을 여기서 본다.
PREFLIGHTS: list[Callable[[], None]] = []


def register(kind: JobKind, fn: Callable[[dict], dict | None]) -> None:
    HANDLERS[kind] = fn


def register_preflight(fn: Callable[[], None]) -> None:
    PREFLIGHTS.append(fn)
