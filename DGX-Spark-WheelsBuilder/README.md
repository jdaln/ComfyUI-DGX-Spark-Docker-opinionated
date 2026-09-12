# DGX Spark wheels builder

Builds the GPU wheels that have no upstream release for CUDA 13.0 on Python 3.12
and arm64, and exports them to disk. It runs no ComfyUI and no entrypoint.

The main `Dockerfile` copies `Wheels/` into the image and prefers those files
over building from source, so what this produces is what the container installs.

## Build

```bash
./export_wheels.sh
```

Output:

```text
DGX-Spark-WheelsBuilder/Wheels/
  flash-attn/*.whl
  flash-attn3/*.whl
  onnxruntime/*.whl
  sageattention/*.whl
```

Without the script:

```bash
docker buildx build -t dgx-spark-wheelsbuilder \
  -o type=local,dest=DGX-Spark-WheelsBuilder/Wheels \
  DGX-Spark-WheelsBuilder
```

## Notes

- Requires Docker with `buildx`, which recent Docker enables by default.
- Change the image tag with `IMAGE_TAG=my-tag ./export_wheels.sh`.
- Wheels are tracked in git-lfs. Commit refreshed builds so a fresh clone does
  not fall back to compiling from source.
- Rebuild these after changing the torch pin. See
  [`docs/maintenance.md`](../docs/maintenance.md).
