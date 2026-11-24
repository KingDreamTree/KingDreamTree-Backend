# Refit GitHub 협업 가이드

Refit 앱 개발은 **Issue → Assignee → Branch → Develop → Commit/Push → PR/Review → Merge/Sync** 순서로 진행한다.
새 팀원과 AI 모두 이 문서 및 저장소의 실제 Issue·PR 템플릿을 기준으로 작성한다.
HIRELINK 협업 가이드의 형식과 Swagger 관리 원칙을 Refit에 맞추고, Assignee와 Flyway 운영 규칙을 추가했다.

## 1. 협업 프로세스

| 순서 | 할 일 | 확인할 곳 |
| --- | --- | --- |
| Issue | 작업 목적·완료 기준·해야 할 일을 등록 | GitHub Issues |
| Assignee | 실제 작업 담당자를 지정 | Issue 오른쪽 Assignees |
| Branch | 최신 `app/develop`에서 이슈별 브랜치 생성 | Git / IDE |
| Develop | 구현하고 작업에 필요한 검증 수행 | 로컬 |
| Commit/Push | 의미 있는 단위로 기록하고 작업 브랜치 push | Git |
| PR/Review | `app/develop`으로 PR, Assignee와 Reviewer 확인 | GitHub Pull requests |
| Merge/Sync | 검사·리뷰 후 병합, 배포 및 이슈 종료 확인, 로컬 최신화 | GitHub + Git |

`main`은 기존 웹 전용 코드로 보존한다. 앱 작업을 `main`에서 시작하거나 `main`으로 PR을 만들지 않는다.
웹 기준 태그는 `web-only-before-app-2026-11-24`다. `app/develop`에도 기능 변경을 직접 push하지 않고 작업 브랜치와 PR을 사용한다.
기존 서버·브랜치 운영은 [앱 개발 안내](docs/app-development.md)를 참고한다.

## 2. Issue 유형과 작성법

새 작업은 먼저 이슈로 등록한다. 이미 같은 범위의 이슈가 있다면 중복 생성하지 않고 기존 이슈에 연결한다.
큰 작업은 독립적으로 구현·검증할 수 있는 단위로 나눈다.

| 유형 | 용도 | 제목 예시 | 양식 |
| --- | --- | --- | --- |
| Feature | 새로운 기능·API | `[FEATURE] 운동 루틴 조회 기능 추가` | [Feature](.github/ISSUE_TEMPLATE/feature.md) |
| Fix | 오류·결함 수정 | `[FIX] 루틴 완료 상태 불일치 수정` | [Fix](.github/ISSUE_TEMPLATE/fix.md) |
| Other | 문서·환경·기타 | `[OTHER] 협업 가이드 정리` | [Other](.github/ISSUE_TEMPLATE/other.md) |

위 기능·오류 제목은 작성 예시이며 실제 미구현 기능이나 확인된 결함을 뜻하지 않는다.
모든 유형은 아래 항목과 순서를 유지한다.

```markdown
### Description
> 작업이 필요한 이유와 완료했을 때의 동작을 간결하게 설명한다.

### Tasks
- [ ] 실제 작업 담당자를 Assignees에 지정
- [ ] 구현·수정할 작업
- [ ] 작업에 필요한 검증

### Note (Optional)
- 관련 자료, 제약, 미결 사항
```

Description은 배경과 완료 기준, Tasks는 실제 수행할 일, Note는 관련 문서와 주의점을 담는다.
API 계약 변경이면 Swagger/OpenAPI와 관련 검증을, DB 변경이면 Flyway SQL·테스트·백업·적용 계획을 Tasks에 포함한다.
작업 시작 전에는 미완료 항목을 체크하지 않는다. 완료 후 실제 결과에 맞춰 갱신한다.

## 3. Assignee와 Reviewer

| 항목 | 역할 | 지정 시점 |
| --- | --- | --- |
| Issue Assignee | 구현·진행 상황·완료 확인을 책임지는 실제 작업자 | 이슈 생성 시, 늦어도 작업 시작 전 |
| PR Assignee | PR 수정·검증·리뷰 의견 대응을 책임지는 작성자 | PR 생성 시 |
| Reviewer | 변경 내용을 검토하는 팀원 | PR 생성 시 팀 합의에 따라 요청 |

Issue와 PR은 각각 Assignee를 지정한다. 이슈 담당자가 PR에 자동으로 지정된다고 가정하지 않는다.
일반적으로 둘은 같은 작업자다. 공동 작업이면 실제 참여자만 추가하고 주 담당자를 본문에 적는다.
인수인계할 때는 본문에 진행 상황을 남기고 Issue·PR 양쪽 담당자를 갱신한다.

Assignee는 승인자가 아니다. Reviewers는 별도 항목이며, 다른 프로젝트 담당자나 AI 이름을 임의로 배정하지 않는다.
담당자가 확정되지 않았다면 착수 전에 정하고, 합의하지 않은 리뷰어를 요청하지 않는다.
현재 가이드 정리 작업 #201의 담당자는 GitHub 계정 `doondowob`다. 이 계정을 모든 작업의 고정 담당자로 사용하지 않는다.

## 4. 브랜치 생성과 네이밍

| 작업 | 패턴 |
| --- | --- |
| 기능 | `feat/#이슈번호-short-description` |
| 버그 | `fix/#이슈번호-short-description` |
| 리팩터링 | `refactor/#이슈번호-short-description` |
| 문서·설정·기타 | `chore/#이슈번호-short-description` |

실제로 생성된 이슈 번호를 사용하고, 설명은 영문 소문자와 하이픈으로 작성한다.
다음은 이 가이드 정리 이슈 #201의 명령 예시다. 다른 작업에는 해당 이슈 번호와 설명을 사용한다.

```bash
git switch app/develop
git pull --ff-only origin app/develop
git switch -c 'chore/#201-collaboration-guide'
```

명령 실행 전 현재 변경 사항을 확인한다. 다른 작업자의 변경을 덮어쓰거나 승인 없이 강제 push하지 않는다.

## 5. 커밋 메시지

형식은 `type: 짧은 변경 요약`이다. 가능한 한 한 커밋에 하나의 의미 있는 변경을 담는다.

| type | 용도 | 예시 |
| --- | --- | --- |
| feat | 기능 추가 | `feat: 루틴 조회 응답에 진행 상태 추가` |
| fix | 오류 수정 | `fix: 루틴 완료 횟수 중복 집계 수정` |
| refactor | 동작을 유지하는 구조 개선 | `refactor: 루틴 상태 계산 로직 분리` |
| docs | 문서 수정 | `docs: Refit 협업 가이드 정리 (#201)` |
| chore | 설정·기타 | `chore: Issue 템플릿 추가` |
| test | 테스트 | `test: 루틴 상태 경계 조건 검증` |

커밋 메시지는 개별 변경의 기록이고 PR 제목은 전체 작업의 요약이다. 둘을 억지로 같게 만들지 않는다.

## 6. PR 작성

base는 `app/develop`, compare는 해당 작업 브랜치로 지정한다.
[PR 템플릿](.github/PULL_REQUEST_TEMPLATE.md)의 이모지·제목·순서를 유지한다.

```markdown
## 📌 작업 내용
- 실제로 변경한 내용과 그 결과

## ✅ 체크리스트
- [ ] Issue·PR Assignee와 대상 브랜치 확인
- [ ] 이 작업에 필요한 검증 수행

## 🔗 관련 이슈
Refs #실제이슈번호

## 💬 리뷰어에게
- 중점 검토할 부분, 재현·테스트 방법, 확인하지 못한 사항
```

체크리스트는 고정 문구를 채우는 형식이 아니다. API·DB·인프라·문서 등 작업에 맞춰 검증 항목을 구성하고
해당하지 않는 항목은 삭제하거나 비해당 이유를 적는다. 실행한 테스트·명령·결과를 남기고 실제 확인한 것만 `[x]`로 표시한다.
필수 검증을 마친 뒤 PR을 제출한다. 검증이 불가능한 항목은 완료 처리하지 않고 이유와 제약을 적어 팀원과 논의한다.

GitHub 기본 브랜치는 현재 `main`이므로 `app/develop` 대상 PR에서는 `Closes #번호`만으로
이슈가 자동 종료되지 않는다. `Refs #번호`로 실제 이슈를 참조하고, 병합·필요한 배포 검증을 마친 담당자가
이슈 완료 항목을 확인한 뒤 직접 닫는다. [GitHub 이슈 연결 기준](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)

## 7. Swagger와 OpenAPI 명세 관리

API를 새로 구현하거나 외부 계약을 바꾸면 **같은 Issue와 PR에서 구현·Swagger/OpenAPI·검증을 함께 수정한다.**
내부 로직만 바뀌고 외부 계약이 같으면 명세 변경은 생략할 수 있다.

Refit은 FastAPI를 사용한다. 실제 라우트·Pydantic 스키마·서비스·예외 처리 코드를 기준으로 명세를 작성한다.

| 변경 항목 | 확인할 내용 |
| --- | --- |
| 경로·메서드·파라미터 | 라우트 선언, 경로·쿼리·헤더 파라미터, 설명과 실제 동작 일치 |
| 요청·응답 | Pydantic 필드·타입·설명·예시·required/nullable·enum |
| 성공·오류 응답 | 실제 상태 코드, 콘텐츠 타입, 응답·오류 스키마 |
| 예외 처리 | 서비스에서 발생하는 예외와 HTTPException·예외 핸들러의 실제 응답 |
| 명세 검증 | OpenAPI 생성 확인 및 변경된 계약에 필요한 테스트 |

실제 코드에서 발생하지 않는 상태 코드나 응답 예시를 임의로 추가하지 않는다.
생성된 `openapi.json`만 손으로 고치는 대신 API 정의를 수정한다.

```bash
python scripts/check_api_docs.py --output tmp/openapi.json
```

이 명령은 문서 생성 검사다. 외부 DB·AI 호출이나 모든 API 동작을 검증하지 않으므로 변경에 필요한 검증을 별도로 수행한다.
현재 PR CI는 `api-docs`와 `database-migrations`를 실행한다. `app/develop`에 병합되면
해당 커밋의 서버 이미지를 자동 배포하고 API 상태·Swagger·OpenAPI·배포 커밋을 확인한다.

- Swagger: https://api.refit.live/docs
- OpenAPI: https://api.refit.live/openapi.json
- API 기본 주소: https://api.refit.live/api/v1

## 8. Flyway와 DB 변경

Python 코드를 수정한다고 DB 구조가 자동으로 추론·변경되지는 않는다. DB 변경은 SQL 마이그레이션 파일로 작성한다.
상세 설정과 복원 절차는 [DB 마이그레이션 안내](docs/database-migrations.md)를 기준으로 한다.

1. Issue에 변경 목적·영향·완료 기준과 DB 적용 담당자를 기록한다.
2. `db/flyway/sql/`에 `V<UTC 날짜시간+마이크로초>__설명.sql`을 추가한다.
   예: `V20261125090000000000__add_example_column.sql`은 파일명 예시이며 실제 적용 버전이 아니다.
3. 기존 적용 SQL과 `V1__web_only_baseline.sql`은 수정하지 않는다. 이전 `db/migrations/`를 다시 실행하지 않는다.
4. `public.테이블명`을 명시하고 Flyway가 트랜잭션을 관리하도록 한다. 일반 SQL 파일에 별도 `BEGIN; COMMIT;`을 넣지 않는다.
5. 임시 PostgreSQL 테스트와 필요한 기존 데이터 호환성 검증을 수행하고 PR에 결과를 남긴다.
6. 담당자가 리뷰된 SQL을 로컬에서 적용한다. 적용 전에는 자동으로 백업과 `public` 복원 검증을 수행한다.
7. `validate`·`info` 결과와 실제 적용 버전을 PR에 기록하고, 코드 병합·배포를 진행한다.

Git Bash 예시:

```bash
.venv/Scripts/python.exe scripts/test_database_migrations.py
bash scripts/flyway.sh info
bash scripts/flyway.sh migrate
bash scripts/flyway.sh validate
bash scripts/flyway.sh info
```

공유 DB 적용은 담당자 한 명이 순서를 조율한다. PR 제출 전 로컬 검증과 병합 전 실제 DB 적용을 구분하고,
미적용 상태라면 PR에 그대로 적는다. **실제 DB 적용이 필요한 변경은 적용·검증을 마친 뒤 병합한다.**
CI의 임시 DB 검사는 실제 Supabase 적용 여부를 확인하거나 대신 적용하지 않는다.
DB 적용과 코드 배포 사이에도 기존 API·worker가 동작하므로 호환되는 추가 변경부터 적용하고,
컬럼 제거·이름 변경 등은 호환 코드 배포 후 별도 작업으로 진행한다.

`.env.flyway`와 DB 접속 정보는 로컬에만 둔다. GitHub Secrets·AWS 환경 파일·이슈·PR·로그에 복사하지 않는다.
DB 덤프와 실제 사용자 데이터도 업로드하지 않는다. 현재 웹 DB 기준은 Flyway baseline 1이며,
`main`이나 코드 태그로 돌아가는 것만으로 DB가 복원되지는 않는다.

## 9. 리뷰·병합·로컬 최신화

1. PR의 base·compare·관련 이슈·Assignee가 맞는지 확인한다.
2. 합의한 Reviewer가 구현·명세·DB 호환성과 검증 결과를 검토한다.
3. 수정 요청은 같은 브랜치에서 수정 → commit → push로 반영한다.
4. CI·필요한 검증·리뷰가 끝나고 충돌이 없으면 병합한다. DB 변경은 위의 실제 적용 순서를 따른다.
5. 병합 후 `app-deploy` 성공과 Swagger의 배포 커밋을 확인한다. 실패하면 완료 처리하지 않고 원인을 해결한다.
6. Issue Tasks를 실제 결과대로 갱신하고 이슈를 닫는다. 더 이상 필요 없는 작업 브랜치는 정리한다.
7. 로컬 `app/develop`을 최신화한다.

```bash
git switch app/develop
git pull --ff-only origin app/develop
```

리뷰 최소 승인 수, Squash/Rebase/Merge 방식, 브랜치 보호 강제 설정은 별도 팀 합의 사항이다.
이 문서의 권장 흐름과 실제 GitHub 강제 설정을 혼동하지 않는다.

## 10. AI 작성 규칙과 최종 체크

- Issue에는 앞으로 할 일을, PR에는 실제로 변경한 내용을 적는다.
- `Description → Tasks → Note (Optional)`과 PR의 `📌 → ✅ → 🔗 → 💬` 순서를 유지한다.
- 실제 코드·로그·테스트 근거만 사용한다. 미확인 검증, 이슈 번호, 담당자, 리뷰 승인, 배포 성공을 만들어내지 않는다.
- Swagger는 실제 라우트·스키마·서비스·예외 처리와 대조하고, Flyway는 파일과 실제 적용 이력을 구분한다.
- HIRELINK의 이름·담당 영역·가상 기능 예시를 Refit의 실제 담당자·완료 작업으로 가져오지 않는다.
- 기존 사용자 변경과 로컬 접속 정보는 보존한다. 코드 변경과 DB 복원은 별개로 취급한다.

| 단계 | 최종 확인 |
| --- | --- |
| Issue | 유형·목적·실제 Tasks·Assignee |
| 작업 시작 | 실제 이슈 번호, 최신 app/develop 기반 브랜치 |
| PR 제출 | 템플릿·작업 맞춤 검증·이슈 참조·PR Assignee |
| API/DB 변경 | Swagger 계약 일치, Flyway 파일·검증·적용 계획 |
| 병합 | CI·리뷰·충돌 확인, 필요한 실제 DB 적용 완료 |
| 병합 이후 | 배포·이슈 종료·브랜치 정리·로컬 최신화 |

## 11. GitHub 템플릿 표시와 문서 관리

Issue·PR 템플릿의 GitHub 자동 표시는 **저장소 기본 브랜치에 파일이 있어야** 활성화된다.
현재 기본 브랜치 `main`을 보존하므로, 앱 브랜치에만 추가한 양식이 GitHub 작성 화면에 자동으로 뜬다고 가정하지 않는다.
자동 표시가 되지 않으면 위 양식 파일에서 본문을 복사해 작성한다.
GitHub CLI를 사용할 때는 작성한 본문을 UTF-8 파일에 저장하고 `--body-file`로 전달한다.
Issue 템플릿의 YAML front matter는 본문에서 제외한다.
[GitHub 템플릿 표시 기준](https://docs.github.com/en/enterprise-cloud@latest/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates)

기본 브랜치나 보호 정책을 변경하기로 합의하면 이 문서의 PR 대상·템플릿 표시·이슈 종료 방식도 함께 갱신한다.
현재 문서와 양식은 자동 배정·자동 리뷰·실제 DB 자동 적용을 수행하는 워크플로가 아니다.

2026-11-24: HIRELINK 협업 가이드를 Refit에 맞춰 정리하고 Assignee·Flyway 규칙을 추가했다.
