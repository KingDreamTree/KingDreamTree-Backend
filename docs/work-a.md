# 담당 A 작업서 — 이미지 파이프라인

> **이 문서 하나만 읽으면 됩니다.** 담당 B의 작업서(`docs/work-b.md`)는 안 봐도 됩니다.
> 참고 문서: `docs/db-design-v4.md` (스키마), `docs/api-spec-v2.md` (F02~F06, F13~F15)

| | |
|---|---|
| **최종 수정일** | 2026-08-13 |
| **한 줄 정의** | **이미지가 들어와서 부위별 라벨 맵과 통계가 나올 때까지** |

---

## 1. 내가 만드는 것

| 기능 | 내용 |
|---|---|
| **F02** 사용자 식별자 | `POST /users`, `GET /users/me` |
| **F03** 세션 관리 | 세션 생성/조회, 단계별 진행 상황 집계 |
| **F04** 레퍼런스 사진 | 업로드 + MediaPipe 포즈 **동기** 추출 + 세그 큐잉 |
| **F05** 사용자 사진 | 촬영/업로드 저장 + 포즈 **서버 재검증** |
| **F06** 세그멘테이션 + 시각화 | Sapiens2 라벨 맵 생성·저장, 팔레트 API, 부위별 통계 |
| **F13** 잡 폴링 | `GET /jobs/{id}` |
| **F14** signed URL | 배치 발급 |
| **F15** 데이터 삭제 | Storage prefix + DB |

## 2. 내가 안 만드는 것

인바디 OCR · VLM 진단 · 루틴 생성 · 운동 기록. 전부 담당 B입니다. 해당 라우터/서비스 파일은 건드리지 마세요.

## 3. 내 파일

```
app/services/segmenter.py       Sapiens2 로드, 라벨 맵 생성, 부위 통계, 크롭
app/services/pose.py            MediaPipe, 스케일 정규화, P/F 점수
app/routes/users.py
app/routes/sessions.py
app/routes/photos.py            F04, F05
app/routes/segmentation.py      F06
app/routes/body_parts.py        마스터 조회
app/routes/storage.py           signed URL
app/routes/jobs.py
app/worker/handlers/seg.py      SEG_REFERENCE, SEG_USER
scripts/seed_body_parts.py      마스터 seed
```

## 4. 담당 B와의 경계선 — 계약은 DB 하나뿐

```
내가 씀  →  segmentation (맵 파일 + label_map)
            body_part_segment (부위별 통계 + crop_path)
            body_part (마스터: class_name, color_hex, is_comparable)
                          ↓
B가 읽음  →  DB SELECT + signed URL 만
```

- ⚠️ **B는 `segmenter.py`를 import하지 않습니다.** 그래야 B가 Sapiens2 1.5GB 없이 개발할 수 있고, 내가 함수 시그니처를 바꿔도 B가 안 깨집니다.
- ⚠️ **VLM 입력 이미지 생성은 내 책임입니다.** B는 `body_part_segment.crop_path`(또는 하이라이트 이미지 경로)를 URL로만 읽습니다. 입력 형식이 `CROP`↔`HIGHLIGHT`로 바뀌면 **내가 설정을 바꾸고 백필 스크립트를 돌립니다.** B의 코드는 그대로입니다.
- B가 나를 기다리지 않도록, **Phase 1 초반에 실제 맵 PNG 샘플 1장 + `label_map` JSON을 공유**하세요. B가 그걸로 더미 데이터를 만듭니다.

---

## 5. 작업 순서

### Phase 0 — 담당 B와 같이 (페어, 반나절) ⚠️ 이걸 건너뛰면 뒤가 다 꼬입니다

| # | 작업 |
|---|---|
| 0-1 | `db/schema.sql` 확정 → Supabase 콘솔 반영 (16개 테이블 + 인덱스 + RLS 켜기, 정책 없음) |
| 0-2 | `app/schemas/` DTO 골격 — API 명세의 요청/응답 전부 |
| 0-3 | `app/services/db.py`, `storage.py`, `app/deps.py`, `app/config.py` |
| 0-4 | `app/worker/queue.py` + 잡 등록/폴링 — `GET /jobs/{id}`가 도는 상태까지 |
| 0-5 | `requirements.txt`, `.env.example` — torch **CPU 빌드**, 전부 `==` 고정 |
| 0-6 | **Supabase Storage 버킷 4개 생성** — `photos`, `segmentations`, `body-parts`, `inbody-temp` (전부 private) + **`segmentations`에 CORS 설정** |

전부 공유 파일입니다. **한 브랜치에서 같이 작업하고 한 PR로 머지하세요.**

### Phase 1 — 세그멘테이션 코어 ⚠️ 최우선

| # | 작업 | 완료 기준 |
|---|---|---|
| 1-1 | ~~Sapiens2 클래스명 확인~~ ✅ | **29개 확정** (Sapiens 28 + Eyeglasses). seed 적용 완료 |
| 1-2 | ~~`seed_body_parts.py` 작성·실행~~ ✅ | 멱등 스크립트. `--check`로 DB 대조 |
| 1-3 | `segmenter.py` — 라벨 맵 PNG 생성 | §6 포맷 규칙 준수 |
| 1-4 | `segmenter.py` — 부위별 통계 | 검출된 **모든** 클래스에 행 생성, `is_valid` 판정 |
| 1-5 | **맵 샘플 + label_map JSON을 B에게 공유** | B의 더미 데이터 작업 해제 |

> ✅ **1-1은 해결됐습니다.** B의 프롬프트 부위 목록도 이제 확정입니다 (비교 대상 9개).
> 남은 확인은 **첫 추론 시 `label_map`을 마스터와 대조**하는 것 하나뿐입니다 —
> `Eyeglasses` 철자와 픽셀 인덱스는 실물로만 확인 가능합니다.

### Phase 2 — 사진 파이프라인

| # | 작업 |
|---|---|
| 2-1 | `pose.py` — MediaPipe 추출, `TORSO`/`HIP_KNEE` 스케일 정규화 |
| 2-2 | `POST /photos/reference` — 포즈 동기 추출 + `SEG_REFERENCE` 큐잉 |
| 2-3 | `POST /photos/user` — 포즈 재검증 → 422 또는 저장 + `SEG_USER` 큐잉 |
| 2-4 | `seg.py` 워커 — 맵 저장 + `segmentation` + `body_part_segment` 행 생성 |
| 2-5 | 사진 교체(upsert) — Storage 삭제 순서 준수 |

### Phase 3 — 조회·부가 API

| # | 작업 |
|---|---|
| 3-1 | `GET /photos/{id}/segmentation` — 팔레트 조립 |
| 3-2 | `GET /sessions/{id}/segmentation` — 레퍼런스·사용자 + `comparable` 교집합 |
| 3-3 | `GET /sessions/active` — 단계별 집계 |
| 3-4 | `POST /storage/signed-urls` |
| 3-5 | `DELETE /users/me` — Storage prefix 4개 + DB |
| 3-6 | `GET /body-parts`, `GET /jobs/{id}` |

### Phase 4 — 배포·튜닝

EC2 배포 스크립트, tmux 세팅, Sapiens2 첫 실행 다운로드 확인, 워커 동시성/메모리 튜닝, 임계값 튜닝.

### Phase 5 — 담당 B와 같이

전체 플로우 통합 테스트 (`POST /users` → 피드백 반영까지), 프론트 연동.

---

## 6. ⚠️ 라벨 맵 — 조용히 망가지는 지점들

이번 버전에서 가장 새롭고 가장 사고가 나기 쉬운 부분입니다.

### 6.1 파일 포맷 (어기면 에러 없이 값이 바뀝니다)

| 규칙 | 어기면 |
|---|---|
| **PNG만.** JPEG·손실 WebP 금지 | 손실 압축이 인접 라벨을 섞어 **없는 클래스가 생깁니다** |
| **8-bit 그레이스케일, 알파 없음** | 알파가 있으면 브라우저가 프리멀티플라이하며 값을 바꿉니다 |
| **ICC 프로파일 넣지 않기** | 브라우저 색 관리가 픽셀 값을 보정합니다 |
| **리사이즈는 nearest-neighbor만** | bilinear/bicubic 보간이 라벨을 섞습니다. **가장 흔한 사고** |
| 팔레트(P 모드) PNG 쓰지 않기 | 브라우저가 RGB로 펼쳐서 값 복원이 한 단계 늘어납니다 |

```python
# Pillow 예시 — 이대로 저장하면 안전합니다
Image.fromarray(label_array.astype("uint8"), mode="L").save(path, format="PNG", optimize=True)

# 리사이즈가 필요하면
img.resize((w, h), Image.NEAREST)   # ⚠️ NEAREST 외에는 절대 쓰지 마세요
```

### 6.2 `label_map`을 행마다 저장

모델 버전이 바뀌면 클래스 ID가 재배열됩니다. 코드 상수로만 두면 모델을 올린 순간 **과거의 모든 맵이 "왼팔을 오른다리로 읽는" 상태**가 되고 에러는 하나도 안 납니다.

- `segmentation.label_map` = `{"0":"Background","1":"Torso",...}` — 추론 당시 매핑을 박제
- `model_name` + `model_version`도 저장 — 나중에 재추론 대상을 골라내려면 필요
- ⚠️ 워커 기동 시 `label_map`을 `body_part` 마스터와 대조해서, **모르는 클래스명이 나오면 경고 로그.** 조용히 넘어가면 seed 불일치를 못 잡습니다.
- ⚠️ **`label_value`로 테이블을 조인하지 마세요.** 조인 키는 항상 `class_name`입니다.

### 6.3 좌표계

- `body_part_segment.bbox_*`는 **맵 좌표계**입니다 (v3에서는 원본 좌표였음 — 바뀌었습니다).
- 맵 해상도가 원본과 다를 수 있으므로 `map_width`/`map_height`를 반드시 저장하세요.
- 프론트가 원본 위에 그릴 때 `photo_width / map_width` 배율로 스케일합니다.

### 6.4 CORS

프론트가 `getImageData()`로 픽셀 값을 읽어야 하는데, signed URL은 다른 오리진이라 **`segmentations` 버킷에 CORS가 없으면 캔버스가 오염되어 읽을 수 없습니다.** 프론트가 제일 먼저 막히는 지점이니 Phase 0에서 설정하고 미리 알려주세요.

### 6.5 색 팔레트

- `body_part.color_hex`를 서버가 내려줍니다. **프론트가 색을 하드코딩하게 두지 마세요** — 부위가 추가되면 색과 라벨이 어긋납니다.
- 좌/우를 비슷한 색 계열(주황↔초록, 보라↔빨강)로 잡은 이유는 **좌우 반전 사고를 눈으로 잡기 위해서**입니다. 색이 좌우 대칭으로 뒤집혀 보이면 반전 규칙이 깨진 것입니다.

---

## 7. ⚠️ 그 외 주의사항

### 🔴 치명적

- **Sapiens2 가중치를 절대 커밋하지 마세요.** `*.pt` `*.pth` `*.safetensors` `*.onnx` `models/`. 첫 실행 시 자동 다운로드(~1.5GB)입니다. 커밋 전 `git diff --cached` 확인.
- **좌우 반전.** Storage에 저장되는 사진·`pose_landmarks`·맵은 전부 **반전되지 않은 카메라 원본** 기준. 미러링은 프론트 CSS만. 어기면 왼팔↔오른팔이 뒤바뀐 채 **에러 없이 조용히** 진행되고 VLM 진단이 전부 좌우 반대로 나옵니다. **구현 직후 맵을 색칠해서 눈으로 확인하세요.**
- **레퍼런스와 사용자는 같은 `pose_scale_basis`.** 사용자 사진을 잴 때 레퍼런스 값을 강제하고, 그 기준을 못 재면 422(`reason="FRAMING"`)로 떨어뜨리세요. 각자 다른 기준으로 재면 점수가 무의미합니다.
- **Storage 삭제 순서.** 사진 교체: `body-parts` 크롭 → `segmentations` 맵 → `photos` 원본 → `photo` 행 삭제(나머지 CASCADE). 유저 삭제: Storage prefix 4개 → DB. **DB를 먼저 지우면 어느 파일을 지울지 알 수 없게 됩니다.**

### ⚠️ 중요

- **t3.large는 GPU가 없습니다.** Sapiens2 CPU 추론 수십 초 + 메모리 8GB. **세그 워커 동시성은 1.** 2개 돌리면 OOM으로 인스턴스가 죽습니다.
- **MediaPipe는 동기, Sapiens2는 비동기.** 레퍼런스 업로드 응답에 landmarks가 즉시 들어가야 사용자가 촬영 화면으로 바로 넘어갑니다. **둘을 같은 잡에 묶지 마세요.**
- **`torch`/`mediapipe` 버전을 `==`로 고정**하고 torch는 **CPU 빌드**. 어긋나면 fp16 로드/추론이 깨집니다. 추가 즉시 `requirements.txt` 반영 + B에게 공지(무거운 의존성).
- **임계값은 전부 `config.py` + `.env`로.** `THRESHOLD`(0.90) `F_MIN`(0.80) `TOL`(40°) `MIN_PIXELS`(1,500) `MIN_RATIO`(0.5%) `MAP_MAX_SIDE`(1024) — 전부 잠정값입니다. 코드에 박지 마세요. 새 환경변수는 **같은 PR에서 `.env.example` 갱신.**
- **`pixel_count` / `area_ratio` 원값을 항상 저장.** `is_valid`는 캐시일 뿐이고 원값이 진실입니다. 임계값을 올렸을 때 기존 데이터를 재판정할 수 있어야 합니다.
- **검출된 모든 클래스에 행을 만드세요.** 유효 부위만 만들면 "왼팔은 왜 빠졌지?"에 답할 수 없습니다. `is_valid=false` + `invalid_reason`이 남아야 "옷에 가려져 노출이 부족합니다" 안내가 나갑니다.
- **`SEG_*` 잡의 `result`에 요약을 넣으세요.** `{"segmentation_id":"...","detected":12,"valid_comparable":7,"invalid":[{"class_name":"...","reason":"TOO_SMALL"}]}` — 프론트가 세그 완료 즉시 재촬영 안내를 낼 수 있습니다.
- **잡 선점은 원자적으로.** `UPDATE ... WHERE status='PENDING' RETURNING`. `SELECT` 후 `UPDATE`하면 워커 2개가 같은 잡을 집습니다.
- **소유권 불일치는 403이 아니라 404.** 403은 리소스 존재를 알려줍니다.
- **signed URL은 prefix 검증만으로 부족.** `{user_id}/`로 시작하는지 + **DB에 그 행이 실제로 있는지** 확인하세요. prefix만 보면 임의 경로 탐색이 가능합니다.
- **`job.error`에 스택 트레이스·모델 경로·API 키를 넣지 마세요.** 프론트에 그대로 노출됩니다.
- **`client_pose_similarity`와 서버 계산값을 함께 로그에.** 프론트가 90%로 촬영했는데 서버가 422를 주는 일이 생깁니다. 차이가 크면 임계값이 아니라 구현이 어긋난 것입니다.

---

## 8. 내 완료 체크리스트

- [ ] Sapiens2 실제 클래스 목록 확인 → B에게 공유
- [ ] `body_part` seed (전체 클래스 + `is_comparable` + `color_hex`)
- [ ] 맵 PNG가 8-bit 그레이스케일 / 알파 없음 / ICC 없음인지 **파일로 확인**
- [ ] 맵을 색칠해서 좌우가 안 뒤집혔는지 **눈으로 확인**
- [ ] `label_map`이 행마다 저장되고 마스터와 대조되는지
- [ ] `segmentations` 버킷 CORS 설정 + 프론트에 공지
- [ ] 사진 재업로드 시 고아 파일이 안 남는지
- [ ] `DELETE /users/me` 후 Storage 4개 prefix가 비었는지
- [ ] 세그 워커 동시성 1 확인, 메모리 사용량 실측
- [ ] 맵 샘플 + `label_map` JSON을 B에게 전달
- [ ] 임계값이 전부 `.env`에 있고 `.env.example`에 반영됐는지

---

## 9. 협업 규칙 (둘 다 지킴)

| 항목 | 규칙 |
|---|---|
| **브랜치** | `main`에서 분기, `feature/`·`fix/`·`refactor/`·`chore/`. 하나의 브랜치 = 하나의 작업 |
| **커밋** | `<타입>: <한글 설명>` (예: `feat: 레퍼런스 세그멘테이션 워커 추가`) |
| **PR** | `main` 직접 push 금지. 최소 1명 승인. 머지 후 브랜치 삭제 |
| **공유 파일** | `app/main.py` `app/config.py` `app/schemas/` `app/services/db.py` `app/services/storage.py` `app/deps.py` `app/worker/queue.py` `db/schema.sql` `requirements.txt` `.env.example` → **변경 시 B 리뷰 필수, 셀프 머지 금지** |
| **남의 파일** | 함부로 수정·재포맷 금지. 꼭 필요하면 최소한만 + PR 설명에 이유 |
| **포맷** | `black` + `isort`, line-length 100. 저장 시 자동 포맷 켜기 |
| **의존성** | 새 패키지는 `==` 고정으로 즉시 `requirements.txt`. 무거운 건 공지 |
| **환경변수** | 새로 추가하면 **같은 PR에서 `.env.example` 갱신** |
| **DB 스키마** | 콘솔 변경 시 **같은 날 `db/schema.sql` 갱신 + 공지.** 파괴적 변경은 합의 후에만 |
| **테스트 데이터** | 무료 티어 공유 DB → `test_` 접두사 |
| **EC2** | 코드 직접 수정 금지, `git pull`만. 8000 포트 동시 실행 불가, tmux 세션 `api`, 재시작 전 공지 |
| **커밋 전** | `git diff --cached`로 `.env` / `*.pem` / 모델 가중치 / `.venv` / `__pycache__` / `.idea` 확인 |

**실시간 공지 대상** — `main` 배포 · EC2 재시작 · DB 스키마 변경 · 공유 파일 변경 · 무거운 의존성 추가 · 키 사고

---

## 10. 내가 결정하거나 확인해야 할 미확정 항목

| # | 항목 | 상태 |
|---|---|---|
| 1 | **Sapiens2 실제 클래스명·개수** | **내가 확인. 최우선** — B의 프롬프트가 막혀 있음 |
| 2 | 맵 저장 해상도 | 내가 결정 (권장: 긴 변 1024px) |
| 3 | 3방향 촬영 필요 여부 | 둘이 합의 — `photo.kind` 값 집합이 바뀜 |
| 4 | 레퍼런스 프리셋 도입 | 둘이 합의 — 도입 시 세그 결과 재사용으로 부하 절반 |
| 5 | 루틴 진행 기준 (수행 횟수/날짜) | 둘이 합의 |
| 6 | 시연 후 데이터 삭제 정책 | 둘이 합의 — 사람 사진이라 정해두는 게 좋음 |
| 7 | `users`에 넣을 컬럼 | 화면 요구 나온 뒤 |
