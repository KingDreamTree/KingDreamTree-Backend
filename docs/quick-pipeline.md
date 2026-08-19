# 퀵 파이프라인 — 실시간 웹캠 경로 (2026-08-19)

> 분석 파이프라인을 둘로 분리했다. **기존 사진 업로드 경로는 한 줄도 다르게
> 동작하지 않는다** — `pipeline`/`mode` 를 안 주면 이전과 완전히 같다
> (`verify_quick.py` §3 이 이 불변을 회귀로 잡는다).

## 두 경로

| | 사진 업로드 (기존) | 웹캠 퀵 (신규) |
|---|---|---|
| 브라우저 | MediaPipe 게이트 + 점수 | MediaPipe 게이트 + 점수 (동일 — 촬영 가능 여부만 판단) |
| 서버 전송 | 최종 사진 1장 | 최종 프레임 1장 (실시간 스트림 없음 — 동일) |
| 세그멘테이션 | Sapiens2 (GPU 워커) | **없음** — 세그 잡 자체를 안 건다 |
| 부위별 진단 (F08) | 오버레이 4장 + 수치 → 부위 카드 9장 | **없음** |
| 종합 진단 | F09 — 부위 결과 + 원본 2장 | **퀵 진단** — 원본 2장 + 인바디만 |
| 유사도 점수 | RULE (부위 등급 합산) | **없음** (null + 사유 명시) |
| 우선 부위 | 규칙(rank_priority)이 격차로 선정 | **빈 배열** — 억지 부위 진단 금지 |
| 개선 방향 | 규칙 (decide_direction) | 규칙 (**decide_direction_quick**) |
| 루틴 | 전신 + 약점 가중(L2) | 전신 기본 볼륨 + 모드(인바디 규칙) |
| 저장/조회 | overall_diagnosis · GET /analysis | **같은 테이블 · 같은 경로** |

## 설계 결정과 이유

**JobKind 를 새로 만들지 않았다.** `job.kind` 에 DB CHECK 가 걸려 있고
(실측 23514), PostgREST 로는 DDL 을 못 돌린다. `VLM_OVERALL` +
`payload.mode="quick"` 으로 분기하면 워커 풀·배포 compose·좀비 회수가
전부 무수정으로 동작한다.

**부위별 결과를 흉내내지 않는다.** 한 프레임을 훑어본 인상으로 부위 등급을
만들면, F08 이 오버레이·좌우 대칭·옷 흡수 신호까지 놓고 내리는 판정과 같은
무게로 화면에 실린다. 퀵의 부위 카드는 "없음"이 정직한 값이다.

**점수도 만들지 않는다.** 점수는 부위 등급의 규칙 합산(RULE)이다. 등급이
없는데 점수를 내면 "웹캠 60점 vs 사진 55점" 같은 비교 불가능한 숫자가
공존한다. `similarity_score=null` + `score_rationale` 에 사유를 남긴다.

**우선 부위도 비운다.** 루틴의 볼륨 가중(L2)은 "왜 이 부위인가"에 데이터로
답할 수 있어야 한다 (rank_priority 의 존재 이유). 전체 인상만으로 특정
부위에 세트를 얹는 것은 그 원칙의 위반이라, 퀵 루틴은 **전신 기본 볼륨 +
모드(인바디 규칙)** 로 간다 — D10(진단은 가중치이지 구성 요소가 아니다)
그대로다.

**방향 규칙은 퀵 전용이 따로 있다** (`scoring.decide_direction_quick`).
기존 `decide_direction` 은 부위 등급이 없으면 LIMITED("재촬영하세요" 신호)를
내는데, 퀵은 **설계상** 부위가 없는 것이라 그 문구가 틀리다. 퀵 규칙:

    CUT(체지방률 실측)        → FAT_LOSS_FIRST  (루틴 decide_mode 와 같은 규칙 승계)
    BALANCE(체지방률 실측)    → STRENGTH_FIRST
    인바디 없음               → STRENGTH_FIRST — 감량 필요 여부는 **판단하지 않는다**

## API 계약 (프론트)

```
① POST /sessions/{id}/photos/reference   form 에 pipeline=quick 추가
② POST /sessions/{id}/photos/user        form 에 pipeline=quick 추가
   → 두 응답 모두 job_id 가 null (세그 잡 없음 — 폴링할 것 없음)
③ POST /sessions/{id}/analysis?mode=quick
   → 202 { part_job_id: null, overall_job_id: "...", part_count: 0 }
④ GET  /sessions/{id}/analysis/progress   completed 가 true 될 때까지 폴링
   (part.status 는 종합 상태를 그대로 비춘다 — 유령 PENDING 없음)
⑤ GET  /sessions/{id}/analysis
   → parts: []  ·  overall: { similarity_score: null, priority_parts: [],
     summary / silhouette / key_differences / user_profile / reference_profile /
     realistic_direction / exercise_strategy 는 사진 모드와 동일 형태 }
⑥ POST /routines · today · workout-logs · coach-chat — 전부 기존과 동일
```

프론트 헬퍼: `api.ts` 의 `uploadReferencePhoto/uploadUserPhoto` 에
`pipeline: 'quick'` 옵션, `startQuickAnalysis(sessionId)` 추가됨.

화면 규칙: **부위 카드 UI 와 점수 UI 를 렌더하지 않는다** (parts 가 빈 배열,
score 가 null 인 것으로 분기 가능 — 모드 플래그를 따로 저장할 필요 없음).

## 검증

`python scripts/verify_quick.py` — 프롬프트 계약(측정·부위판정·점수 금지,
기준선 분리, 완성 예문 없음) + 실제 DB 왕복(업로드→진단→조회→루틴, mock)
+ **기존 파이프라인 불변 회귀**.
