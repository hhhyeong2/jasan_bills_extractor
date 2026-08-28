"""
extract/vision_client.py
---------------------------------
VisionExtractor 모듈 (spec.md §3): Claude 비전 API 호출 래퍼.

- 문서유형 분류 + 필드 추출을 한 번의 호출로 처리 (spec.md §1.2)
- 구조화 출력을 위해 tool use(강제 tool_choice)를 사용
- 간단한 재시도 로직 포함 (일시적 오류 대응)
"""

import base64
import os
import time

from .prompts import SYSTEM_PROMPT, build_user_prompt
from .schema import EXTRACT_TOOL_NAME, EXTRACT_TOOL_SCHEMA

DEFAULT_MODEL = os.environ.get("JASAN_MODEL", "claude-sonnet-5")
MAX_RETRIES = 2
RETRY_BACKOFF_SEC = 2


class VisionExtractionError(Exception):
    pass


def _get_client():
    """anthropic SDK 클라이언트를 지연 생성한다.
    (API 키가 없는 환경에서 --dry-run으로 돌릴 때 import 자체가 실패하지 않도록)
    """
    try:
        import anthropic
    except ImportError as e:
        raise VisionExtractionError(
            "anthropic 패키지가 설치되어 있지 않습니다. `pip install anthropic`을 실행하세요."
        ) from e

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise VisionExtractionError(
            "환경변수 ANTHROPIC_API_KEY가 설정되어 있지 않습니다. "
            ".env 파일 또는 셸 환경변수로 설정하세요."
        )
    return anthropic.Anthropic(api_key=api_key)


def extract_from_image(
    png_bytes: bytes,
    filename_hint: str,
    frame_index: int = 0,
    frame_count: int = 1,
    model: str = DEFAULT_MODEL,
) -> dict:
    """이미지 1장을 Claude Vision에 보내 documents 배열을 얻는다.

    반환값: {"documents": [...]}  (schema.py의 EXTRACT_TOOL_SCHEMA 형태)
    """
    client = _get_client()
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    user_prompt = build_user_prompt(filename_hint, frame_index, frame_count)

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=[EXTRACT_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": EXTRACT_TOOL_NAME},
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64,
                                },
                            },
                            {"type": "text", "text": user_prompt},
                        ],
                    }
                ],
            )
            for block in response.content:
                if block.type == "tool_use" and block.name == EXTRACT_TOOL_NAME:
                    return block.input
            raise VisionExtractionError("응답에 tool_use 블록이 없습니다.")
        except VisionExtractionError:
            raise
        except Exception as e:  # noqa: BLE001 - PoC 단계에서는 폭넓게 재시도
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SEC * attempt)
            continue

    raise VisionExtractionError(
        f"{filename_hint} (frame {frame_index}) 추출 실패: {last_err}"
    )
