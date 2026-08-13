# Frontend 통합 가이드

> **정확한 요청/응답 필드 정의는 [Swagger UI — /docs](http://localhost:8000/docs) 를 진실의 원천으로 사용하세요.**  
> 이 문서는 호출 흐름·이미지 규칙·에러 형태 등 Swagger에 담기 어려운 맥락을 보완합니다.

---

## Base URL

| 환경 | URL |
|------|-----|
| 로컬 개발 | `http://localhost:8000` |
| 스테이징 | TBD |
| 프로덕션 | TBD |

---

## 호출 순서

```
1. POST /analyze   — 사용자 이미지 + 레퍼런스 이미지 업로드 → 세그멘테이션 결과 반환
2. POST /compare   — analyze 결과를 토대로 체형 비교 분석 (Claude Call1)
3. POST /routine   — compare 결과를 토대로 개인화 운동 루틴 생성 (Claude Call2)
```

각 단계의 응답 값을 다음 단계의 요청 body에 전달합니다.

---

## 이미지 입력 규칙

- **형식**: JPEG, PNG, WebP, **HEIC** 모두 허용. 서버에서 HEIC → JPEG 자동 변환.
- **리사이즈**: 장변 기준 최대 1024px로 서버에서 자동 축소 (비율 유지).
- **Content-Type**: `multipart/form-data`
- **업로드 크기 제한**: 요청 당 최대 **20MB**

---

## Mock 모드

서버 실행 시 `USE_MOCK=true` 환경 변수를 설정하면 실제 모델·Claude API·Supabase 호출 없이  
가짜 데이터로 세 엔드포인트 모두 200 응답을 반환합니다. API 키 없이 바로 개발 가능합니다.

```bash
USE_MOCK=true uvicorn app.main:app --reload --port 8000
```

---

## 공통 에러 응답 형태

모든 에러는 아래 스키마로 통일됩니다.

```json
{
  "detail": "에러 설명 문자열"
}
```

| HTTP 상태 | 의미 |
|-----------|------|
| `400` | 잘못된 요청 (이미지 형식 오류 등) |
| `422` | Validation 실패 (필수 필드 누락 등) |
| `500` | 서버 내부 오류 |

---

## Auth

현재 단계에서는 **인증 없음**. 추후 Supabase Auth 연동 시 이 문서를 업데이트합니다.

---

## 예시 curl

```bash
# 1. analyze
curl -X POST http://localhost:8000/analyze \
  -F "user_image=@/path/to/user.jpg" \
  -F "ref_image=@/path/to/reference.jpg"

# 2. compare (analyze 응답의 필드를 JSON body로 전달)
curl -X POST http://localhost:8000/compare \
  -H "Content-Type: application/json" \
  -d '{"analysis_id": "uuid-from-analyze", "user_seg": {...}, "ref_seg": {...}}'

# 3. routine (compare 응답의 필드를 JSON body로 전달)
curl -X POST http://localhost:8000/routine \
  -H "Content-Type: application/json" \
  -d '{"analysis_id": "uuid-from-analyze", "comparison": {...}}'
```

> 실제 필드명·타입은 `/docs` Swagger에서 확인하세요.
