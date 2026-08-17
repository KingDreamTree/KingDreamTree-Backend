# B 전달 — 죽은 코드·잔재 정리 목록 (2026-08-17)

> A가 리포 전체 잔재 정리를 하면서 발견한 것 중 **B 영역이라 손대지 않은 것들**이다.
> (합의대로 A는 B 파트를 직접 고치지 않는다. 판단과 수정은 B가.)
> A 영역 정리 내역은 같은 날 커밋 로그 참고.

## 1. 죽은 코드 (참조 0건 — 지워도 되는지 확인 요청)

| 위치 | 대상 | 근거 |
|---|---|---|
| `app/services/vlm.py:406` | `_clamp_score()` | 호출 0건. "VLM이 점수를 직접 낸다" 설계의 잔재 — 바로 아래 `parse_overall_response` docstring이 "LLM이 점수를 보내와도 버린다"고 명시 |
| `app/services/vlm.py:324` | `_GAP_SEVERITY` 상수 | 참조 0건. **더 중요한 것**: 주석("통일할 때 나쁜 쪽으로 맞춘다")이 실제 `_unify_pairs()` 동작(gap_level 다르면 건너뜀, 문장 길이로 기준 선택)과 **반대**다. 읽는 사람이 심각도 병합 로직이 있다고 오해한다 |
| `app/services/routine_mode.py:35` | `BMI_CUTOFF = 25.0` | 참조 0건. 같은 파일 110~123행이 "BMI는 트리거가 아니다"라고 폐기 선언했는데 상수와 "폴백 컷오프" 주석만 남았다 |
| `app/services/segmap.py:342` | `part_stats()` | **removed-code.md에 2026-08-14부터 확인 요청이 걸려 있던 그 건.** 여전히 호출 0건 |
| `app/routes/routines.py:22` | `DomainStatus` import | 미사용 import (같은 줄의 다른 것들은 사용 중) |
| `app/services/routine_templates.py:37` | `Any` import | 미사용 import |
| `app/services/coach_chat.py:352` | `call_json` noqa import | "provider 준비 확인용"이라는데 실제로 아무 검증도 안 함 — 의도가 있으면 검증답게, 없으면 삭제 |

## 2. 검증 공백 의심 (죽은 코드보다 우선 확인 권장)

- `scripts/verify_routine_build.py:45` — `INBODY_FIT` 픽스처가 정의만 되고 안 쓰인다.
  **BALANCE 분기가 실제로 검증되지 않고 CUT만 돌고 있을 가능성.** 확인 후
  픽스처를 쓰든 지우든 결정 필요.

## 3. 결정 필요 (A·B 걸침)

- **`scripts/seed_test_data.py` 은퇴 여부** — "B가 A를 기다리지 않기 위한" 초기
  발판이었는데, 지금은 B도 로컬 GPU로 실추론이 되고 `smoke_full_flow.py`가 같은
  역할을 자기완결적으로 한다. B가 아직 쓰면 유지, 아니면 삭제.
- **`body-parts` 버킷 배선** — 업로드하는 코드가 0건인데 배선 4곳이 남아 있다:
  `seg.py:120`(매 잡 delete_prefix), `routes/storage.py:27`(signed URL 허용),
  `db.py:231`(항상 False인 분기), `storage.py:195`(`crop_path()`).
  `db-design-v4.md`는 "VLM 입력이 크롭으로 확정되면 채운다"(계획 보류)라 하고
  `segmap.py:5`는 "안 쓴다"고 단정한다 — **두 문서부터 통일하고** 배선을
  지울지 결정하자. (시연 전엔 안 건드리는 게 안전, 시연 후 정리 권장)
- `requirements-ml.txt`의 `accelerate` — 유일한 소비처(오프로드 분기)가 8/14에
  제거됐다. transformers 5.x가 내부적으로 요구하는지 확인 후 빼면 설치가 가벼워짐.

## 3-1. 🔴 급함 — 거울 매칭 좌우 교차 짝짓기: B 쪽 반영 필요 (2026-08-17 팀 결정)

**배경**: 실시간 촬영(CAPTURE)은 거울 미리보기 + 반전 채점으로 유도되므로,
레퍼런스가 왼팔을 들면 사용자는 **오른팔**을 든다. 같은 이름끼리 비교하면 "든 팔
vs 내린 팔"을 비교하게 된다. A(사용자 결정)가 "회피 불가, 반드시 해결"로 확정.

**A가 이미 한 것**:
- `app/services/part_pairing.py` — 짝짓기 단일 관문. `mirror_class()`,
  `is_cross_paired(user_photo)`(= `capture_source == "CAPTURE"`, 새 필드 없음),
  `reference_class_for(user_class, cross)`. **짝짓기는 반드시 이 모듈만 경유.**
- 세션 비교 API(`routes/segmentation.py`) — 교차 반영 완료. 응답에
  `comparable.cross_paired` 추가, `class_names`는 사용자 기준으로 통일.
- `scripts/verify_part_pairing.py` — 규칙 검증 (런북 등재).

**B가 할 것 — `app/worker/handlers/vlm.py` (부위 진단 짝짓기)**:
현재 레퍼런스/사용자 양쪽을 같은 `class_name` 키로 당긴다. 교차 세션이면
레퍼런스 쪽 접근만 `part_pairing.reference_class_for(name, cross)`로 바꿔야 한다:
1. `cross = part_pairing.is_cross_paired(사용자 photo 행)` 을 컨텍스트에서 한 번 계산
2. `ref_segments.get(name)` → `ref_segments.get(reference_class_for(name, cross))`
   (`_to_diagnosis_rows`의 `reference_segment_id` 포함 — 스키마는 ref/user
   segment_id 를 따로 저장하므로 교차 쌍을 그대로 담을 수 있다)
3. 레퍼런스 오버레이/하이라이트를 칠할 부위 목록도 ref 프레임으로 변환
4. `segmap.compare_parts(ref_segments, user_segments, names, ...)` — names 가
   양쪽 공용 키로 쓰인다면 ref 쪽만 변환해서 넘기도록 수정
5. 진단문 부위명은 **사용자 기준**(사용자의 오른팔이면 "오른팔")으로 유지

⚠️ 반쯤 반영이 최악이다 — 세션 비교(A, 반영됨)와 부위 진단(B)이 다르게 짝지으면
왼팔 카드에 오른팔 근거가 붙고 에러는 안 난다. **동결(8/20) 전에 같이 맞추자.**
검증: 비대칭 포즈 CAPTURE 세션 하나로 세션 비교 API의 쌍과 part_diagnosis 의
segment_id 쌍이 같은 짝인지 대조.

## 4. 참고 (A가 이미 처리한 것 중 B에게 영향 있는 것)

- config 기본값 `SAPIENS_SIZE` 5b→1b (`3ef054e`) — **`git pull` 필요.**
- 스모크 E2E의 5b 하드코딩 제거 (`684e915`) — B 로컬에서 스모크 실패 2건 보이면 pull 안 한 것.
- `smoke_segmentation_api.py` 삭제 — 실추론 스모크(`smoke_e2e_segmentation.py`)가 대체.
- `verify_ab_contract.py`의 4번 검사(실샘플 대조)가 리포 밖 경로를 봐서 **한 번도
  실행된 적이 없었다** — `out/e2e/segmentation.json`을 보도록 수정했다.
- 런북에 빠져 있던 검증 4종(part_merge·restatement·seg_scale·worker_resilience)을
  `selftest-runbook.md`에 추가했다 — 코드 수정 후 같이 돌려주면 된다.
