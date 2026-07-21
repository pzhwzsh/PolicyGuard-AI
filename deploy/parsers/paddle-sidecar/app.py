import io
import os
from hashlib import sha256

import fitz
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image


app = FastAPI(title="PolicyGuard Paddle Parser Sidecar")
MODE = os.getenv("PARSER_MODE", "paddleocr")
LANG = os.getenv("OCR_LANG", "ch")
_engine = None


def engine():
    global _engine
    if _engine is None:
        if MODE == "ppstructure":
            from paddleocr import PPStructure
            _engine = PPStructure(show_log=False, lang=LANG)
        else:
            from paddleocr import PaddleOCR
            _engine = PaddleOCR(use_angle_cls=True, lang=LANG, show_log=False)
    return _engine


def bbox_tuple(points) -> list[float]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def paddle_blocks(image: np.ndarray, page: int) -> list[dict]:
    result = engine().ocr(image, cls=True)
    lines = result[0] if result and len(result) == 1 else result
    blocks = []
    for index, line in enumerate(lines or []):
        if not line or len(line) < 2:
            continue
        points, recognized = line
        text, confidence = recognized
        blocks.append({
            "block_id": f"p{page}-ocr-{index}", "page": page,
            "block_type": "paragraph", "text": text, "markdown": text,
            "bbox": bbox_tuple(points), "section_path": [],
            "confidence": float(confidence), "metadata": {"engine": "paddleocr"},
        })
    return blocks


def structure_blocks(image: np.ndarray, page: int) -> list[dict]:
    blocks = []
    for index, item in enumerate(engine()(image) or []):
        block_type = str(item.get("type", "paragraph"))
        result = item.get("res") or []
        text = ""
        markdown = ""
        metadata = {"engine": "ppstructure", "raw_type": block_type}
        if block_type == "table" and isinstance(result, dict):
            markdown = str(result.get("html", ""))
            text = " ".join(str(cell.get("text", "")) for cell in result.get("cell_bbox", []))
            metadata["html"] = markdown
        elif isinstance(result, list):
            text = " ".join(str(line.get("text", "")) for line in result if isinstance(line, dict))
            markdown = text
        if not text and not markdown:
            continue
        bbox = item.get("bbox")
        blocks.append({
            "block_id": f"p{page}-structure-{index}", "page": page,
            "block_type": block_type, "text": text, "markdown": markdown or text,
            "bbox": [float(value) for value in bbox] if bbox else None,
            "section_path": [], "confidence": 0.8, "metadata": metadata,
        })
    return blocks


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": MODE, "engine_loaded": _engine is not None}


@app.post("/parse")
async def parse(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="invalid_pdf_magic")
    document = fitz.open(stream=content, filetype="pdf")
    blocks = []
    warnings = []
    for page_index, page in enumerate(document, start=1):
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = np.asarray(Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB"))
        page_blocks = (
            structure_blocks(image, page_index)
            if MODE == "ppstructure"
            else paddle_blocks(image, page_index)
        )
        blocks.extend(page_blocks)
        if not page_blocks:
            warnings.append(f"page_{page_index}:ocr_no_content")
    return {
        "filename": file.filename or "upload.pdf",
        "content_hash": sha256(content).hexdigest(),
        "parser": f"{MODE}_cpu_sidecar_v1",
        "page_count": len(document),
        "status": "review_required" if warnings else "parsed",
        "warnings": warnings,
        "blocks": blocks,
    }
