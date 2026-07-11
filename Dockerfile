FROM nvcr.io/nvidia/cuda:13.0.2-devel-ubuntu24.04
ARG DEBIAN_FRONTEND=noninteractive
ARG BUILD_JOBS=8

# Install Python and dependencies (Ubuntu 24.04 has Python 3.12 by default)
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    libopengl0 \
    libcudnn9-dev-cuda-13 \
    cuda-cccl-13-0 \
    && rm -rf /var/lib/apt/lists/*

ENV CUDA_HOME=/usr/local/cuda-13.0
ENV PATH="$CUDA_HOME/bin:${PATH}"
ENV LD_LIBRARY_PATH="$CUDA_HOME/lib64:$CUDA_HOME/targets/sbsa-linux/lib:/usr/lib/aarch64-linux-gnu:${LD_LIBRARY_PATH}"
ENV LIBRARY_PATH="$CUDA_HOME/lib64:$CUDA_HOME/targets/sbsa-linux/lib:/usr/lib/aarch64-linux-gnu:${LIBRARY_PATH}"
ENV TORCH_CUDA_ARCH_LIST="12.1+PTX"
ENV TRITON_PTXAS_PATH="${CUDA_HOME}/bin/ptxas"

# Add CCCL headers path (libcudacxx) so CUTLASS can find cuda/std/* headers
# cuda-cccl package installs to targets/sbsa-linux/include
ENV CPLUS_INCLUDE_PATH="/usr/local/cuda-13.0/targets/sbsa-linux/include:${CPLUS_INCLUDE_PATH}"
ENV C_INCLUDE_PATH="/usr/local/cuda-13.0/targets/sbsa-linux/include:${C_INCLUDE_PATH}"

# Optional prebuilt wheels exported by DGX-Spark-WheelsBuilder
# Use local wheel if compatible with this image (Py3.12/aarch64), else build from source.
COPY DGX-Spark-WheelsBuilder/Wheels/ /opt/prebuilt-wheels/

# Build onnxruntime-gpu from source for CUDA 13.0 only if no compatible prebuilt wheel was found
# Only build the wheel, install happens in entrypoint to use mounted venv
WORKDIR /tmp
RUN mkdir -p /opt/onnxruntime && \
    ONNXRUNTIME_WHEEL="" && \
    mkdir -p /tmp/onnxruntime-validate && \
    for wheel in /opt/prebuilt-wheels/onnxruntime/onnxruntime_gpu-*-cp312-*-linux_aarch64.whl; do \
        [ -e "$wheel" ] || continue; \
        if python3 -m zipfile -t "$wheel" >/dev/null 2>&1 && \
            python3 -m pip install --break-system-packages --no-deps --target /tmp/onnxruntime-validate "$wheel" >/dev/null 2>&1; then \
            ONNXRUNTIME_WHEEL="$wheel"; \
            rm -rf /tmp/onnxruntime-validate/*; \
            break; \
        fi; \
        echo "Ignoring invalid onnxruntime wheel: ${wheel}"; \
        rm -rf /tmp/onnxruntime-validate/*; \
    done && \
    rmdir /tmp/onnxruntime-validate 2>/dev/null || true && \
    if [ -n "${ONNXRUNTIME_WHEEL}" ]; then \
        echo "Using prebuilt onnxruntime wheel: ${ONNXRUNTIME_WHEEL}"; \
        cp "${ONNXRUNTIME_WHEEL}" /opt/onnxruntime/; \
    else \
        echo "No compatible prebuilt onnxruntime wheel found, building from source..."; \
        mkdir -p /tmp/onnxruntime-build && \
        cd /tmp/onnxruntime-build && \
        pip3 install --break-system-packages cmake ninja packaging "numpy>=2.0" && \
        git clone --recursive --depth 1 --branch v1.24.4 --shallow-submodules https://github.com/microsoft/onnxruntime.git && \
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
            --parallel "${BUILD_JOBS}" \
            --nvcc_threads 2 \
            --skip_tests \
            --compile_no_warning_as_error \
            --allow_running_as_root \
            --cmake_generator Ninja \
            --use_binskim_compliant_compile_flags \
            --cmake_extra_defines CMAKE_CUDA_ARCHITECTURES="121" \
                onnxruntime_BUILD_UNIT_TESTS=OFF \
                CMAKE_CXX_STANDARD_INCLUDE_DIRECTORIES="/usr/local/cuda-13.0/targets/sbsa-linux/include" && \
        cp build/cuda13/Release/dist/onnxruntime_gpu-*.whl /opt/onnxruntime/ && \
        cd / && rm -rf /tmp/onnxruntime-build; \
    fi


# =====================================================================
# Build FlashAttention from source for CUDA 13.0 / sm_121 only if no compatible prebuilt wheel was found
# Only build the wheel; installation happens at runtime inside the venv.
# Requires torch with CUDA 13.0 (using official cu130 wheels).
# =====================================================================
WORKDIR /tmp
RUN mkdir -p /opt/flash-attn && \
    FLASH_ATTN_WHEEL="" && \
    mkdir -p /tmp/flash-attn-validate && \
    for wheel in \
        /opt/prebuilt-wheels/flash-attn/flash_attn-*-cp312-*-linux_aarch64.whl \
        /opt/prebuilt-wheels/flash-attn3/flash_attn_3-*-abi3-linux_aarch64.whl \
        /opt/prebuilt-wheels/flash-attn3/flash_attn_3-*-cp312-*-linux_aarch64.whl; do \
        [ -e "$wheel" ] || continue; \
        if python3 -m zipfile -t "$wheel" >/dev/null 2>&1 && \
            python3 -m pip install --break-system-packages --no-deps --target /tmp/flash-attn-validate "$wheel" >/dev/null 2>&1; then \
            FLASH_ATTN_WHEEL="$wheel"; \
            rm -rf /tmp/flash-attn-validate/*; \
            break; \
        fi; \
        echo "Ignoring invalid flash-attn wheel: ${wheel}"; \
        rm -rf /tmp/flash-attn-validate/*; \
    done && \
    rmdir /tmp/flash-attn-validate 2>/dev/null || true && \
    if [ -n "${FLASH_ATTN_WHEEL}" ]; then \
        echo "Using prebuilt flash-attn wheel: ${FLASH_ATTN_WHEEL}"; \
        cp "${FLASH_ATTN_WHEEL}" /opt/flash-attn/; \
    else \
        echo "No compatible prebuilt flash-attn wheel found, building from source..."; \
        mkdir -p /tmp/flash-attn-build && \
        cd /tmp/flash-attn-build && \
        pip3 install --break-system-packages "packaging" "ninja" && \
        pip3 install --break-system-packages \
            "torch==2.10.0+cu130" \
            --index-url https://download.pytorch.org/whl/cu130 && \
        git clone --recursive --depth 1 --shallow-submodules https://github.com/Dao-AILab/flash-attention.git && \
        cd flash-attention && \
        export CUDA_HOME=/usr/local/cuda-13.0 && \
        export MAX_JOBS="${BUILD_JOBS}" && \
        export CMAKE_BUILD_PARALLEL_LEVEL="${BUILD_JOBS}" && \
        export NINJA_NUM_JOBS="${BUILD_JOBS}" && \
        export MAKEFLAGS="-j${BUILD_JOBS}" && \
        export NVCC_THREADS=2 && \
        export CMAKE_GENERATOR=Ninja && \
        export TORCH_CUDA_ARCH_LIST="12.1+PTX" && \
        python3 -m pip wheel . --no-deps -w dist && \
        cp dist/*.whl /opt/flash-attn/ && \
        cd / && rm -rf /tmp/flash-attn-build; \
    fi



# =====================================================================
# Build SageAttention from source for CUDA 13.0 / sm_121.
# Install happens at runtime from the local pre-built wheel only.
# =====================================================================
ARG SAGEATTN_REF=main
WORKDIR /tmp
RUN mkdir -p /opt/sageattention && \
    SAGEATTN_WHEEL="" && \
    mkdir -p /tmp/sageattention-validate && \
    for wheel in /opt/prebuilt-wheels/sageattention/sageattention-*-cp312-*-linux_aarch64.whl; do \
        [ -e "$wheel" ] || continue; \
        if python3 -m zipfile -t "$wheel" >/dev/null 2>&1 && \
            python3 -m pip install --break-system-packages --no-deps --target /tmp/sageattention-validate "$wheel" >/dev/null 2>&1; then \
            SAGEATTN_WHEEL="$wheel"; \
            rm -rf /tmp/sageattention-validate/*; \
            break; \
        fi; \
        echo "Ignoring invalid SageAttention wheel: ${wheel}"; \
        rm -rf /tmp/sageattention-validate/*; \
    done && \
    rmdir /tmp/sageattention-validate 2>/dev/null || true && \
    if [ -n "${SAGEATTN_WHEEL}" ]; then \
        echo "Using prebuilt SageAttention wheel: ${SAGEATTN_WHEEL}"; \
        cp "${SAGEATTN_WHEEL}" /opt/sageattention/; \
    else \
        echo "No compatible prebuilt SageAttention wheel found, building from source..."; \
        mkdir -p /tmp/sageattention-build && \
        cd /tmp/sageattention-build && \
        pip3 install --break-system-packages \
            packaging \
            "torch==2.10.0+cu130" \
            --index-url https://download.pytorch.org/whl/cu130 && \
        git clone --depth 1 --branch ${SAGEATTN_REF} https://github.com/thu-ml/SageAttention.git && \
        cd SageAttention && \
        export CUDA_HOME=/usr/local/cuda-13.0 && \
        export TORCH_CUDA_ARCH_LIST="12.1+PTX" && \
        export TRITON_PTXAS_PATH="${CUDA_HOME}/bin/ptxas" && \
        export MAX_JOBS="${BUILD_JOBS}" && \
        export CMAKE_BUILD_PARALLEL_LEVEL="${BUILD_JOBS}" && \
        export NINJA_NUM_JOBS="${BUILD_JOBS}" && \
        python3 -m pip wheel . --no-build-isolation --no-deps -w dist && \
        cp dist/sageattention-*.whl /opt/sageattention/ && \
        cd / && rm -rf /tmp/sageattention-build; \
    fi

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

# =====================================================================
# Build comfy-aimdo wheel with native aimdo.so for Linux aarch64/CUDA 13.0
# (PyPI currently provides a stub wheel for this platform without aimdo.so)
# =====================================================================
ARG COMFY_AIMDO_VERSION=v0.2.12
WORKDIR /tmp
RUN mkdir -p /opt/comfy-aimdo && \
    mkdir -p /tmp/comfy-aimdo-build && \
    cd /tmp/comfy-aimdo-build && \
    git clone --depth 1 --branch ${COMFY_AIMDO_VERSION} https://github.com/Comfy-Org/comfy-aimdo.git && \
    cd comfy-aimdo && \
    gcc -shared -fPIC -O2 -g src/*.c src-posix/*.c \
        -Isrc \
        -I/usr/local/cuda-13.0/include \
        -L/usr/local/cuda-13.0/targets/sbsa-linux/lib/stubs \
        -lcuda \
        -o comfy_aimdo/aimdo.so && \
    pip3 install --break-system-packages build && \
    python3 -m pip wheel . --no-deps -w dist && \
    cp dist/comfy_aimdo-*.whl /opt/comfy-aimdo/ && \
    cd / && rm -rf /tmp/comfy-aimdo-build


# Venv will be created at runtime in mounted volume
ENV VENV_PATH="/workspace/venv"
ENV PATH="${VENV_PATH}/bin:${PATH}"

WORKDIR /workspace

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
# Keep this copy near the end to avoid rebuilding heavy layers when cutter changes.
COPY ["models-cutter", "/workspace/models-cutter"]

ENTRYPOINT ["/entrypoint.sh"]
LABEL authors="dr-vij"
