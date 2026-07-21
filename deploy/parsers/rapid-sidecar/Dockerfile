FROM taizhang-rapidocr:latest

WORKDIR /app
RUN pip install --no-cache-dir PyMuPDF==1.24.10
COPY app.py /app/app.py
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
