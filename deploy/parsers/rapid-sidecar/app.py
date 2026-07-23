import base64
import io
import tempfile
from hashlib import sha256
from pathlib import Path

import fitz
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel
from rapidocr_onnxruntime import RapidOCR

app = FastAPI(title="PolicyGuard RapidOCR PDF Sidecar")
_engine = None


class MediaRequest(BaseModel):
    filename: str
    content_base64: str


def engine() -> RapidOCR:
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


def ocr_frame(image: np.ndarray, frame_index: int, timestamp_seconds: float) -> dict:
    result, _ = engine()(image)
    lines = []
    for line in result or []:
        points, text, confidence = line
        lines.append({
            "text": str(text),
            "confidence": float(confidence),
            "bbox": [[float(point[0]), float(point[1])] for point in points],
        })
    return {
        "frame_index": frame_index,
        "timestamp_seconds": round(timestamp_seconds, 3),
        "lines": lines,
        "text": "\n".join(item["text"] for item in lines),
    }


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


@app.post("/parse-media")
def parse_media(payload: MediaRequest) -> dict:
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="media_base64_invalid") from exc
    suffix = Path(payload.filename).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg"}:
        try:
            image = np.asarray(Image.open(io.BytesIO(content)).convert("RGB"))
        except Exception as exc:
            raise HTTPException(status_code=422, detail="image_decode_failed") from exc
        frames = [ocr_frame(image, 0, 0.0)]
    elif suffix in {".mp4", ".webm"}:
        import cv2

        temporary = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            temporary.write(content)
            temporary.close()
            capture = cv2.VideoCapture(temporary.name)
            fps = max(float(capture.get(cv2.CAP_PROP_FPS) or 0), 1.0)
            interval = max(int(fps * 2), 1)
            frames = []
            index = 0
            while len(frames) < 30:
                ok, frame = capture.read()
                if not ok:
                    break
                if index % interval == 0:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    frames.append(ocr_frame(rgb, index, index / fps))
                index += 1
            capture.release()
        finally:
            Path(temporary.name).unlink(missing_ok=True)
    else:
        raise HTTPException(status_code=415, detail="media_type_not_supported")
    return {
        "filename": payload.filename,
        "content_hash": sha256(content).hexdigest(),
        "frames": frames,
        "warnings": [] if frames else ["media_no_frames"],
        "sampling": "image_once_or_video_every_2_seconds_max_30",
    }
