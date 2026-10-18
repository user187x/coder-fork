# The Model Library's security scanner (ghcr.io/user187x/coder-garak): garak, NVIDIA's LLM vulnerability scanner
# (https://github.com/NVIDIA/garak), with everything it needs offline - CPU PyTorch, the Python packages of
# requirements.lock, the detector models and probe datasets garak downloads (fetched once at build by
# `coder-garak warmup`, kept in the image) - and llama.cpp's llama-server, which serves the GGUF model being scanned.
# Build with ../tools/build-garak-image.sh: it adds llama.cpp, checks the image offline and saves it into the bundle.
ARG BASE=docker.io/library/python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libmagic1 \
	&& rm -rf /var/lib/apt/lists/*
COPY requirements.lock /opt/garak/requirements.lock
RUN pip install --no-cache-dir --no-deps --extra-index-url https://download.pytorch.org/whl/cpu \
	-r /opt/garak/requirements.lock && pip check
COPY llama/ /opt/llama/
RUN useradd --uid 1000 --create-home --home-dir /home/garak --shell /usr/sbin/nologin garak \
	&& chown garak:garak /opt/garak && LD_LIBRARY_PATH=/opt/llama /opt/llama/llama-server --version
COPY --chmod=0755 coder-garak /usr/local/bin/coder-garak
COPY profiles.json /opt/garak/profiles.json
ARG BUILT=""
ENV HOME=/home/garak HF_HOME=/home/garak/.cache/huggingface HF_HUB_DISABLE_TELEMETRY=1 PYTHONUNBUFFERED=1 \
	PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false CODER_GARAK_BUILT=${BUILT}
USER 1000
WORKDIR /home/garak
# Every profile's probes and detectors run once against garak's test generator: what they download (detector
# models, datasets, NLTK and WordNet data) stays in the image; /opt/garak/info.json describes it.
RUN coder-garak warmup
# From here on, nothing is downloaded: the scans run offline.
ENV HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 TRANSFORMERS_OFFLINE=1
ENTRYPOINT ["coder-garak"]
CMD ["info"]
