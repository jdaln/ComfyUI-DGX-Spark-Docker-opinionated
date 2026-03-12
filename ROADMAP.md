# Roadmap

## PyTorch Baseline Reminder

Current runtime target:

```text
torch==2.10.0+cu130
```

When staying on this version, remember to rebuild ABI-sensitive wheels after major dependency updates:

- [ ] `flash-attn3`
- [ ] `onnxruntime-gpu`
- [ ] `decord`
- [ ] Refresh `SelfBuiltWheels/` with rebuilt artifacts
- [ ] Rebuild image: `docker compose build --no-cache`

## Quick Commands

```bash
# Rebuild/export flash-attn3 + onnxruntime wheels
./DGX-Spark-WheelsBuilder/export_wheels.sh

# Rebuild full image (also refreshes image-built wheels like decord)
docker compose build --no-cache
```
