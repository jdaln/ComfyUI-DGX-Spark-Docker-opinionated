#!/bin/bash
set -euo pipefail

# Source:
# https://github.com/mmartial/ComfyUI-Nvidia-Docker/blob/main/extras/dgx_spark-helper.sh
# Adapted from instructions seen on
# https://forums.developer.nvidia.com/t/unlocking-the-power-of-the-spark-in-comfyui-no-crashes/360336

# Spark clock cap (disabled by default)
# CLOCK_MIN_MHZ="${SPARK_CLOCK_MIN_MHZ:-300}"
# CLOCK_MAX_MHZ="${SPARK_CLOCK_MAX_MHZ:-2100}"

## Fix 1: Disable Swap (Critical)
sudo swapoff -a

## Fix 2: GPU stability (+ optional clock cap)
sudo nvidia-smi -pm 1
# Uncomment to cap clocks if you need extra stability:
# sudo nvidia-smi -lgc "${CLOCK_MIN_MHZ},${CLOCK_MAX_MHZ}"

echo "Applied Spark host profile: persistence=on, swap=off (clock cap disabled)"

## Monitoring Script
while true; do
    TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits)
    POWER=$(nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits)
    MEM_USED=$(free -g | awk '/Mem:/{print $3}')
    MEM_TOTAL=$(free -g | awk '/Mem:/{print $2}')
    SWAP_USED=$(free -g | awk '/Swap:/{print $3}')
    echo "$(date +%H:%M:%S) GPU=${TEMP}°C PWR=${POWER}W RAM=${MEM_USED}/${MEM_TOTAL}G SWAP=${SWAP_USED}G" | tee -a thermal_monitor.log
    sleep 5
done
