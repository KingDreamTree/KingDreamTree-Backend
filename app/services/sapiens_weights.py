"""Sapiens2 세그멘테이션 가중치 — 레포 이름·받을 파일·다운로드.

    sapiens_weights.ensure(size, model_dir) -> 경로   (없으면 내려받고, 있으면 그대로)

scripts/download_sapiens.py(수동) 와 app/pod/main.py(팟 기동 시 자동) 가 같은 함수를 쓴다.

⚠️ 반드시 좁혀서 받는다. 레포에는 같은 가중치가 **두 이름으로** 들어 있다
   (model.safetensors / sapiens2_<size>_seg.safetensors, 내용 동일). `*.safetensors` 로
   받으면 두 배를 받는다. transformers 가 읽는 이름만 받는다.
⚠️ 가중치는 절대 커밋하지 말 것. .gitignore 에 *.safetensors / models/* 가 있다.
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("services.sapiens_weights")

#: 다운로드 재시도 횟수·간격 — 컨테이너 기동 직후 DNS 가 늦게 뜨거나 받는 도중 끊기는 경우
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_RETRY_SEC = 5

#: 크기별 세그멘테이션 체크포인트 레포
REPOS: dict[str, str] = {
    "0.4b": "facebook/sapiens2-seg-0.4b",
    "0.8b": "facebook/sapiens2-seg-0.8b",
    "1b": "facebook/sapiens2-seg-1b",
    "5b": "facebook/sapiens2-seg-5b",
}

ALLOW_PATTERNS: list[str] = [
    "model.safetensors",
    "config.json",
    "preprocessor_config.json",
]


def target_dir(size: str, model_dir: str) -> str:
    return os.path.join(model_dir, f"sapiens2-seg-{size}")


def is_present(size: str, model_dir: str) -> bool:
    return os.path.isfile(os.path.join(target_dir(size, model_dir), "model.safetensors"))


def ensure(size: str, model_dir: str) -> str:
    """가중치가 없으면 내려받는다. 반환: 가중치 폴더 경로.

    팟(문 없는 이미지)은 들어가서 받을 수 없으므로 기동 때 여기서 받는다. Network Volume
    (/workspace) 에 두면 다음 기동부터는 있으니 그대로 지나간다. 1b 는 약 5.5GB 다.
    """
    path = target_dir(size, model_dir)
    if is_present(size, model_dir):
        return path
    from huggingface_hub import snapshot_download  # 이미지에 있음 (requirements-ml)

    os.makedirs(path, exist_ok=True)
    log.warning("가중치 없음 — %s 를 %s 로 내려받습니다 (1b ≈ 5.5GB, 몇 분)", REPOS[size], path)
    # ⚠️ 몇 GB 를 받는 동안 연결이 한 번 끊기면 통째로 실패한다. 받다 만 파일은 이어받으므로
    #    (huggingface_hub 의 .incomplete) 몇 번 더 시도하는 비용은 거의 없다.
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            snapshot_download(repo_id=REPOS[size], local_dir=path, allow_patterns=ALLOW_PATTERNS)
            break
        except Exception as e:  # noqa: BLE001 — 네트워크 계열이 대부분, 종류를 다 열거하지 않는다
            if attempt == DOWNLOAD_ATTEMPTS:
                raise
            log.warning("가중치 다운로드 실패(%d/%d) — %s: %s — %d초 뒤 재시도",
                        attempt, DOWNLOAD_ATTEMPTS, type(e).__name__, str(e)[:120], DOWNLOAD_RETRY_SEC)
            time.sleep(DOWNLOAD_RETRY_SEC)
    if not is_present(size, model_dir):
        raise RuntimeError(f"다운로드 뒤에도 model.safetensors 가 없습니다: {path}")
    log.info("가중치 준비 완료: %s", path)
    return path
