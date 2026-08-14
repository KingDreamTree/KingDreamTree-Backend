# 실연동 후속 — A 지적 4건 처리 (2026-08-14 밤)

> 앞선 PR #31(분모 붕괴·크기 수치 폐기·억지 칭찬)의 **범위 밖**으로 A 가 되돌려준 4건.
> 검증: `verify_analysis` · `verify_ab_contract` · `verify_coach_chat` · `verify_routine_rules`

---

## ② 오버레이는 병합 전, 숫자는 병합 후 — **수정** 🔴

**A 지적**: `segmap.apply_clothing_merge` 의 호출처가 **0곳**. 정의와 docstring 뿐.
`_load_side` 가 원본 맵으로 오버레이를 만들어, VLM 이 "소매 끝 조각만 칠해진
상완 그림"과 "병합 후 수치"를 동시에 받았다.

**확인**: 사실이었다. `grep` 결과 호출처 0. `seg.py` 주석의 설계
("읽는 쪽은 같은 함수를 태운다")가 이행되지 않은 상태였다.

**수정**: `segmap.merge_map()` 추가 — PIL(L) ↔ numpy 변환만 담당하고 병합은
`part_merge` 에 위임. `_load_side` 가 오버레이 생성 전에 이걸 태운다.

```
before:  load_map(map_bytes)                    → build_overlay   (원본 맵)
after :  merge_map(load_map(...), label_map, …) → build_overlay   (병합 맵)
```

이제 **그림과 숫자가 같은 몸을 말한다.** 병합 로직 구현은 여전히 `part_merge`
하나뿐이라는 원칙도 유지된다.

> `numpy` 를 venv 에 설치했다 (`requirements.txt` 에 이미 있었으나 미설치 상태).

---

## ③ blocked + 인바디 없음인데 gap_level 이 점수에 들어감 — **수정** 🔴

**A 지적**: 프롬프트는 null 강제인데 `_coerce_part` 가 이 조합을 안 거른다.
56점 중 SIGNIFICANT 둘이 이 경로였고, 빼면 88점.

**확인**: 사실. `_coerce_part` 는 `blocked` 유무와 무관하게 유효한 enum 이면 통과시켰다.

**수정**: `inbody_available` 을 파싱 체인에 흘려서 —

| blocked | 인바디 | gap_level |
|---|---|---|
| 있음 | 없음 | **null 로 강등** ← 시각도 실측도 없으면 근거가 0 |
| 있음 | 있음 | 유지 (인바디를 근거로 등급을 매기는 건 프롬프트가 허용한 경로) |
| 없음 | 무관 | 유지 |

강등 시 `log.warning` 을 남겨 프롬프트 위반 빈도를 추적할 수 있게 했다.
회귀 테스트: `verify_analysis.py::test_blocked_without_inbody`.

---

## ④ F09 가 프롬프트 예시를 글자 그대로 복사 — **수정**

**A 지적**: "하체 균형이 좋습니다"·"좌우 팔 근육량 차이…"가 `overall_diagnosis.py`
출력 예시와 **동일 문자열**. excluded 부위 목록이 F09 에 전달되지도 않음.

**수정 2건**:

1. 출력 예시를 복사 불가능한 **자리 표시**로 교체
   ```
   before: "strengths": ["하체 균형이 좋습니다"]
   after : "strengths": ["<NONE/SLIGHT 근거가 있는 부위의 좋은 점 — 없으면 빈 배열>"]
   ```
   + "자리 표시나 명세 문장을 출력에 복사하지 말 것" 명시

2. **`excluded` 전달** — 검출조차 안 된 비교 대상 부위 목록을 F09 에 넘긴다.
   `_excluded_parts()` 가 `body_part` 마스터 − 진단 행으로 계산.
   프롬프트에 "이 부위는 **아무 정보가 없다**. strengths·summary 근거로 쓰지 말 것.
   cautions 에 '확인할 수 없었다'로만 언급 가능" 을 넣었다.

> 이 둘이 겹쳐서 "하체 균형이 좋습니다"가 나왔다 — 예시 문자열이 있었고,
> 하체가 분석 안 됐다는 사실은 전달되지 않았다.

---

## ① 분자도 오염 — **A 의견 수용, 재촬영 유도로 결론**

**A 지적**: 후보 (a) `aspect` 폴백은 이 케이스에서 못 믿는다. 상완이 셔츠 반쪽씩을
흡수했으니 그 부위의 bbox·aspect·width_share 가 전부 "셔츠 모양"이다. 검출 집합이
무너졌다는 건 이웃 부위가 옷을 먹었다는 뜻이라 **분모 없는 지표도 같이 죽는다.**

**동의한다.** 그리고 PR #31 에서 이미 그 방향으로 갔다 — 사진 간 크기 비교 수치를
**통째로 폐기**했으므로 `aspect` 폴백 논의 자체가 사라졌다. 프롬프트에 남은 수치는
각도가 소거되는 것뿐이다(같은 사진 안 좌우 대칭 · 인바디 실측 · 옷 흡수 비율 · 잘림).

**"재촬영 유도가 맞을 수도"** 에 대해 — 이미 두 겹이 있다:

| 겹 | 조건 | 결과 |
|---|---|---|
| A | `retake_recommended` (SEG job.result) | 프론트가 재촬영 유도 |
| B | `min_comparable_parts = 3` 미달 | `INSUFFICIENT_PARTS`, 재시도 안 함 |

오늘 세션은 교집합 4개라 둘 다 통과했다. **임계값을 올릴지는 데이터를 더 보고
정하자** — 근거 없이 3→5 로 올리면 `clothing_ratio > 0.5` 때와 같은 실수가 된다.
대신 지금은 `framing_bias` 가 기록되고 있으니, 실사진이 쌓이면 "몇 부위 미만에서
진단 품질이 무너지는가"를 수치로 정할 수 있다.

---

## 남은 것

- [ ] 좋은 사진 2장(정면 기립·팔 15~30도 벌림·손목 노출)으로 진단 풀런
      → 이번 수정 4건이 실제 진단문에 어떻게 반영되는지 확인
- [ ] `framing_bias` 실사진 로그 축적 → `min_comparable_parts` 재검토 근거
