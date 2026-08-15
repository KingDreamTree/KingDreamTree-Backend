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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
