FROM nvcr.io/nvidia/cuda:13.0.2-devel-ubuntu24.04 AS builder
ARG DEBIAN_FRONTEND=noninteractive

# Install Python and build dependencies (Ubuntu 24.04 has Python 3.12 by default)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
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
WORKDIR /tmp/onnxruntime-build
RUN pip3 install --break-system-packages cmake ninja packaging "numpy>=2.0" && \
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
    mkdir -p /wheels/onnxruntime && \
    cp build/cuda13/Release/dist/onnxruntime_gpu-*.whl /wheels/onnxruntime/ && \
    cd / && rm -rf /tmp/onnxruntime-build

# Build FlashAttention-3 (Hopper beta) from source for CUDA 13.0 / sm_121
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
    mkdir -p /wheels/flash-attn3 && \
    cp dist/*.whl /wheels/flash-attn3/ && \
    cd / && rm -rf /tmp/flash-attn-build

FROM scratch
COPY --from=builder /wheels/ /
LABEL authors="dr-vij"
