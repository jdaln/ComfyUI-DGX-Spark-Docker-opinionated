#!/usr/bin/env python3
"""Summarise audio files, or the soundtrack of video files, to tell music from noise.

Prints duration, sample rate, channels, mean and peak level, the share of
near-silent 100 ms blocks, spectral flatness and the left/right difference.

Flatness is the geometric over the arithmetic mean of the power spectrum, the
median over 4096-sample frames within 40 dB of the loudest. White noise reads
about 0.5; the music and song outputs here read 1e-3 and below. The left/right
difference is the RMS of L-R over the RMS of the signal: 0 for mono, about 1.4
for two unrelated channels.

Runs in the container, which has PyAV and numpy:

    docker cp scripts/smoke/audio_check.py comfyui:/tmp/
    docker exec comfyui python3 /tmp/audio_check.py /workspace/ComfyUI/output/audio/*.flac
"""
import sys

import av
import numpy as np


def load(path):
    with av.open(path) as container:
        stream = container.streams.audio[0]
        rate, channels = stream.rate, stream.channels
        chunks = []
        for frame in container.decode(stream):
            raw = frame.to_ndarray()
            data = raw.astype(np.float32)
            data = data.T if frame.format.is_planar else data.reshape(-1, channels)
            if np.issubdtype(raw.dtype, np.integer):
                data /= float(np.iinfo(raw.dtype).max)
            chunks.append(data)
    return np.concatenate(chunks), rate, channels


def summarise(path):
    audio, rate, channels = load(path)
    mono = audio.mean(axis=1)
    rms = np.sqrt(np.mean(mono ** 2)) + 1e-12
    window = rate // 10
    blocks = mono[: len(mono) // window * window].reshape(-1, window)
    block_db = 20 * np.log10(np.sqrt(np.mean(blocks ** 2, axis=1)) + 1e-12)
    frames = mono[: len(mono) // 4096 * 4096].reshape(-1, 4096) * np.hanning(4096)
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2 + 1e-12
    loud = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-12) > block_db.max() - 40
    flatness = np.exp(np.mean(np.log(power[loud]), axis=1)) / np.mean(power[loud], axis=1)
    side = np.sqrt(np.mean((audio[:, 0] - audio[:, -1]) ** 2)) / (np.sqrt(np.mean(audio ** 2)) + 1e-12)
    print(f"{path.rsplit('/', 1)[-1]}: {len(mono) / rate:6.1f} s, {rate} Hz, {channels} ch, "
          f"mean {20 * np.log10(rms):6.1f} dBFS, peak {20 * np.log10(np.abs(audio).max() + 1e-12):5.1f} dBFS, "
          f"silent {np.mean(block_db < -60) * 100:4.1f}%, flatness {np.median(flatness):.1e}, "
          f"L-R difference {side:.2f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    for path in sys.argv[1:]:
        summarise(path)
