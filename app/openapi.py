"""공통 인증 의존성/예외 핸들러와 Swagger 오류 계약을 맞춘다."""

from fastapi import FastAPI

from app.schemas.auth import AUTH_RESPONSES


def install_error_docs(app: FastAPI) -> None:
    original_openapi = app.openapi

    def openapi() -> dict:
        schema = original_openapi()
        # FastAPI가 중첩 라우터까지 해석하여 생성한 security를 사용한다.
        for path in schema["paths"].values():
            for operation in path.values():
                if not isinstance(operation, dict) or "responses" not in operation:
                    continue
                responses = operation["responses"]
                content = {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/AuthErrorResponse"}
                    }
                }
                if any("RefitBearer" in item for item in operation.get("security", [])):
                    for status, response in AUTH_RESPONSES.items():
                        responses.setdefault(
                            str(status),
                            {
                                "description": response["description"],
                                "content": content,
                            },
                        )
                # RequestValidationError는 실제로 400 + 공통 오류 본문을 반환한다.
                if "422" in responses:
                    responses.pop("422")
                    responses.setdefault(
                        "400", {"description": "요청 형식 오류", "content": content}
                    )
        return schema

    app.openapi = openapi
