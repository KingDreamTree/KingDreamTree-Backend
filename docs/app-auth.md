# React Native 카카오 로그인 계약

이 문서는 앱 브랜치의 인증 구현을 설명한다. `docs/api-spec-v2.md`의 익명 UUID 인증은
웹 시절 명세이며 이 브랜치의 인증 계약으로 사용하지 않는다. 실제 배포 여부는
서버 Swagger와 배포 커밋으로 확인한다.

## 서비스 등록과 설정

Kakao Developers에서 리핏 서비스를 등록하고 카카오 로그인을 활성화한다.
앱 담당자는 Android/iOS 플랫폼 정보와 SDK용 앱 키를 설정한다. 백엔드는 해당 서비스의
숫자 **앱 ID**를 `KAKAO_APP_ID`로 설정한다. 앱 ID는 Native 앱 키·REST API 키·Admin 키와 다르다.
설정이 없으면 로그인은 `503 AUTH_NOT_CONFIGURED`로 실패하며 임의 계정으로 우회하지 않는다.

백엔드가 카카오 액세스 토큰을 검증하는 방식이므로 이 흐름에는 백엔드의 카카오 콜백 URL이나
Admin 키가 필요하지 않다. 앱의 SDK 설정은 선택한 React Native 라이브러리에 맞춘다.
카카오 토큰은 서버에서 앱 ID·회원번호·남은 수명을 검증한 뒤 폐기하며 저장하지 않는다.
닉네임·이메일·프로필 사진은 현재 기능에 필요하지 않아 수집하지 않는다.

## 앱에서 호출하는 순서

1. 카카오 SDK 로그인으로 **카카오 액세스 토큰**을 얻는다. 로그인 UI와 SDK 설정은 앱 담당 범위다.
2. `POST /api/v1/auth/kakao`에 JSON `{"access_token":"카카오 액세스 토큰"}`을 보낸다.
3. 응답의 **리핏 access_token**을 앱의 OS 보안 저장소에 보관한다. JWT로 해석하지 않는다.
4. 보호된 API마다 `Authorization: Bearer <리핏 access_token>`을 붙인다.
5. 재실행 시 `GET /api/v1/users/me`로 로그인 상태와 사용자 정보를 확인한다.

정상 응답은 HTTP 200이며 구조는 다음과 같다. 아래 토큰/사용자 값은 설명용이다.

```json
{
  "access_token": "refit_...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "expires_at": "2026-11-24T06:00:00Z",
  "user": {
    "user_id": "00000000-0000-4000-8000-000000000001",
    "is_pro_user": false,
    "created_at": "2026-11-24T05:00:00Z"
  }
}
```

리핏 토큰은 256비트 무작위 값으로 만든 불투명 Bearer 토큰이다. DB에는 SHA-256 해시만 저장한다.
인증할 때마다 서버 세션의 존재와 만료를 확인하므로 로그아웃 이후 새 요청은 즉시 차단된다.
검증을 이미 통과해 처리 중인 요청을 취소하는 기능은 아니다.
기본 수명은 1시간이며 `AUTH_SESSION_TTL_SEC`로 60~86400초 안에서 설정한다.
이번 구현에는 별도 리핏 refresh token API가 없다. 만료되면 카카오 SDK의 갱신/로그인으로
유효한 카카오 토큰을 얻어 로그인 API를 다시 호출한다. 매번 동의 화면을 보여준다는 뜻은 아니다.

사진·분석 API 주소와 데이터 구조는 이 작업에서 변경하지 않는다. `X-User-Id`는 인증에 사용하지 않으며,
이 헤더만 보내면 401이다. 유효한 Bearer와 함께 보내도 사용자 선택에 영향을 주지 않는다.
`POST /users`는 익명 계정을 만들지 않으며 인증된 사용자 조회 호환 경로(200, deprecated)로 남긴다.
앱에서는 `GET /users/me`를 사용한다. 기존 웹 클라이언트는 새 인증 계약에 맞춰 수정해야 사용할 수 있다.

## 로그아웃과 회원 탈퇴

- `POST /api/v1/auth/logout`: 현재 리핏 Bearer 필요. 성공은 본문 없는 204.
- 서버는 현재 리핏 세션만 삭제한다. 다른 기기의 세션, 사진, 분석 기록, 계정은 유지한다.
- 앱은 별도로 카카오 SDK 로그아웃을 수행하고 리핏 토큰과 개인정보 캐시를 지운다.
- 이미 만료/폐기된 토큰의 로그아웃 요청은 401이다. 앱은 이 경우에도 로컬 로그아웃을 마칠 수 있다.
- 503/네트워크 장애는 서버 폐기 완료가 아니다. 재시도하거나 사용자가 재시도할 수 있게 안내한다.
- 카카오 계정 전체 로그아웃·카카오 연결 해제·리핏 회원 탈퇴는 서로 다른 동작이다.
- 기존 `DELETE /users/me`는 사진 등 Storage 삭제 후 계정을 삭제한다. 새 계정 연결과 리핏 세션은 FK로 함께 삭제된다.
  카카오 연결 해제 API는 이번 구현에 포함하지 않는다.

## 오류와 Swagger

기존 `{ "error": { "code": "...", "message": "..." } }` 형식을 유지한다.
앱에 표시할 오류 문구는 한국어다.

| 상태 | 코드 | 앱 처리 |
| --- | --- | --- |
| 400 | INVALID_REQUEST | 요청 형식 수정. 사용자 ID를 대신 보내지 않는다. |
| 401 | INVALID_KAKAO_TOKEN | 만료·잘못된 토큰 또는 다른 카카오 앱의 토큰. SDK 로그인/갱신 확인 |
| 401 | MISSING_ACCESS_TOKEN / INVALID_ACCESS_TOKEN | 리핏 로그인 필요. 보호된 API에 카카오 토큰을 직접 보내지 않는다. |
| 429 | TOO_MANY_REQUESTS | 응답 detail의 retry_after_sec 이후 재시도 |
| 503 | AUTH_NOT_CONFIGURED | 서버의 KAKAO_APP_ID 설정 필요 |
| 503 | AUTH_UNAVAILABLE | 카카오 또는 인증 DB 일시 장애. 로그인 실패/회원 탈퇴로 취급하지 않는다. |

Swagger에서는 `/auth/kakao` 응답의 리핏 토큰만 복사하여 **Authorize → RefitBearer**에 입력한다.
카카오 토큰은 로그인 요청 본문에만 사용한다. 토큰·키를 채팅, 이슈, PR, 앱 로그에 남기지 않는다.
카카오 검증과 인증 저장소 오류의 원문은 응답에 노출하지 않으며 요청 검증 오류에도 입력값을 싣지 않는다.

로그인 요청의 IP 제한은 현재 API 프로세스 내에서 동작한다. API 인스턴스/프로세스를 늘릴 경우
공유 저장소 기반 제한으로 옮겨야 한다. 프록시가 실제 접속 IP를 전달하는지도 배포 때 확인한다.

## DB와 배포 순서

1. Flyway로 `user_identity`, `auth_session` 및 서비스 역할 전용 RPC를 추가한다.
2. 새 테이블은 RLS를 켜고 anon/authenticated의 직접 조회·변경·RPC 실행을 막는다.
   기존 테이블의 RLS/권한을 전부 재설계하는 작업은 포함하지 않는다.
3. 카카오 앱 ID·회원번호의 조합을 내부 사용자 UUID에 연결한다. 동시 첫 로그인도 동일 계정으로 연결한다.
4. 기존 익명 사용자와 기록은 보존한다. 클라이언트가 제출한 UUID만으로 기존 기록을 카카오 계정에 이전하지 않는다.
5. 로컬 일회성 PostgreSQL 검증 후 담당자가 로컬에서 백업·실제 DB 적용·validate를 수행한다.
6. 서버에 KAKAO_APP_ID를 설정하고 앱의 Bearer 전환과 함께 백엔드를 배포한다.
7. 실제 SDK 로그인·한국어 화면·로그아웃·사진 소유권을 기기에서 확인한다.

기존 웹 기준 main/태그는 유지한다. 이 마이그레이션은 기존 테이블의 데이터나 키를 변경하지 않는다.
공유 DB 직접 접속 정보와 `.env.flyway`는 로컬에만 보관한다.

## 한국어 로그인 화면

리핏 API의 한국어 오류와 카카오가 표시하는 화면은 별개다. 웹 인가 화면을 사용하는 경로는
카카오의 `lang=ko` 지원을 활용할 수 있다. 네이티브 카카오톡 로그인 화면까지 백엔드에서
한국어로 강제할 수 있다고 가정하지 않고, 앱 담당자가 실제 SDK·기기 언어로 확인한다.

공식 근거: [카카오 액세스 토큰 정보 조회](https://developers.kakao.com/docs/ko/kakaologin/rest-api#req-access-token-info),
[카카오 로그인 설정](https://developers.kakao.com/docs/ko/kakaologin/prerequisite),
[카카오 로그아웃](https://developers.kakao.com/docs/ko/kakaologin/common#logout),
[동의 화면 언어 FAQ](https://developers.kakao.com/docs/en/kakaologin/faq#can-i-set-the-language-applied-to-consent-screen).
