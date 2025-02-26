FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_PREFER_BINARY=1 \
    PYTHONUNBUFFERED=1 \
    CMAKE_BUILD_PARALLEL_LEVEL=8

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.10 python3-pip git wget libgl1 git-lfs libglib2.0-0 \
    python3-dev build-essential gcc \
    && ln -sf /usr/bin/python3.10 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install --no-cache-dir \
    comfy-cli runpod requests numpy insightface==0.7.3

# Install ComfyUI
RUN /usr/bin/yes | comfy --workspace /comfyui install \
    --cuda-version 11.8 --nvidia --version 0.3.12

WORKDIR /comfyui

# Copy configuration
COPY src/extra_model_paths.yaml /comfyui/extra_model_paths.yaml

# Download models
RUN git lfs install \
    && cd /comfyui/models \
    && git clone https://huggingface.co/Aitrepreneur/insightface \
    && mkdir -p pulid \
    && wget -O pulid/pulid_flux_v0.9.0.safetensors https://huggingface.co/Aitrepreneur/FLX/resolve/main/pulid_flux_v0.9.0.safetensors?download=true \
    && mkdir -p facexlib \
    && wget -O facexlib/detection_Resnet50_Final.pth https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth \
    && wget -O facexlib/parsing_parsenet.pth https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth \
    && wget -O facexlib/parsing_bisenet.pth https://github.com/xinntao/facexlib/releases/download/v0.2.0/parsing_bisenet.pth \
    && mkdir -p custom_nodes

# Clone and setup custom nodes
RUN cd /comfyui/custom_nodes && \
    for repo in \
    https://github.com/giriss/comfy-image-saver.git \
    https://github.com/lldacing/ComfyUI_PuLID_Flux_ll.git \
    https://github.com/rgthree/rgthree-comfy.git \
    https://github.com/glowcone/comfyui-base64-to-image.git; \
    do \
        repo_dir=$(basename "$repo" .git); \
        git clone "$repo" "$repo_dir"; \
        if [ -f "$repo_dir/requirements.txt" ]; then \
            pip install -r "$repo_dir/requirements.txt"; \
        fi; \
        if [ -f "$repo_dir/install.py" ]; then \
            python "$repo_dir/install.py"; \
        fi; \
    done

COPY src/start.sh src/restore_snapshot.sh src/rp_handler.py /
RUN chmod +x /start.sh

CMD ["/start.sh"]