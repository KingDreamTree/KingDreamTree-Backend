# 제거한 코드 기록

지운 코드가 **무엇이었고 왜 지웠는지** 남긴다.
"이거 원래 있지 않았나?" 싶을 때 여기부터 보고, 실물이 필요하면 git에서 꺼낸다.

```bash
git show <커밋>^:app/routes/analyze.py     # 지우기 직전 내용
git log --diff-filter=D -- app/routes/analyze.py
```

---

## 2026-08-14 — v1 스캐폴드 잔재 정리

### 배경

v1 API는 `POST /analyze` → `/compare` → `/routine` 3단계였다.
v4 스키마에서 **`analysis` 테이블이 제거**되면서 이 구조는 폐기됐고,
세그멘테이션은 `photos` + `segmentation` 라우터로, 진단·루틴은 잡 큐로 옮겨갔다.

그런데 파일은 남아 있었다. `app/main.py` 에 이런 주석과 함께.

> ⚠️ 구 스캐폴드 라우터(/analyze, /compare, /routine)는 등록하지 않는다.
> `analysis` 테이블이 v4 스키마에서 제거되어 호출하면 런타임에 실패한다.
> 파일은 참고용으로 남겨두었고, 각 담당이 새 구조로 옮긴 뒤 삭제한다.

**세그멘테이션 쪽은 옮기기가 끝났다.** 그래서 지운다.

⚠️ 실제로는 담당 B 가 F08·F09 진단 파이프라인을 올리면서 `routes/analyze.py` 와
`schemas/analyze.py` · `compare.py` 를 먼저 지웠다. 여기 기록은 **그 파일들이
무엇이었고 왜 참고하면 안 되는지**를 남기기 위해 그대로 둔다.

### 지운 것

| 파일 | 줄 | 왜 |
|---|---|---|
| `app/routes/analyze.py` | 42 | 라우터 미등록 + **존재하지 않는 함수를 호출**한다 (`segmenter.preprocess_image`, `storage.upload_image` — 둘 다 정의 자체가 없다). 등록했어도 즉시 AttributeError |
| `app/schemas/analyze.py` | 53 | 위 라우터와 `compare.py` 만 쓰던 스키마 |
| `app/schemas/compare.py` | 58 | 아무 데서도 안 씀 |

⚠️ **이 스키마들의 모양은 지금 설계와 다르다.** 남겨두면 오히려 잘못된 참고가 된다.

```python
# 구 SegmentationResult — 폭 몇 개를 재는 방식이었다
keypoints = {"shoulder_width": 42.5, "hip_width": 38.0,
             "waist_width": 30.2, "height_px": 512}
```

지금은 **부위 단위**다. Sapiens가 몸을 29개 클래스로 나누고, 부위마다 픽셀 수·bbox·
유효성(`is_valid`)을 따로 갖는다. `analysis_id` 라는 식별자도 없다 —
`photo_id` / `session_id` 로 매달린다.

담당 B가 진단(F08·F09)을 만들 때는 **이 파일들을 참고하지 말고**
`app/schemas/segmentation.py` 와 `docs/api-spec-v2.md` 를 볼 것.

### 같이 지운 것

| 대상 | 왜 |
|---|---|
| `app/schemas/common.py` 의 `ErrorBody` · `ErrorResponse` | 어디서도 안 쓴다. 에러 형태는 `app/errors.py` 의 `ApiError.to_dict()` 가 **실제로 만들어 내보내는** 유일한 정의다. 같은 걸 두 군데 적어두면 한쪽만 고쳐져 어긋난다 |
| `app/schemas/job.py` 의 `JobEnqueued` | 안 쓴다. 잡을 만드는 라우트들은 각자의 응답 모델에 `job_id` 를 직접 넣는다 |
| `app/worker/queue.py` 의 `all_settled()` | 안 쓴다. "인바디처럼 있으면 쓰고 없으면 진행"할 때 쓰려고 미리 만든 것인데, 그 코드가 아직 없다. 필요해지면 그때 다시 만드는 편이 낫다 — 지금은 `list_jobs()` 에 결합만 만들고 있다 |

### 설정에서 뺀 것

| 설정 | 왜 |
|---|---|
| `SEG_WORKER_CONCURRENCY` | **배선돼 있지 않았다.** `app/worker/run.py` 는 단일 루프라 몇을 넣든 워커는 하나다 |
| `VLM_WORKER_CONCURRENCY` | 담당 B 가 먼저 지웠다 — 부위별 병렬 호출을 폐기하고 전 부위를 한 번에 진단하게 바뀌면서 "동시에 몇 부위를 부를지"가 의미를 잃었다 (llm-strategy.md §F08) |

⚠️ 이건 단순한 미사용보다 나쁘다. `VLM_WORKER_CONCURRENCY=3` 을 넣고
"워커 3개가 돈다"고 믿게 만든다. **동시 실행이 필요해지면 run.py 에 실제로
구현한 뒤에 설정을 되살릴 것.**

주석에 적혀 있던 근거는 남겨둘 값어치가 있어 옮겨 적는다 —
*"t3.large는 GPU가 없고 메모리 8GB다. 세그 워커를 2개 이상 돌리면 OOM."*

### 남겨둔 것 (안 쓰이지만 의도된 것)

지우지 않았다. 이유가 있다.

| 대상 | 왜 남기나 |
|---|---|
| `app/schemas/enums.py` 의 미사용 열거형 (`GapLevel`, `Confidence`, `ScoreSource`, `GenerationType`, `Gender`, `VlmInputType`) | 파일 자체가 **`db/schema.sql` 의 CHECK 제약을 그대로 옮긴 미러**다. DB에 있는 값이 여기 없으면 미러가 아니게 된다 |
| `app/services/routine.py`, `routine_templates.py`, `app/schemas/routine.py`, `app/prompts/routine_*.py`, `vlm.compare_body()` | **담당 B의 미구현 기능(F08~F12) 뼈대**다. 아직 호출되지 않을 뿐 폐기된 게 아니다 |

---

## 2026-08-14 — 설정 정리

### 지운 설정

| 설정 | 왜 |
|---|---|
| `SAPIENS_OFFLOAD` · `SAPIENS_GPU_MAX_GIB` | VRAM 보다 큰 모델을 CPU 로 흘려보내는 설정(accelerate `device_map`). **우리 두 환경 어디서도 타지 않는다** — RunPod 4090 은 24GB 라 5b(fp16 ~9.5GB)가 다 올라가고, 로컬은 CPU 라 `device == "cuda"` 조건에 걸리지 않는다. 한 번도 실행되지 않는 분기가 모델 로딩 경로에 있었다 |
| `SUPABASE_ANON_KEY` | 프론트가 Supabase 에 직접 붙지 않아 서버에서 쓸 일이 없다. `.env` 에 빈 칸으로 남아 있으면 "채워야 하나?" 싶어진다 |

되살릴 일이 생기면 `app/services/segmenter.py` 의 모델 로딩부에 이렇게 넣는다.

```python
budget = settings.sapiens_gpu_max_gib
if budget <= 0:
    budget = round(torch.cuda.get_device_properties(0).total_memory / 1024**3 * 0.9, 1)
kwargs["device_map"] = "auto"
kwargs["max_memory"] = {0: f"{budget}GiB", "cpu": "48GiB"}
```

⚠️ **그때는 `model.to(device)` 를 건너뛰어야 한다.** accelerate 가 이미 레이어를
배치한 뒤라 `.to()` 를 부르면 배치가 깨진다. 코드에도 주석으로 남겨뒀다.

### `.env` 에서 지운 죽은 줄

`SAPIENS_REQUIRE_VERIFIED_LABELS` 가 남아 있었다. **`SAPIENS_STRICT_LABELS` 로
이름이 바뀐 뒤의 잔재**다. `Settings` 가 `extra="ignore"` 라서 아무 말 없이
무시되고 있었다 — 넣어둔 사람은 켜둔 줄 알았을 것이다.

### 그래서 붙인 것 — 모르는 키 경고

`app/config.py` 의 `_warn_unknown_env_keys()`. 기동할 때 `.env` 를 읽어
`Settings` 가 모르는 키를 경고로 찍는다.

```
WARNING config: .env 에 모르는 키가 있습니다 (무시됨): FOO_BAR_TYPO
```

⚠️ **막지는 않는다.** 같은 `.env` 를 다른 도구가 쓸 수도 있어서 에러로 만들지
않았다. 보이게만 한다.

---

## 2026-08-14 (2차) — 전수 정리

### 코드

| 대상 | 왜 |
|---|---|
| `app/schemas/routine.py` 의 `RoutineRequest` · `Exercise` · `RoutineResponse` | v1 `POST /routine` 의 DTO. **`analysis_id`(= /analyze 응답)를 참조**하는데 그 테이블도 엔드포인트도 v4 에서 사라졌다. `analyze.py`·`compare.py` 를 지울 때 같이 빠졌어야 했다. F10 은 같은 파일의 `RoutineGenerate*` 를 쓴다 |
| `web/pose-score.js` 의 `angleOf` · `angleDiff` export | 내부 계산용 헬퍼인데 밖으로 열려 있었다. 쓰는 곳이 없다. 함수는 남기고 `export` 만 뗐다 |
| `ANTHROPIC_API_KEY` (설정) | `vlm.py` 가 진단용으로 재작성되며 openai 전용이 됐다. 키만 남겨두면 "`VLM_PROVIDER=claude` 로 바꾸면 되겠지"라는 오해를 만든다 |
| `docs/FRONTEND.md` (구본) | 새 가이드와 내용이 겹친 채 둘 다 살아 있었다. 반드시 어긋난다. 새 것을 같은 이름으로 승격 |

### 문서 — 낡은 스펙 버전

`docs/api-spec-v1.md` · `docs/db-design-v3.md` 를 지웠다.

⚠️ 단순히 "안 읽어서"가 아니다. **틀린 정의가 적혀 있었다.**

```
api-spec-v1.md §287   F = Jaccard(레퍼런스 인물 bbox, 사용자 인물 bbox)
db-design-v3.md §144  framing_score … 프레이밍 일치도 F (Jaccard)
```

`framing_score` 는 2026-08-14 에 **몸통 길이 비율**로 바뀌었다. bbox 방식은
팔다리 움직임을 프레이밍 문제로 오인해, 사용자가 물러서도 고칠 수 없는 안내를
띄웠기 때문이다. 옛 문서를 그대로 두면 **읽은 사람이 틀린 걸 구현한다.**

v2·v4 의 "이전 버전" 줄은 파일 링크 대신 삭제 사실을 적도록 고쳤고,
`work-split.md` 의 기준 문서도 v4·v2 로 갱신했다.

### ⚠️ 담당 B 영역이라 손대지 않은 것

`app/services/segmap.py` 의 `part_stats()` 가 어디서도 호출되지 않는다.
확인 후 정리 부탁드립니다.

---

## 2026-08-14 (3차) — 문서에 남은 틀린 서술

코드가 아니라 **문서가 지금과 다른 걸 말하고 있던 것**들. 지우지 않고 고쳤다.

| 문서 | 무엇이 틀렸나 |
|---|---|
| `api-spec-v2.md` §339 | `F = Jaccard(bbox)` — 현재는 **몸통 길이 비율**이다. 그리고 거부선이 `F_MIN` 이 아니라 `F_HARD` 다. 판정 순서에 `FACING` 도 빠져 있었다 |
| `handoff-to-a.md` §107 | `Eyeglasses` (복수형) — 공식 문서 기준은 `Eyeglass` |
| `work-b.md` §101, §254 | 같은 철자 오류 |
| `app/services/segmap.py` §16 | 같은 철자 오류 (주석) |

⚠️ **철자 하나가 왜 문제인가** — 워커가 기동할 때 `label_map` 을 `body_part`
마스터와 대조한다. 복수형으로 seed 하면 "마스터에 없는 클래스"로 걸려 잡이
통째로 실패한다. 실제로 한 번 겪고 고친 것이라, 문서에 옛 철자가 남아 있으면
다음 사람이 그대로 다시 넣는다.

⚠️ **`api-spec-v2.md` 의 산식은 특히 위험했다.** 프론트가 명세서를 보고 구현하는데
bbox 로 재면 팔다리 움직임이 프레이밍 문제로 보고되고, 사용자는 물러서도
고칠 수 없는 안내를 받는다. 오늘 그걸 고쳤는데 명세서에는 옛 정의가 남아 있었다.
