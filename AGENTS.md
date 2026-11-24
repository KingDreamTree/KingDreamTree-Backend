# Refit 작업 기준

- 작업 전에 [CONTRIBUTING.md](CONTRIBUTING.md)의 협업 흐름과 실제 `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md`를 확인한다.
- 새 작업은 실제 GitHub 이슈와 담당자를 확인하고, `app/develop`에서 이슈별 작업 브랜치를 만들어 PR을 준비한다. `main`과 웹 기준 태그는 보존한다.
- Issue·PR Assignee를 각각 실제 작업자로 지정한다. 다른 프로젝트의 담당자나 합의하지 않은 Reviewer를 임의로 배정하지 않는다.
- PR 항목 `📌 작업 내용`, `✅ 체크리스트`, `🔗 관련 이슈`, `💬 리뷰어에게`의 순서를 유지한다. 체크리스트는 작업에 맞추고 확인한 근거만 완료로 표시한다.
- API 계약을 바꾸면 같은 작업에서 FastAPI 정의·Swagger/OpenAPI·관련 검증을 함께 갱신한다.
- DB 변경은 `docs/database-migrations.md`와 `db/flyway/sql/`을 따른다. 실제 Supabase 적용은 로컬에서 수행하며 CI의 임시 DB 검사와 구분한다.
- `.env.flyway`, DB 접속 정보, 덤프, 실제 사용자 데이터는 GitHub·AWS·이슈·PR에 복사하지 않는다. 사용자 미커밋 변경은 보존한다.
- GitHub 기본 브랜치와 PR 대상은 `app/develop`이다. Issue·PR 템플릿은 이 브랜치에서 자동 표시된다. 병합으로 완료되는 이슈는 `Closes #번호`, 배포 등 후속 확인이 남은 이슈는 `Refs #번호`로 연결하고 확인 후 닫는다.
