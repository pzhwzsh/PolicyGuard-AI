# PDF parser sidecars

CPU PaddleOCR and PP-Structure services implement PolicyGuard's canonical `POST /parse` contract.
An additional RapidOCR/ONNX CPU sidecar reuses the locally available model image and provides a lighter scanned-page fallback.

```powershell
docker compose -f deploy/parsers/compose.yml build
docker compose -f deploy/parsers/compose.yml up -d
```

Then configure:

```env
PADDLEOCR_BASE_URL=http://127.0.0.1:9011
PPSTRUCTURE_BASE_URL=http://127.0.0.1:9012
RAPIDOCR_BASE_URL=http://127.0.0.1:9013
```

The first image build and first OCR request download large CPU/model dependencies. This repository does not claim OCR quality until the labeled PDF suite is populated and `python -m policyguard.scripts.evaluate_documents` is run. MinerU remains an external optional service configured through `MINERU_BASE_URL`; it is intentionally isolated because its PyTorch/runtime requirements conflict with the lightweight API environment on this machine.
