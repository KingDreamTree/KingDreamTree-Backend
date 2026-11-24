# 사진 저장과 노트북 GPU 시연

현재 계산식과 모델을 유지하고 사진 전달 경로를 바꾼다. 과거 코드 전체를 되돌리는 작업이 아니다.

```text
Android 앱 → AWS API → Supabase Storage(사진) + DB(사진 경로·작업)
                          ↓
                   노트북 GPU 워커(세그멘테이션)
                          ↓
                   Storage(맵) + DB(분석 상태·결과)
                          ↓
                   AWS LLM 워커(AI 진단)
                          ↓
                   앱이 API로 결과·사진·맵 조회
```

노트북은 Supabase에서 작업과 사진을 가져간다. 앱이 노트북에 접속하거나 사진을 RunPod에 직접
업로드하지 않는다. 노트북의 공개 IP·포트 개방·터널은 이 구성에 필요하지 않다.
인바디 OCR과 운동 루틴 생성도 기존 AWS LLM 워커가 맡는다.

## 서버와 노트북 설정

API와 두 종류의 워커 모두 같은 Supabase 프로젝트를 사용해야 한다.

| 실행 위치 | 설정/역할 |
| --- | --- |
| AWS API | `PHOTO_PIPELINE=storage`, Supabase 및 OpenAI API 설정 |
| AWS LLM 워커 | `PHOTO_PIPELINE=storage`, `WORKER_KINDS=OCR_INBODY,VLM_PART,VLM_OVERALL,ROUTINE_GEN,ROUTINE_PATCH` |
| 노트북 GPU 워커 | `PHOTO_PIPELINE=storage`, `SAPIENS_DEVICE=auto` 또는 `cuda`, 기존 `MODEL_DIR`/`SAPIENS_SIZE`, Supabase API 설정 |

이전 Pod 구성의 WORKER_KINDS에는 VLM_PART/VLM_OVERALL이 빠져 있을 수 있으므로 배포 때 수정한다.
노트북은 SEG_REFERENCE/SEG_USER만 처리하며 기존 RunPod의 작업 소비와 동시에 운영하지 않는다.
GPU 워커를 여러 개 띄우면 각각 모델 메모리를 사용하므로 시연에서는 한 프로세스만 실행한다.
실제 `.env` 파일 전체를 다른 서버에 복사하지 않고 각 실행 위치에 필요한 설정만 관리한다.
특히 `.env.flyway`의 DB 직접 접속 정보는 로컬 전용이며 이 워커에 필요하지 않다.

## 노트북에서 실행 — Git Bash

저장소 루트에서 기존 가상환경과 모델을 사용한다.

```bash
cd /c/Users/dyund/refit-backend
# 합성 이미지로 GPU/모델만 확인. DB 작업을 가져오거나 OpenAI를 호출하지 않는다.
.venv/Scripts/python.exe scripts/local_gpu.py check

# 실제 공유 DB의 세그멘테이션 작업을 처리한다.
.venv/Scripts/python.exe scripts/local_gpu.py run
```

PowerShell에서는 `.venv/Scripts/python.exe`를 `.\.venv\Scripts\python.exe`로 사용할 수 있다.
`run`은 GPU 점검과 모델 준비가 성공한 뒤 작업을 가져온다. CPU로 조용히 대체하지 않으며,
PHOTO_PIPELINE이 storage가 아니면 작업을 소비하지 않고 종료한다.
Ctrl+C는 현재 작업을 마친 뒤 종료한다. 시연 전에 미리 실행해 모델 로딩을 끝내 두고
전원·인터넷 연결과 절전 설정을 확인한다. 워커 로그에서 잡 시작/완료를 확인한다.

`check` 결과의 inference_ms는 모델 추론만 측정한다. 파일 업로드·다운로드와 OpenAI 진단은
포함하지 않는다. 합성 이미지 검사는 실사진의 부위 인식 품질을 검증하는 테스트가 아니다.

## Android 앱의 API 순서

인증 PR #206을 함께 반영한 서버에서는 리핏 Bearer 토큰을 사용한다. 이 문서는 인증을 우회하는
별도 계정 발급을 제안하지 않는다. 로그인 실제 연동과 공유 DB 인증 마이그레이션은 별도 선행 조건이다.

1. 로그인 후 분석 세션을 만들거나 기존 세션을 조회한다.
2. `GET /api/v1/pose-criteria`로 기존 관절·자세·크기 판정 기준을 받는다.
3. 앱에서 사진을 선택해 기존 기준과 동일한 좌표/점수를 계산한다.
4. `POST /api/v1/sessions/{id}/photos/reference`에 multipart로 기준 사진을 업로드한다.
5. `POST /api/v1/sessions/{id}/photos/user`에 사용자 사진과 점수를 업로드한다.
6. 두 응답의 job_id를 `GET /api/v1/jobs/{job_id}`로 확인한다.
7. 세그멘테이션이 성공하면 기존 `POST /api/v1/sessions/{id}/analysis`로 AI 진단을 시작한다.
8. 기존 분석 진행/결과 API와 세그멘테이션 API로 사진 위에 결과를 표시한다.

두 사진 업로드에는 `file`, `pose_landmarks`(33개 JSON 배열), `pose_scale_basis`가 필요하다.
사용자 업로드에는 `capture_source=UPLOAD`, `pose_similarity`, `framing_score`를 추가한다.
시연은 `pipeline=full`을 사용한다. 기존 CAPTURE/quick API는 호환용으로 남지만 앱 화면에서는
사진 선택만 제공한다. 앱은 upload-token을 요청하거나 Pod /upload를 호출하지 않는다.

같은 세션의 같은 종류 사진을 다시 올리면 기존 사진/결과를 교체한다. 새 측정의 과거 기록을
보존하려면 기존 세션을 보관하고 새 세션을 생성해야 한다. 히스토리 목록/그래프는 별도 작업이다.

## 사진 재조회와 맵 표시

- 사진 조회: `GET /api/v1/sessions/{id}/photos/reference` 또는 `/photos/user`
- 맵 조회: `GET /api/v1/photos/{photo_id}/segmentation`
- 사진 조회 응답의 signed_url, 맵 응답의 photo_url은 서버에 저장된 가공 JPEG다.
- 저장 사진은 거울 보정·3:4 크롭이 끝난 상태다. was_mirrored가 true여도 URL 사진을 다시 뒤집지 않는다.
- 맵은 merged_map_url을 우선 사용하고 없으면 map_url을 사용한다. 사진과 맵을 같은 화면 영역에 맞춰 표시한다.
- 사진 크기와 맵 크기는 다를 수 있다. photo_width/height와 map_width/height의 좌표계를 구분한다.
- 응답의 pose_landmarks는 저장 사진 좌표계다. 사진 업로드 응답과 이후 조회 응답이 같은 좌표를 제공한다.
- 자세 계산에는 좌표와 해당 이미지의 종횡비를 함께 사용한다. 원본 좌표와 가공 사진 비율을 섞지 않는다.
- URL은 만료된다. signed_url_expires_at 이후에는 사진/맵 API를 다시 조회한다. URL을 영구 식별자로 저장하지 않는다.
- 사진 재조회는 서버 저장 파일을 사용하므로 IndexedDB에 원본을 영구 보관할 필요가 없다.

## 사진 처리와 오류

저장 사진과 세그 입력은 기존 전처리를 유지한다. OpenAI의 스크리닝·부위 진단·종합 진단에
전달할 때만 저장된 좌표로 얼굴 가림 복사본을 만든다. 복사본은 다시 Storage에 저장하지 않는다.
STORAGE_FACE_MASK_STYLE은 blur(기본) 또는 fill이다. 기존 Pod 경로의 설정은 별도로 유지한다.

얼굴 가림은 기존 랜드마크 기반 알고리즘이다. 얼굴을 검출하지 못한 경우에는 기존 Pod와 같이
박스를 만들지 못하므로 모든 사진의 익명화를 보장하는 기능은 아니다. 저장 사진 자체는 가림 전
사진이므로 인증·소유권 확인과 private Storage 접근을 함께 적용해야 한다.

좌표가 아예 없는 과거 사진은 AI에 그대로 보내지 않는다. 기준 사진이라면 409로 재업로드를
요구하고, 진단 워커에서는 재시도해도 해결되지 않는 오류로 처리한다.
기준 사진 다운로드가 실패하면 503으로 재시도를 안내하며 비교 검사를 생략하고 사진을 저장하지 않는다.
기존 자세/프레이밍 기준 미달은 422, 파일 누락/형식 오류는 400이다. 오류의 세부 코드는 Swagger를 따른다.

## 적용 및 검증 범위

현재 DB에는 사진 경로·랜드마크·맵·작업 필드가 있어 이 변경에는 신규 DB 마이그레이션이 필요하지 않다.
저장·조회·실패 처리와 가림 경로는 외부 서비스 없는 테스트로 검증한다. 노트북에서는 기존 모델의
합성 이미지 추론을 별도로 실행한다. 실제 API→Storage→노트북→AI→Android 전체 시연은 로그인과
서버 설정 전환 후 확인해야 한다. 이 문서 작성만으로 공유 서버를 전환하지 않는다.
