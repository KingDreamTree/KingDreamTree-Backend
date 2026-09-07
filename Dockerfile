# 배포 이미지 — API 서버와 LLM 워커가 **같은 이미지**를 쓴다.
# (docker-compose.yml 에서 command 만 다르게 준다)
#
# ⚠️ torch / transformers 를 넣지 않는다. EC2 는 세그멘테이션을 돌리지 않는다 —
#    세그는 RunPod 워커 담당이고, app/worker/run.py 가 --kinds 에 따라
#    seg 핸들러 모듈 자체를 import 하지 않으므로 torch 없이 안전하게 뜬다.
#    넣으면 이미지가 ~500MB → ~3GB 가 된다. (scripts/install_cpu.sh 가 EC2 에
#    torch 를 깔던 건 도커 이전 방식의 "혹시나"였다)
#
# ⚠️ .env 를 이미지에 굽지 않는다 (.dockerignore 로 차단). 시크릿은
#    compose 의 env_file 로 실행 시점에 주입한다.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# 의존성 먼저 — 코드만 바뀐 재빌드에서 이 레이어가 캐시로 넘어간다.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app app
COPY data data
COPY scripts scripts

EXPOSE 8000

# 기본 명령은 API. 워커 컨테이너는 compose 에서 command 로 덮어쓴다.
# ⚠️ --proxy-headers (#169): Caddy(reverse_proxy) 뒤에서 X-Forwarded-For 를 읽어
#    request.client 를 실제 클라이언트 IP 로 바꾼다. 없으면 모든 요청의 client.host 가
#    caddy 컨테이너 IP 라, IP당 레이트리밋(#115, POST /users)이 **전체 공용 상한**이 된다 —
#    홈 진입마다 새 user_id 를 받는 방침에서는 시연 중 열 번째 «시작»부터 429 가 난다.
#    api 는 포트를 공개하지 않아(compose 참고) caddy 만 붙으므로 allow-ips=* 가 안전하다.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
