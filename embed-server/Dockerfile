# EmbeddingGemma 2 on CPU for Luna's search. Python and package versions as in the evaluated environment of the
# retrieval study (README.md); torch is the CPU build of the same release.
FROM python:3.10-slim
WORKDIR /srv
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
      torch==2.6.0+cpu torchvision==0.21.0+cpu \
      sentence-transformers==6.1.0 transformers==5.20.1 tokenizers==0.23.3 safetensors==0.8.0 \
      pillow==12.3.0 numpy==2.2.6 huggingface_hub==1.33.0
# the model at its pinned revision, baked into the image: no download at start, no token needed (Apache 2.0)
ARG MODEL_REVISION=914f7f89142e33e77833254d9c9b90c3cef7303b
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('google/embeddinggemma-2', revision='${MODEL_REVISION}', local_dir='/model')"
COPY server.py .
ENV PYTHONUNBUFFERED=1 HOME=/tmp MODEL_DIR=/model HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false
USER nobody
CMD ["python", "server.py"]
