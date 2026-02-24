FROM nvcr.io/nvidia/cuda:13.0.2-devel-ubuntu24.04
ARG DEBIAN_FRONTEND=noninteractive

# Install Python and dependencies (Ubuntu 24.04 has Python 3.12 by default)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    curl \
    git \
    ninja-build \
    cmake \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libcudnn9-dev-cuda-13 \
    cuda-cccl-13-0 \
    && rm -rf /var/lib/apt/lists/*

ENV CUDA_HOME=/usr/local/cuda-13.0
ENV PATH="$CUDA_HOME/bin:${PATH}"
ENV LD_LIBRARY_PATH="$CUDA_HOME/lib64:$CUDA_HOME/targets/sbsa-linux/lib:/usr/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH}"
ENV LIBRARY_PATH="$CUDA_HOME/lib64:$CUDA_HOME/targets/sbsa-linux/lib:/usr/lib/aarch64-linux-gnu:${LIBRARY_PATH}"
ENV TORCH_CUDA_ARCH_LIST="12.1a"
ENV TRITON_PTXAS_PATH="${CUDA_HOME}/bin/ptxas"

# Add CCCL headers path (libcudacxx) so CUTLASS can find cuda/std/* headers
# cuda-cccl package installs to targets/sbsa-linux/include
ENV CPLUS_INCLUDE_PATH="/usr/local/cuda-13.0/targets/sbsa-linux/include:${CPLUS_INCLUDE_PATH}"
ENV C_INCLUDE_PATH="/usr/local/cuda-13.0/targets/sbsa-linux/include:${C_INCLUDE_PATH}"

# Build onnxruntime-gpu from source for CUDA 13.0
# Only build the wheel, install happens in entrypoint to use mounted venv
WORKDIR /tmp/onnxruntime-build
RUN pip3 install --break-system-packages cmake ninja packaging "numpy>=2.0" && \
    git clone --recursive --depth 1 https://github.com/microsoft/onnxruntime.git && \
    cd onnxruntime && \
    export CXXFLAGS="-I/usr/local/cuda-13.0/targets/sbsa-linux/include $CXXFLAGS" && \
    export CFLAGS="-I/usr/local/cuda-13.0/targets/sbsa-linux/include $CFLAGS" && \
    ./build.sh --config Release \
        --build_dir build/cuda13 \
        --build_wheel \
        --use_cuda \
        --cuda_home /usr/local/cuda-13.0 \
        --cudnn_home /usr/local/cuda-13.0 \
        --cuda_version 13.0 \
        --parallel 6 \
        --nvcc_threads 1 \
        --skip_tests \
        --compile_no_warning_as_error \
        --allow_running_as_root \
        --cmake_generator Ninja \
        --use_binskim_compliant_compile_flags \
        --cmake_extra_defines CMAKE_CUDA_ARCHITECTURES="121" \
            onnxruntime_BUILD_UNIT_TESTS=OFF \
            CMAKE_CXX_STANDARD_INCLUDE_DIRECTORIES="/usr/local/cuda-13.0/targets/sbsa-linux/include" && \
    mkdir -p /opt/onnxruntime && \
    cp build/cuda13/Release/dist/onnxruntime_gpu-*.whl /opt/onnxruntime/ && \
    cd / && rm -rf /tmp/onnxruntime-build


# =====================================================================
# Build FlashAttention-3 (Hopper beta) from source for CUDA 13.0 / sm_121
# Only build the wheel; installation happens at runtime inside the venv.
# Requires torch with CUDA 13.0 (using official cu130 wheels). [oai_citation:1‡GitHub](https://github.com/Dao-AILab/flash-attention)
# =====================================================================
WORKDIR /tmp/flash-attn-build
RUN pip3 install --break-system-packages "packaging" "ninja" && \
    pip3 install --break-system-packages \
        "torch==2.9.1+cu130" \
        --index-url https://download.pytorch.org/whl/cu130 && \
    git clone --recursive https://github.com/Dao-AILab/flash-attention.git && \
    cd flash-attention && \
    git submodule update --init --recursive && \
    cd hopper && \
    export CUDA_HOME=/usr/local/cuda-13.0 && \
    export MAX_JOBS=8 && \
    export TORCH_CUDA_ARCH_LIST="12.1a" && \
    python3 -m pip wheel . --no-deps -w dist && \
    mkdir -p /opt/flash-attn3 && \
    cp dist/*.whl /opt/flash-attn3/ && \
    cd / && rm -rf /tmp/flash-attn-build

# =====================================================================
# Build decord from source (Python 3.12 / Ubuntu 24.04)
# =====================================================================
WORKDIR /tmp/decord-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        pkg-config \
        libavcodec-dev libavformat-dev libavfilter-dev libavutil-dev libswscale-dev \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    \
    # Clone decord fork and checkout dgx-spark branch (with FFmpeg 7 fixes)
    && git clone --recursive https://github.com/dr-vij/decord.git \
    && cd decord \
    && git checkout dgx-spark \
    && git submodule update --init --recursive \
    \
    # Provide stub libnvcuvid.so for link stage (must export real cuvid symbols
    # so the linker adds libnvcuvid.so to DT_NEEDED; at runtime the real
    # libnvcuvid.so from the host driver replaces this stub)
    && printf '%s\n' \
        'void cuvidCreateDecoder(){}' \
        'void cuvidDestroyDecoder(){}' \
        'void cuvidDecodePicture(){}' \
        'void cuvidGetDecoderCaps(){}' \
        'void cuvidMapVideoFrame64(){}' \
        'void cuvidUnmapVideoFrame64(){}' \
        'void cuvidCreateVideoParser(){}' \
        'void cuvidDestroyVideoParser(){}' \
        'void cuvidParseVideoData(){}' \
        'void cuvidCreateVideoSource(){}' \
        'void cuvidDestroyVideoSource(){}' \
        'void cuvidCreateVideoSourceW(){}' \
        'void cuvidGetVideoSourceState(){}' \
        'void cuvidSetVideoSourceState(){}' \
        'void cuvidGetSourceVideoFormat(){}' \
        'void cuvidGetSourceAudioFormat(){}' \
        'void cuvidCtxLockCreate(){}' \
        'void cuvidCtxLockDestroy(){}' \
        'void cuvidCtxLock(){}' \
        'void cuvidCtxUnlock(){}' \
        | gcc -shared \
            -o /usr/local/cuda-13.0/lib64/stubs/libnvcuvid.so \
            -x c - -Wl,-soname,libnvcuvid.so.1 \
    \
    && mkdir -p build && cd build \
    && cmake .. -G Ninja \
        -DUSE_CUDA=ON \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CUDA_ARCHITECTURES=121 \
        -DCMAKE_LIBRARY_PATH=/usr/local/cuda-13.0/lib64/stubs \
        -DCMAKE_CXX_FLAGS="-Wno-deprecated-declarations" \
        -DCMAKE_C_FLAGS="-Wno-deprecated-declarations" \
    && ninja -j"$(nproc)" \
    \
    && cd ../python \
    && python3 -m pip wheel . --no-deps -w dist \
    && mkdir -p /opt/decord \
    && cp dist/*.whl /opt/decord/ \
    && cd / \
    && rm -rf /tmp/decord-build


RUN apt-get update && apt-get install -y --no-install-recommends libopengl0 && rm -rf /var/lib/apt/lists/*

# Venv will be created at runtime in mounted volume
ENV VENV_PATH="/workspace/venv"
ENV PATH="${VENV_PATH}/bin:${PATH}"

WORKDIR /workspace

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
LABEL authors="dr-vij"
