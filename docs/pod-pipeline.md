# GPU 팟 직접 업로드 경로 (PHOTO_PIPELINE=pod)

| | |
|---|---|
| **작성** | 2026-09-09 |
| **목적** | 사진을 서버(API·Storage)에 두지 않고, GPU 팟이 메모리에서 처리해 결과만 남긴다 |
| **담당** | A |
| **상태** | 코드 완료 · 로컬 검증 완료 · **RunPod·EC2 배포는 미실행** (서버 꺼져 있음) |

## 무엇이 바뀌나

```
[기기] 원본은 IndexedDB (기기에서 블러하지 않는다 — 아래 "얼굴 가림")
   ├─ 기준/사용자 사진 등록 ──랜드마크·메타만──▶ [API]  POST /photos/reference · /photos/user
   ├─ 분석 시작 ──▶ [API] POST /sessions/{id}/upload-token  (팟 주소는 안 줌 — 프론트 고정)
   └─ 두 장 + X-Upload-Token ──▶ [팟] POST /upload
                                    ① 토큰·크기·속도 제한 (본문 읽기 전)
                                    ② 거울 되돌림·3:4 크롭·리사이즈 (종전 _store 와 같은 함수)
                                       + 랜드마크로 얼굴을 회색으로 덮은 **복사본 한 벌** (face_mask)
                                    ③ 사용자 사진 스크리닝(GPT, 가린 복사본) → 이 응답에서 즉시 통과/반려
                                    ④ 202 → 뒤에서 세그×2(안 가린 가공본) → 부위·종합 진단(가린 복사본)
                                    ⑤ 맵·진단문·수치·crop_box 만 Supabase, 사진 두 벌은 메모리에서 해제
[기기] ──폴링──▶ [API] GET /jobs/{id} · /analysis/progress · /analysis   (그대로)
[화면] = 기기 원본을 (flipped 면 좌우 반전 후) crop_box 로 잘라 + 서버 맵
```

사진이 존재하는 곳: 기기 · 팟 메모리(처리 중, 가공본 + 가린 복사본) · OpenAI(**가린 복사본만**).
Storage·DB·API·EC2 워커에는 없다.

## 코드 지도

| 위치 | 역할 |
|---|---|
| `app/pod/server.py` | `POST /upload`, `GET /health`. 관문 → 가공 → 스크리닝 → 대기열 |
| `app/pod/pipeline.py` | 처리 스레드(1개). 잡을 PROCESSING 으로 만들어 세그·진단 핸들러를 그대로 돌린다 |
| `app/pod/main.py` | 진입점. 점검 → 모델 예열 → 재시작 잔여 잡 FAILED 정리 → 서버 |
| `app/services/photo_source.py` | 핸들러가 사진을 읽는 유일한 창구 — 메모리 우선, 없으면 Storage(종전) |
| `app/services/upload_token.py` | HMAC 일회용 토큰. API 발급 / 팟 검증 |
| `app/services/face_mask.py` | 랜드마크(코·눈·귀·입)로 얼굴 박스를 잡아 회색으로 덮는다. OpenAI 행 복사본에만 |
| `app/routes/photos.py` | pod 모드: 파일 없이 랜드마크만 저장, `upload-token` 발급 |
| `db/migrations/2026-09-09_photo_pod_pipeline.sql` | `photo.storage_path` NULL 허용 + `photo.crop_box` |
| `Dockerfile.pod` | SSH·터미널 없는 이미지. CMD 가 곧 서버 |

바뀌지 않은 것: 세그·진단 핸들러 본문, 프롬프트, 잡 테이블·폴링, 결과 저장 형태, 점수·우선순위.

## 환경 변수 (팟)

```
PHOTO_PIPELINE=pod
POD_UPLOAD_SECRET=<API 와 같은 값>
SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY
OPENAI_API_KEY
MODEL_DIR=/workspace/models        HF_HOME=/workspace/.cache/huggingface
SAPIENS_SIZE=1b  SAPIENS_DEVICE=cuda  SAPIENS_DTYPE=float16
CORS_ORIGINS=https://www.refit.live  (브라우저가 팟에 직접 붙는다 — 비우면 화면에서만 실패)
POD_PORT=8080  POD_QUEUE_MAX=8  POD_UPLOAD_RATE_LIMIT=10
POD_FACE_MASK=true   (false 는 실측 전용 — 운영에서 끄면 OpenAI 에 얼굴이 나간다)
```

API 쪽: `PHOTO_PIPELINE=pod`, 같은 `POD_UPLOAD_SECRET`, 그리고 compose 의 `WORKER_KINDS=OCR_INBODY,ROUTINE_GEN,ROUTINE_PATCH` (EC2 워커가 VLM 잡을 집지 않게).

## RunPod 설정 (미실행 — 팟을 올릴 때)

- 이미지: `kingdreamtree-pod:<tag>` (레지스트리에 푸시한 것)
- **Community Cloud 그대로** (2026-09-09 결정). Secure Cloud 는 GPU 주인이 메모리를 볼 수 있다는 구조적 위험 때문에 고려 대상이었지만, 지금 단계에서는 하지 않는다. 이슈 표에 후순위로 남긴다
- Network Volume → `/workspace` (가중치). 리전은 볼륨 리전(EU-RO-1)
- **Expose HTTP Ports: 8080**. SSH/TCP 포트는 열지 않는다. 템플릿의 start command 는 비운다 (이미지 CMD 사용)
- 환경 변수는 위 목록을 팟 템플릿에 넣는다 (시크릿 포함)
- 프록시 주소 `https://<pod-id>-8080.proxy.runpod.net` 을 **프론트 빌드 환경변수**에 고정한다. API 응답에 넣지 않는다

## 배포 순서 (셋이 맞물린다)

1. Supabase SQL 편집기에서 `2026-09-09_photo_pod_pipeline.sql` 적용 (종전 경로에 영향 없음)
2. 팟 이미지 빌드·푸시 → 팟 생성 → `GET /health` 가 `status: ok`, `pipeline.worker_alive: true`
3. API `.env`: `PHOTO_PIPELINE=pod`, `POD_UPLOAD_SECRET`, `WORKER_KINDS=OCR_INBODY,ROUTINE_GEN,ROUTINE_PATCH` → `docker compose up -d --build`
4. 프론트 배포 (팟 주소 고정, 새 흐름)
5. `scripts/smoke_pod_pipeline.py --api https://api.refit.live/api/v1 --pod https://<pod>-8080.proxy.runpod.net` 로 관통 확인

되돌리기: API `PHOTO_PIPELINE=storage` + `WORKER_KINDS` 비움 + 프론트 이전 빌드. 마이그레이션은 두어도 된다.

## 운영

- **로그**: RunPod 대시보드 컨테이너 로그(stdout). 기동 점검 실패도 여기 첫 줄에 나온다. 사진 바이트·토큰은 찍지 않는다
- **헬스**: `GET /health` → `pipeline.queued`(대기), `held_photos`(메모리 사진 수 — 대기 상태면 0), `worker_alive`
- **재시작**: 메모리 사진이 사라지므로 처리 중이던 잡은 기동 직후 `FAILED` + "다시 올려주세요" 로 정리된다 (`queue.fail_orphans`, payload.source=pod 만)
- **마이그레이션 전**: 폴백이 없다. `storage_path` NOT NULL 은 API 의 랜드마크 등록을, `crop_box` 없음은 팟의 행 갱신(업로드 500)을 막는다 — **pod 모드는 마이그레이션 적용이 전제**다. (crop_box 없이 조용히 저장하는 폴백은 2026-09-09 결정으로 뺐다: 업로드는 성공처럼 보이는데 화면만 어긋나는 게 더 찾기 어렵다)
- **대기열 초과**: 업로드가 503 `POD_BUSY` — 프론트는 "잠시 후 다시"
- **디버깅**: 문 없는 팟에는 들어갈 수 없다. 같은 이미지에 SSH 템플릿을 얹은 **스테이징 팟**을 따로 만들어 거기서 본다. 프로덕션 팟은 재배포만
- **잔여 확인**: `scripts/check_pod_no_photo_residue.py` (스테이징 팟·로컬), `scripts/audit_photo_privacy.py --session <id>` (DB·Storage 감사)

## 얼굴 가림 (이슈 3) — 팟이 OpenAI 행 복사본만 가린다 (2026-09-09 결정)

**왜 기기 블러가 아닌가.** 원본 vs 얼굴 블러본의 세그 결과를 실측했다 (사진 4장, RTX 5060 · 1b fp16, `scripts/measure_face_blur.py`).

| 블러 방식 | 인물 픽셀(분모) 변화 | 부위 면적비 최대 차 | 부위 IoU 최소 | 판정 |
|---|---|---|---|---|
| 머리+목 박스(Face_Neck·Hair +15%) · 강한 블러(짧은 변/6) | −12 ~ −15% | 15 ~ 18% (몸통 픽셀 −15%) | 0.835 | **다름** |
| 얼굴만 · 강한 블러(짧은 변/6) | −7 ~ −9% (1장은 ±0) | 7 ~ 10% | 0.979 | **다름** |
| 얼굴만 · 약한 블러(짧은 변/12) | ±1% | ≤ 2.5% | ≥ 0.98 | 같음 (4/4) |

- 사피엔스는 **강하게 뭉갠 얼굴을 배경으로** 분류한다 → 인물 픽셀(모든 부위 비율의 분모)이 줄고 부위 비율이 틀어진다
- 세그가 안 틀어지는 건 약한 블러뿐인데, 약한 블러는 얼굴 윤곽이 남아 익명화가 안 된다. 즉 "한 장을 기기에서 블러해 세그·GPT 에 같이 쓴다"는 목표(OpenAI 에 얼굴이 안 나감)를 못 이룬다
- 스크리닝(GPT) 판정은 강한 블러·머리 박스에서도 2/2 쌍 동일 — GPT 쪽은 세게 가려도 된다

**그래서.** 세그는 **안 가린 가공본**, OpenAI 로 가는 세 호출(스크리닝·부위·종합)은 **얼굴을 가린 복사본**을 쓴다. 복사본은 가공 직후 한 번 만들어(`pipeline.prepare` → `face_mask.apply`) 처리 내내 메모리에 두고(`photo_source` 의 vlm 변형, `load(photo, for_vlm=True)`), 끝나면 가공본과 함께 지운다. 세 프롬프트에 "얼굴은 일부러 가려져 있다 — 품질·진단 근거로 쓰지 마라"를 넣었다.

- 가리는 방식 `POD_FACE_MASK_STYLE`: **blur**(기본, 강한 가우시안 = 박스 짧은 변/6 — 이목구비가 사라지고 피부색·머리 윤곽만 남는다) | fill(회색 사각형). 둘 다 진단 차이는 아래 실측에서 흔들림 범위 안이었다. blur 가 기본인 이유는 사진처럼 보여 GPT 가 "가림"을 품질 문제로 볼 여지가 적기 때문
- 얼굴 위치: 프론트가 등록 때 보낸 포즈 랜드마크 0~10(코·눈·귀·입) 중 visibility ≥ 0.5 인 점. 귀~귀 폭을 얼굴 폭으로 보고 이마(위 0.75폭)·턱(아래 0.6폭)·좌우 0.25폭을 넓힌다. 점이 3개 미만(뒷모습·프레임 밖)이면 가리지 않고 로그만 남긴다 — 업로드 응답 `face_masked` 로 알 수 있다
- 프론트: **블러 작업 없음.** 원본을 그대로 올린다
- `/health` 의 `pipeline.face_mask` 가 true 여야 한다

**진단 동일성 실측 (2026-09-09, 사진 123→456, 실제 MediaPipe 랜드마크 `scripts/pose_landmarks_web.mjs`, gpt temperature=0).** 같은 사진으로 5회: 원본 2회(A·A2), 회색 2회(B·B2), 블러 1회(C). 세그 행은 5회 전부 동일(가림이 세그에 안 닿는다). 스크리닝은 5회 전부 통과.

| | 원본 A | 원본 A2 | 회색 B | 회색 B2 | 블러 C |
|---|---|---|---|---|---|
| 상완 좌우 등급 | SLIGHT | MODERATE | MODERATE | MODERATE | MODERATE |
| 하퇴 좌우 등급 | SLIGHT | SLIGHT | SLIGHT | SLIGHT | NONE |
| 종합 점수 | 69 | 62 | 62 | 62 | 69 |

- **원본끼리(A vs A2)가 원본 vs 가림만큼 다르다.** 상완 SLIGHT↔MODERATE, 점수 69↔62 는 temperature=0 이어도 나오는 GPT 흔들림이지 가림의 영향이 아니다. 가림(회색·블러) 결과는 전부 이 흔들림 범위 안에 있다
- 즉 "가림 때문에 결과가 바뀐다"는 근거는 없다. 대신 **경계 부위(상완·하퇴)의 등급은 원래 실행마다 한 단계 흔들린다** — 이건 가림과 무관한 기존 문제다 (`vlm.call_json` 에 `seed` 를 주면 줄어들 수 있으나 보장은 아니다. 담당 B 영역)
- 재실측: `node scripts/pose_landmarks_web.mjs 사진 out/landmarks/x.json` → 팟 `POD_FACE_MASK=false/true` 로 스모크 `--keep --ref-landmarks --user-landmarks` → `scripts/compare_diagnosis.py --a --b` → `DELETE /users/me`. 같은 조건을 두 번 돌려 흔들림 기준선을 먼저 잰다

## 로컬에서 돌리기 (검증용)

```
# .env: PHOTO_PIPELINE=pod  POD_UPLOAD_SECRET=dev-secret
python -m app.pod.main                         # :8080  (GPU + 1b 가중치 필요)
uvicorn app.main:app --port 8000               # API (pod 모드)
python -m app.worker.run --kinds OCR_INBODY,ROUTINE_GEN,ROUTINE_PATCH
python scripts/smoke_pod_pipeline.py --ref photos/reference.jpg --user photos/mybody.jpg
```

네트워크 없이 관문만: `python scripts/verify_pod_upload.py` (스크리닝·파이프라인은 가짜로 대체 — 얼굴 가림은 실제로 검사한다).
