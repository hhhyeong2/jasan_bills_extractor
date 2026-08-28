"""
preprocess/image_prep.py
---------------------------------
Preprocessor 모듈 (spec.md §3): TIFF 프레임 분리, 화질 보정(디노이즈/업스케일/대비),
API 전송용 PNG 바이트 인코딩.

디스큐(기울기 보정)는 PoC 범위에서는 생략하고 TODO로 남겨둔다 (필요성은
Phase 2에서 실제 샘플로 재평가).
"""

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps

# 참고용 상수: Claude Vision의 표준 해상도 상한(긴 변 기준). 이보다 큰 이미지는
# API 쪽에서 자동 다운스케일되므로 여기서 직접 사용하지는 않는다. (spec.md §1.4)
MAX_LONG_EDGE = 1568


@dataclass
class PreppedImage:
    png_bytes: bytes
    width: int
    height: int
    frame_index: int
    frame_count: int


def load_tiff_frames(path: str) -> list[Image.Image]:
    """멀티프레임 TIFF를 프레임별 PIL 이미지 리스트로 분리."""
    frames = []
    with Image.open(path) as im:
        try:
            i = 0
            while True:
                im.seek(i)
                frames.append(im.copy())
                i += 1
        except EOFError:
            pass
    return frames


def enhance_fax_image(img: Image.Image, denoise: bool = False) -> Image.Image:
    """팩스 화질을 보정한다.

    실측 메모(Phase 1 PoC 중 발견, 2026-08): 원본이 이미 1-bit 이진화된 팩스본인
    경우, MedianFilter는 halftone 노이즈뿐 아니라 글자 획 경계까지 뭉개서 오히려
    가독성을 떨어뜨리는 경우가 있었다. 그래서 기본값은 denoise=False로 바꾸고,
    실제 추출 정확도를 보면서(Phase 1) denoise=True와 비교 테스트할 것을 권장한다.

    또한 Claude Vision은 모델 해상도 한도를 넘는 이미지를 API 쪽에서 자동
    다운스케일하므로(공식 문서 확인, spec.md §1.4), 여기서 별도로 다운스케일하지
    않는다 — 미리 축소하면 이미 저해상도인 팩스본의 정보 손실만 커진다.

    1) 그레이스케일 변환
    2) (선택) 미디언 필터로 디더링 노이즈 제거
    3) 오토 컨트라스트로 대비 강화
    4) 원본이 작으면 업스케일(가독성 향상). 큰 이미지는 그대로 둔다.
    """
    gray = img.convert("L")
    if denoise:
        gray = gray.filter(ImageFilter.MedianFilter(size=3))
    contrasted = ImageOps.autocontrast(gray, cutoff=1)

    w, h = contrasted.size
    long_edge = max(w, h)

    if long_edge < 1200:
        # 저해상도 팩스본은 업스케일해서 문자 획을 더 뚜렷하게 만든다.
        scale = 1200 / long_edge
        new_size = (int(w * scale), int(h * scale))
        contrasted = contrasted.resize(new_size, Image.LANCZOS)

    return contrasted


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def prep_file(path: str, denoise: bool = False) -> list[PreppedImage]:
    """TIFF 파일 하나를 프레임 단위로 분리 + 보정 + PNG 인코딩까지 수행."""
    frames = load_tiff_frames(path)
    prepped = []
    for idx, frame in enumerate(frames):
        enhanced = enhance_fax_image(frame, denoise=denoise)
        png_bytes = to_png_bytes(enhanced)
        prepped.append(
            PreppedImage(
                png_bytes=png_bytes,
                width=enhanced.width,
                height=enhanced.height,
                frame_index=idx,
                frame_count=len(frames),
            )
        )
    return prepped
