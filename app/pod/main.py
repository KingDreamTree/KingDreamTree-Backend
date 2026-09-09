"""팟 프로세스 진입점 — 점검 → 모델 예열 → 파이프라인 스레드 → HTTP 서버.

    python -m app.pod.main

⚠️ 하나라도 점검에 걸리면 뜨지 않는다. 문 없는 팟에서는 들어가서 볼 수 없으므로,
   이유를 **기동 로그 첫 줄들**에 남긴다 (RunPod 컨테이너 로그로 밖에서 본다).
"""

from __future__ import annotations

import logging
import sys

from app.config import settings

log = logging.getLogger("pod")


def _preflight() -> list[str]:
    """뜨기 전 확인. 문제 목록을 돌려준다 (비어 있으면 통과)."""
    problems: list[str] = []
    if settings.photo_pipeline != "pod":
        problems.append("PHOTO_PIPELINE 이 'pod' 가 아닙니다 — 이 서버는 팟 경로 전용입니다.")
    if not settings.pod_upload_secret:
        problems.append("POD_UPLOAD_SECRET 이 비어 있습니다 (API 와 같은 값 필요).")
    if not settings.use_mock and not settings.openai_api_key:
        problems.append("OPENAI_API_KEY 가 없습니다 (스크리닝·진단에 필요).")
    try:
        from app.services.db import get_client

        get_client().table("body_part").select("class_name").limit(1).execute()
    except Exception as e:  # noqa: BLE001
        problems.append(f"Supabase 연결 실패: {type(e).__name__}")
    # 가중치가 볼륨에 없으면(볼륨을 새로 만든 경우) 여기서 내려받는다 — 문 없는 팟은
    # 들어가서 받을 수 없다. Network Volume 이면 다음 기동부터는 있다. 1b ≈ 5.5GB.
    if settings.pod_auto_download_weights:
        try:
            from app.services import sapiens_weights

            sapiens_weights.ensure(settings.sapiens_size, settings.model_dir)
        except Exception as e:  # noqa: BLE001
            problems.append(f"가중치 다운로드 실패: {type(e).__name__}: {str(e)[:120]}")
    try:
        from app.worker.handlers import seg  # torch 로드

        seg._preflight()
    except Exception as e:  # noqa: BLE001
        problems.append(f"세그 워커 점검 실패: {e}")
    return problems


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        stream=sys.stdout,
    )
    log.info("팟 기동 — port=%d queue_max=%d", settings.pod_port, settings.pod_queue_max)

    problems = _preflight()
    if problems:
        for p in problems:
            log.error("기동 점검 실패 — %s", p)
        return 1

    # 모델 예열 — 첫 업로드가 콜드 스타트(30초+)를 맞지 않게 지금 올린다.
    from app.services import segmenter

    log.info("모델 예열: %s", segmenter.describe_environment())
    segmenter.load_model()
    log.info("모델 예열 완료")

    from app.pod import pipeline
    from app.pod.server import app

    pipeline.start()

    import uvicorn

    # ⚠️ proxy_headers — RunPod 프록시 뒤에서 client.host 를 실제 IP 로. 없으면 IP당
    #    속도 제한이 프록시 IP 하나로 합쳐져 전체 공용 상한이 된다 (API 의 #169 와 같은 함정).
    # ⚠️ forwarded_allow_ips="*" 는 **RunPod 프록시가 유일한 입구**라는 전제다. 팟 포트를
    #    프록시 밖(TCP 공개 포트)으로 열면 X-Forwarded-For 를 위조해 IP 속도 제한을 피할 수
    #    있다 — 템플릿에서 HTTP 8080 만 노출한다 (docs/pod-pipeline.md).
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.pod_port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
        access_log=False,  # 업로드 경로에 토큰이 찍히지 않게. 접수 로그는 서버가 직접 남긴다
    )
    pipeline.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
