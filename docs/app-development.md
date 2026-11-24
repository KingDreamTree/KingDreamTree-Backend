# 앱 개발 시작점과 자동 배포

2026-11-24부터 기존 EC2 인스턴스와 `api.refit.live`를 앱 개발용으로 재사용한다.

## 코드 기준점

- 웹 전용 코드: 태그 `web-only-before-app-2026-11-24`
- 기준 커밋: `87421881e3578a822c59d6343ae92b893461b4f4`
- 앱 개발 통합 브랜치: `app/develop`
- `main`은 웹 전용 코드로 남겨 둔다.

태그는 Git에 커밋된 소스의 기준점이다. 로컬의 미추적 `web/architecture-guide/`,
`.env`, DB 데이터, 모델 파일, AWS 설정은 포함하지 않는다.

기능 작업은 `app/develop`에서 새 브랜치를 만들어 진행하고, 완료되면 다시 합친다.

```bash
git switch app/develop
git pull --ff-only
git switch -c feat/app-login
```

전환 전 코드를 별도 브랜치로 꺼내려면, 현재 작업을 먼저 커밋하거나 보관한 뒤:

```bash
git fetch origin --tags
git switch -c restore/web-only web-only-before-app-2026-11-24
```

이 명령은 로컬 코드만 전환한다. 서버나 DB를 과거 상태로 돌리는 명령이 아니다.

## 자동 반영 흐름

`app/develop`에 push → Swagger/OpenAPI 생성 검사 → OpenAPI 파일 보관 →
EC2에서 해당 커밋의 Docker 이미지 빌드 → API/worker 재시작 → 외부 접속 검사.

- 워크플로: `.github/workflows/app-deploy.yml` (`app-deploy`)
- PR은 문서 생성 검사만 실행한다. 서버 배포는 `app/develop` push에서만 실행한다.
- 기존 `deploy.yml` (`main` 배포)은 GitHub Actions에서 **비활성화**한다.
  같은 EC2를 사용하는 두 브랜치가 서로의 배포를 덮어쓰지 않도록 유지해야 한다.
  `gh workflow disable deploy.yml`로 비활성화 상태를 다시 설정할 수 있다.
- `app-deploy`는 `main`에 없으므로 수동 실행 버튼에 의존하지 않는다.
  서버를 켠 뒤 실패한 실행의 **Re-run failed jobs**를 사용하거나 새 커밋을 push한다.
- `EC2_HOST`, `EC2_SSH_KEY`는 기존 GitHub Actions repository secrets를 사용한다.
- 서버 경로는 `~/KingDreamTree-Backend`, SSH 사용자는 `ubuntu`다.
- 배포 서버는 커밋을 직접 체크아웃한다. 서버에서 `git pull`로 수동 갱신하지 않는다.
- Docker의 `BUILD_REVISION`에 커밋 SHA를 넣는다. 서버 `.env`에 같은 변수를
  지정하면 이미지 값을 덮어쓰므로 넣지 않는다.
- Swagger의 버전은 `0.2.0+<커밋 SHA>`다. 배포 검사는 이 SHA까지 일치해야 통과한다.
- `/health`의 HTTP 상태뿐 아니라 `status=ok`, `db=ok`도 검사한다.
- EC2가 꺼져 있어도 문서 생성 검사와 OpenAPI artifact 저장은 가능하다.
  SSH 단계는 실패하며, 이 상태는 배포 완료가 아니다.

## 앱 개발자에게 전달할 주소

- Swagger: https://api.refit.live/docs
- OpenAPI JSON: https://api.refit.live/openapi.json
- API 기본 주소: https://api.refit.live/api/v1
- 상세 연동 가이드: [FRONTEND.md](FRONTEND.md)

주소는 EC2 기동과 배포 성공 후 사용할 수 있다. Swagger의 요청/응답 형식은
FastAPI 라우트와 Pydantic 스키마에서 생성된다. 형식을 변경하면 해당 코드도 함께
수정해야 한다. 업무 로직만 바꾼다고 설명 문구까지 자동 작성되지는 않는다.

서버를 켜기 전에도 GitHub Actions의 `app-deploy` 실행 화면에서
`openapi-<커밋 SHA>` artifact를 다운로드해 API 명세를 볼 수 있다.

## 중지된 EC2 다시 시작하기

1. 기존 EC2 인스턴스를 시작하고 상태 검사 통과를 기다린다.
2. 탄력적 IP 연결과 `api.refit.live` DNS를 확인한다. IP가 달라졌다면
   DNS와 GitHub secret `EC2_HOST`를 새 IP에 맞춘다.
3. 기존 보안 그룹의 SSH(배포용), HTTP/HTTPS 접근과 서버 `.env`를 확인한다.
4. GitHub Actions에서 최신 `app-deploy` 실패 실행을 재실행한다.
5. `deploy` 작업이 성공하고 Swagger에 같은 커밋 SHA가 표시되는지 확인한다.

문서와 API 서버의 기동 검사는 GPU RunPod 워커의 전체 사진 분석까지 검증하지 않는다.
사진 분석 통합 테스트에는 기존 RunPod 등 별도 의존 서비스도 필요하다.

## 로컬 문서 검사

```bash
python scripts/check_api_docs.py --output tmp/openapi.json
```

이 검사는 `/docs`, `/openapi.json`만 읽으며 DB·AI API를 호출하지 않는다.
로컬 `.env`가 Pod 모드라면 기존 `POD_UPLOAD_SECRET` 설정이 필요하거나
검사할 프로세스에서 `PHOTO_PIPELINE=storage`로 지정한다.
