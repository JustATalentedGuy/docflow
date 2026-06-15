FROM python:3.11-slim

ARG PRELOAD_EMBEDDING_MODEL=true

# System libraries:
#   tesseract-ocr   — OCR engine (replaces PaddleOCR)
#   poppler-utils   — needed by pytesseract for PDF page info
#   libgomp1        — OpenMP, needed by numpy / sentence-transformers
#   libglib2.0-0    — glib, needed by Pillow TIFF support
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libgomp1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache layer: install Python deps before copying source
# (only re-runs when requirements.txt changes)
ENV HF_HOME=/app/.hf_cache
ENV TRANSFORMERS_CACHE=/app/.hf_cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download sentence-transformers model weights at build time.
# (~90 MB) — avoids a cold-start delay on first /query request.
RUN if [ "$PRELOAD_EMBEDDING_MODEL" = "true" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2'); print('sentence-transformers model ready')"; \
    else \
      echo "Skipping embedding model preload"; \
    fi

# Copy application source
COPY app/ ./app/
COPY scripts/ ./scripts/

EXPOSE 8000
