# GPU 팟 직접 업로드 경로 (PHOTO_PIPELINE=pod)

| | |
|---|---|
| **작성** | 2026-09-09 |
| **목적** | 사진을 서버(API·Storage)에 두지 않고, GPU 팟이 메모리에서 처리해 결과만 남긴다 |
| **담당** | A |
| **상태** | 코드 완료 · 로컬 검증 완료 · **RunPod·EC2 배포는 미실행** (서버 꺼져 있음) |

## 무엇이 바뀌나

```
[기기] 얼굴 블러 → 원본은 IndexedDB
   ├─ 기준/사용자 사진 등록 ──랜드마크·메타만──▶ [API]  POST /photos/reference · /photos/user
   ├─ 분석 시작 ──▶ [API] POST /sessions/{id}/upload-token  (팟 주소는 안 줌 — 프론트 고정)
   └─ 두 장 + X-Upload-Token ──▶ [팟] POST /upload
                                    ① 토큰·크기·속도 제한 (본문 읽기 전)
                                    ② 거울 되돌림·3:4 크롭·리사이즈 (종전 _store 와 같은 함수)
                                    ③ 사용자 사진 스크리닝(GPT) → 이 응답에서 즉시 통과/반려
                                    ④ 202 → 뒤에서 세그×2 → 부위 진단 → 종합 진단 (메모리)
                                    ⑤ 맵·진단문·수치·crop_box 만 Supabase, 사진은 메모리에서 해제
[기기] ──폴링──▶ [API] GET /jobs/{id} · /analysis/progress · /analysis   (그대로)
[화면] = 기기 원본을 (flipped 면 좌우 반전 후) crop_box 로 잘라 + 서버 맵
```

사진이 존재하는 곳: 기기 · 팟 메모리(처리 중) · OpenAI. Storage·DB·API·EC2 워커에는 없다.

## 코드 지도

| 위치 | 역할 |
|---|---|
| `app/pod/server.py` | `POST /upload`, `GET /health`. 관문 → 가공 → 스크리닝 → 대기열 |
| `app/pod/pipeline.py` | 처리 스레드(1개). 잡을 PROCESSING 으로 만들어 세그·진단 핸들러를 그대로 돌린다 |
| `app/pod/main.py` | 진입점. 점검 → 모델 예열 → 재시작 잔여 잡 FAILED 정리 → 서버 |
| `app/services/photo_source.py` | 핸들러가 사진을 읽는 유일한 창구 — 메모리 우선, 없으면 Storage(종전) |
| `app/services/upload_token.py` | HMAC 일회용 토큰. API 발급 / 팟 검증 |
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
```

API 쪽: `PHOTO_PIPELINE=pod`, 같은 `POD_UPLOAD_SECRET`, 그리고 compose 의 `WORKER_KINDS=OCR_INBODY,ROUTINE_GEN,ROUTINE_PATCH` (EC2 워커가 VLM 잡을 집지 않게).

## RunPod 설정 (미실행 — 팟을 올릴 때)

- **Secure Cloud** 로 만든다 (Community Cloud 아님). 이미지: `kingdreamtree-pod:<tag>` (레지스트리에 푸시한 것)
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
- **마이그레이션 전**: `photo.crop_box` 컬럼이 없으면 팟이 경고를 남기고 crop_box 없이 저장한다 (업로드는 죽지 않음). 단 `storage_path` NOT NULL 은 폴백이 없다 — API 의 랜드마크 등록이 DB 에서 막히므로 **pod 모드는 마이그레이션 적용이 전제**다
- **대기열 초과**: 업로드가 503 `POD_BUSY` — 프론트는 "잠시 후 다시"
- **디버깅**: 문 없는 팟에는 들어갈 수 없다. 같은 이미지에 SSH 템플릿을 얹은 **스테이징 팟**을 따로 만들어 거기서 본다. 프로덕션 팟은 재배포만
- **잔여 확인**: `scripts/check_pod_no_photo_residue.py` (스테이징 팟·로컬), `scripts/audit_photo_privacy.py --session <id>` (DB·Storage 감사)

## 얼굴 블러 선행 실측 (2026-09-09, `scripts/measure_face_blur.py`)

사진 4장(photos/123·456·reference·mybody), 로컬 RTX 5060 · 1b fp16. 원본 vs 얼굴 블러본의 세그 결과 비교.

| 블러 방식 | 인물 픽셀(분모) 변화 | 부위 면적비 최대 차 | 부위 IoU 최소 | 판정 |
|---|---|---|---|---|
| 머리+목 박스(Face_Neck·Hair +15%) · 강한 블러(짧은 변/6) | −12 ~ −15% | 15 ~ 18% (몸통 픽셀 −15%) | 0.835 | **다름** |
| 얼굴만 · 강한 블러(짧은 변/6) | −7 ~ −9% (1장은 ±0) | 7 ~ 10% | 0.979 | **다름** |
| **얼굴만 · 약한 블러(짧은 변/12)** | **±1%** | **≤ 2.5%** | **≥ 0.98** | **같음 (4/4)** |

- 사피엔스는 **강하게 뭉갠 얼굴을 배경으로** 분류한다 → 인물 픽셀(모든 부위 비율의 분모)이 줄고, 목·어깨까지 가리면 몸통도 깎인다
- 약한 블러(반지름 ≈ 얼굴 짧은 변의 1/12, 110px 얼굴이면 9px)는 얼굴로 인식된 채 형태만 뭉개진다. is_valid 변경은 어느 설정에서도 없었다
- 스크리닝(GPT) 판정은 강한 블러·머리 박스에서도 2/2 쌍 동일 (suitable·rule·blurry 관찰 모두 같음)
- **프론트 지침**: 포즈 랜드마크(눈·코·귀)로 잡은 **얼굴 박스만**, 가우시안 반지름 = 박스 짧은 변 / 12. 목·머리카락까지 넓히거나 더 세게 뭉개면 결과가 달라진다. 기준 사진도 같은 규칙으로 (분모 변화가 두 장에서 상쇄되도록)
- 재실측: `python scripts/measure_face_blur.py --pairs a.jpg:b.jpg --box face --blur 12 --screen`

## 로컬에서 돌리기 (검증용)

```
# .env: PHOTO_PIPELINE=pod  POD_UPLOAD_SECRET=dev-secret
python -m app.pod.main                         # :8080  (GPU + 1b 가중치 필요)
uvicorn app.main:app --port 8000               # API (pod 모드)
python -m app.worker.run --kinds OCR_INBODY,ROUTINE_GEN,ROUTINE_PATCH
python scripts/smoke_pod_pipeline.py --ref photos/reference.jpg --user photos/mybody.jpg
```

네트워크 없이 관문만: `python scripts/verify_pod_upload.py` (스크리닝·파이프라인은 가짜로 대체).
