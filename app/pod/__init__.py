"""GPU 팟 서버 — 사진을 **받아서 메모리에서 끝까지 처리**하고 결과만 남긴다.

    기기 ──두 장 + 토큰──▶ POST /upload (app/pod/server.py)
                              │ 토큰 검증 · 크기 상한 · 스크리닝(즉시 응답)
                              ▼
                         pipeline.submit  (app/pod/pipeline.py)
                              │ 세그 ×2 → 부위 진단 → 종합 진단   (전부 메모리)
                              ▼
                         Supabase: 맵 · 진단문 · 수치 · 크롭 박스   (사진 없음)

기동: python -m app.pod.main   (Dockerfile.pod 의 CMD)

⚠️ 이 프로세스에는 로그인 경로(SSH·터미널)가 없다. 코드 변경은 이미지 배포로만.
   디버깅은 터미널 있는 스테이징 팟에서 — docs/pod-pipeline.md.
"""
