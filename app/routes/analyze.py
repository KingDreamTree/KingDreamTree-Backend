from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.analyze import AnalyzeResponse
from app.services import db, segmenter, storage

router = APIRouter(prefix="/analyze", tags=["analyze"])

_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20MB


@router.post(
    "",
    response_model=AnalyzeResponse,
    summary="이미지 세그멘테이션",
    description="사용자 이미지와 레퍼런스 이미지를 업로드하면 세그멘테이션 결과를 반환합니다.",
)
async def analyze(
    user_image: UploadFile = File(..., description="사용자 신체 이미지 (JPEG/PNG/WebP/HEIC)"),
    ref_image: UploadFile = File(..., description="레퍼런스 신체 이미지 (JPEG/PNG/WebP/HEIC)"),
) -> AnalyzeResponse:
    user_bytes = await user_image.read()
    ref_bytes = await ref_image.read()

    if len(user_bytes) > _MAX_IMAGE_BYTES or len(ref_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="이미지 크기는 20MB 이하여야 합니다.")

    user_bytes = segmenter.preprocess_image(user_bytes)
    ref_bytes = segmenter.preprocess_image(ref_bytes)

    user_url = await storage.upload_image("images", f"user/{user_image.filename}", user_bytes)
    ref_url = await storage.upload_image("images", f"ref/{ref_image.filename}", ref_bytes)

    analysis_id = await db.create_analysis(user_url, ref_url)

    user_seg = await segmenter.segment(user_bytes)
    ref_seg = await segmenter.segment(ref_bytes)

    return AnalyzeResponse(
        analysis_id=analysis_id,
        user_seg=user_seg,  # type: ignore[arg-type]
        ref_seg=ref_seg,  # type: ignore[arg-type]
    )
