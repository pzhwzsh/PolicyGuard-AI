import io
from hashlib import sha256

import fitz
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


app = FastAPI(title="PolicyGuard RapidOCR PDF Sidecar")
_engine = None


def engine() -> RapidOCR:
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "engine": "rapidocr_onnxruntime", "loaded": _engine is not None}


@app.post("/parse")
async def parse(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="invalid_pdf_magic")
    document = fitz.open(stream=content, filetype="pdf")
    blocks = []
    warnings = []
    for page_number, page in enumerate(document, start=1):
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = np.asarray(Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB"))
        result, _ = engine()(image)
        page_blocks = []
        for index, line in enumerate(result or []):
            points, text, confidence = line
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            page_blocks.append({
                "block_id": f"p{page_number}-rapidocr-{index}",
                "page": page_number, "block_type": "paragraph",
                "text": str(text), "markdown": str(text),
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "section_path": [], "confidence": float(confidence),
                "metadata": {"engine": "rapidocr_onnxruntime"},
            })
        blocks.extend(page_blocks)
        if not page_blocks:
            warnings.append(f"page_{page_number}:ocr_no_content")
    return {
        "filename": file.filename or "upload.pdf",
        "content_hash": sha256(content).hexdigest(),
        "parser": "rapidocr_onnxruntime_cpu_v1",
        "page_count": len(document),
        "status": "review_required" if warnings else "parsed",
        "warnings": warnings,
        "blocks": blocks,
    }
