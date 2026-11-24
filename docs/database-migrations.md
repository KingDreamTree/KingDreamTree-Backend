# Flyway와 웹 전용 DB 복원 기준점

웹 전용 코드 기준은 `web-only-before-app-2026-11-24` 태그다.
기존 Supabase DB는 전체 논리 백업을 만든 뒤, `public` 스키마의 18개 테이블과
19,704개 행을 격리된 PostgreSQL에 복원하여 스키마·행 수·내용 해시를 확인했다.
그 다음 기존 데이터를 유지한 채 Flyway **baseline 1**을 등록했다.

## 보관 위치와 범위

- 원본 백업: 로컬 `C:\Users\dyund\refit-backups\web-only-20261124T121931`
- `database.dump`: 전체 DB 논리 백업. 테이블 데이터·스키마·권한 정의를 포함한다.
- `roles.sql`: 역할 정의. 역할 비밀번호는 제외한다.
- `public-schema.sql`: 앱 스키마 DDL. 테이블 데이터와 권한은 제외한다.
- `inventory.json`: 테이블별 행 수와 내용 해시.
- `manifest.json`, `verification.json`: 대상 식별 해시, 검증 결과, 백업 파일 SHA-256.
- Git의 `db/flyway/sql/V1__web_only_baseline.sql`: 데이터 없는 앱 스키마 기준점.
  빈 테스트 DB 생성용이며, 기존 DB에서는 다시 실행하지 않는다.
- 적용 이력: Supabase의 `refit_migrations.flyway_schema_history`.
  앱의 `public` 스키마와 분리한다.

백업에는 Supabase Storage의 **파일 자체가 들어 있지 않다**. Storage 메타데이터는
DB 백업에 있지만 사진 등의 실제 파일은 별도로 보존해야 한다. Supabase 관리 스키마
(`auth`, `storage` 등)는 전체 덤프에 포함되지만 이번 복원 시험의 범위는 `public`이다.
따라서 새 Supabase 프로젝트 전체 복구까지 검증된 백업은 아니다.
기존 프로젝트·Storage를 유지한 상태에서 앱 DB를 웹 기준점으로 복구하는 기준이다.
격리 DB 복원 시험에서는 `--no-owner --no-privileges`를 사용했다.
운영 복구 시 소유권·접근 권한은 별도로 점검해야 한다.

## 접속 정보는 로컬에만

로컬 `.env.flyway`에서 `DATABASE_URL`과 `DATABASE_PASSWORD`를 읽는다.
Git과 Docker 빌드에서 제외하며 GitHub Secrets, AWS 환경 파일에도 복사하지 않는다.
`DATABASE_PASSWORD`는 연결 문자열의 비밀번호보다 우선한다. `.env`의
`SUPABASE_URL`과 같은 프로젝트인지 검사한다.

`scripts/database.py`는 로컬 Docker만 허용하고, 접속 정보를 메모리에서 일회성
컨테이너 환경으로 전달한다. TLS 인증서·호스트 검증을 사용한다. Docker를 사용하는
동안 로컬 관리자에게는 컨테이너 환경이 보일 수 있으므로 작업 후 컨테이너는 삭제한다.
Docker Desktop이 실행 중이어야 하며 Python 의존성은 기존 `requirements.txt`를 사용한다.

## 앞으로 DB를 변경하는 방법 — Git Bash

Python 코드만 수정한다고 Flyway가 DB 변경을 추론하지 않는다.
DB 변경 SQL을 별도 파일로 작성해야 한다. 기존 적용 파일을 수정하지 말고 다음 파일을 추가한다.

1. `app/develop`에서 기능 브랜치를 만든다.
2. `db/flyway/sql/V20261125090000000000__add_example_column.sql`처럼
   UTC 날짜·시간·마이크로초를 숫자로 붙인 버전과 `__설명.sql` 이름을 사용한다.
3. SQL에서 `public.테이블명`을 명시한다. 기본 스키마는 이력용 스키마다.
   일반 마이그레이션의 트랜잭션은 Flyway가 관리하므로 `BEGIN; COMMIT;`을 넣지 않는다.
4. 로컬 테스트 후 실제 DB에 적용한다. 실제 적용 전에 자동으로 백업·복원 검증을 수행한다.
5. 적용 성공 후 코드를 `app/develop`에 반영하고 push한다.

```bash
cd /c/Users/dyund/refit-backend
.venv/Scripts/python.exe scripts/test_database_migrations.py
bash scripts/flyway.sh info
bash scripts/flyway.sh migrate
bash scripts/flyway.sh validate
# 작업을 검토·커밋하고 app/develop에 합친 뒤
git push origin app/develop
```

PowerShell에서는 `bash scripts/flyway.sh migrate` 대신
`.\.venv\Scripts\python.exe scripts/database.py migrate`를 사용한다.

`migrate`는 아직 적용되지 않은 SQL만 실행한다. `clean`, `repair`, `undo`는 래퍼에서
허용하지 않고, `cleanDisabled=true`, `baselineOnMigrate=false`를 유지한다.
SQL 에디터에서 따로 적용하면 이력이 어긋나므로 새 변경은 이 경로로 적용한다.
여러 브랜치의 버전 순서가 뒤집혔다면 **미적용 파일만** 새 번호로 조정한다.
동시에 여러 사람이 같은 개발 DB를 변경하지 않도록 적용 순서를 합의한다.

프롬프트 변경 도구 `scripts/prompt_version.py new/activate`도 이 폴더에 Flyway SQL을
생성한다. 이전 `db/migrations/` 파일들은 이미 반영된 역사 자료로 남기며 재실행하지 않는다.

GitHub Actions는 일회성 PostgreSQL에 SQL을 적용해 검사하고, 통과하면 API·Swagger를
자동 배포한다. **실제 Supabase 마이그레이션은 로컬에서 먼저 실행해야 한다.**
GitHub/AWS에는 새 DB 접속 정보를 두지 않으므로 CI는 실제 DB 적용 여부를 확인하거나
대신 적용하지 않는다. DB 변경은 기존 서버 코드와 호환되게 먼저 추가하고, 제거·이름 변경은
호환 코드 배포 이후 별도 마이그레이션으로 나눈다.

## 백업 추가 생성

```bash
bash scripts/flyway.sh backup --label before-change
```

백업은 사용자 홈의 `refit-backups/`에 생성한다. 백업 중 데이터가 바뀌면 내용 검증이
실패하고 마이그레이션은 진행하지 않는다. 이때 쓰기 작업을 멈춘 뒤 다시 실행한다.
백업은 개인정보를 포함하므로 저장소나 채팅에 올리지 않는다. 오류의 상세 내용도
로컬 `tmp/database-error.log`에만 남는다.

## 웹 버전으로 되돌릴 때

`git switch main`만으로 DB가 되돌아가지는 않는다. Flyway baseline도 데이터 복사본이 아니다.
코드 태그와 위의 백업을 함께 사용해야 한다. 과거 백업 복원은 그 이후 DB 데이터를 덮어쓴다.

1. 위 백업 파일들과 SHA-256을 확인한다. 현재 DB도 별도로 백업한다.
2. API·worker 및 외부 작업자의 DB 쓰기를 중지한다. 자동 배포도 일시 중지한다.
3. 로컬 임시 PostgreSQL에 원본 덤프의 `public` 스키마를 복원하고 확인한다.
   현 시점 DB와 예전 백업의 행 수가 다를 수 있으므로 최초 검증과 복구 리허설을 구분한다.
4. 현재 스키마와 관리 스키마 사이의 의존성을 검토한 뒤, 같은 Supabase 프로젝트의
   앱 스키마를 백업으로 복원한다. 전체 관리 스키마를 무작정 덮어쓰지 않는다.
   실제 복구 명령은 당시 변경·의존성을 확인하여 작성한다.
5. Flyway 이력도 복원 대상에 맞춘다. 최초 웹 백업은 baseline 등록 **이전**이므로
   이후 버전 이력을 그대로 남겨서는 안 된다. 앱 개발 재개 시 검증한 DB를 다시 baseline한다.
6. `web-only-before-app-2026-11-24` 코드로 서버 이미지를 빌드·배포하고 API·DB·사진 접근을
   점검한다. 코드 태그에는 DB 비밀번호나 서버 `.env`가 포함되지 않는다.

복원용 `clean`이나 운영 스키마 삭제를 자동화하지 않았다. 현재 데이터는 유지되어 있다.
