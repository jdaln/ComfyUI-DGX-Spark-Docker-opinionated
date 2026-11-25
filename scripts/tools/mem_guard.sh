#!/usr/bin/env bash
# Kill container NAME if the machine's available memory drops below FLOOR GiB; log the lowest value seen.
# CPU and GPU share one memory pool on the DGX Spark, and running out means a hard reboot.
# Usage: scripts/tools/mem_guard.sh NAME FLOOR_GIB   (runs until the container stops)
name=$1; floor=$2; lowest=999
while docker ps --filter "name=^${name}$" --filter status=running -q | grep -q .; do
    avail=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
    if [ "$avail" -lt "$lowest" ]; then lowest=$avail; echo "lowest available $lowest GiB at $(date +%T)"; fi
    if [ "$avail" -lt "$floor" ]; then echo "KILL at $avail GiB"; docker kill "$name"; fi
    sleep 1
done
echo "guard done, lowest available $lowest GiB"
