"""Real system readings for the right panel: CPU, RAM, GPU, CPU temperature, disk and network.

Every number comes from the operating system. Anything that can't be read on this computer is returned as None and
the panel hides it: nothing is ever made up or shown as a fake zero. Readings are cheap (psutil, no new processes)
except the NVIDIA GPU, which is only asked every few seconds and only if nvidia-smi exists.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import psutil
except ImportError:                     # declared in pyproject; guarded so the app still starts without it
    psutil = None

_IS_WINDOWS = sys.platform == "win32"
GPU_EVERY = 5.0                          # seconds between NVIDIA queries
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class RAMInfo:
    percent: float
    total_gb: float
    used_gb: float
    free_gb: float

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class GPUInfo:
    model: str
    percent: float
    memory_used_mb: float
    memory_total_mb: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SystemTelemetryService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._net_last: tuple[float, int, int] | None = None
        self._gpu_cache: tuple[float, Optional[GPUInfo]] = (0.0, None)
        self._nvidia = shutil.which("nvidia-smi")
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)        # first call primes the counter
            except Exception:
                pass

    def get_cpu_percent(self) -> Optional[float]:
        if psutil is None:
            return None
        try:
            return round(float(psutil.cpu_percent(interval=None)), 1)
        except Exception:
            return None

    def get_ram_info(self) -> Optional[RAMInfo]:
        if psutil is None:
            return None
        try:
            vm = psutil.virtual_memory()
        except Exception:
            return None
        gb = 1024 ** 3
        return RAMInfo(percent=round(float(vm.percent), 1), total_gb=round(vm.total / gb, 2),
                       used_gb=round((vm.total - vm.available) / gb, 2), free_gb=round(vm.available / gb, 2))

    def get_gpu_info(self) -> Optional[GPUInfo]:
        """NVIDIA GPU use via nvidia-smi; None when there is no readable GPU (integrated GPUs aren't readable yet)."""
        if not self._nvidia:
            return None
        with self._lock:
            when, cached = self._gpu_cache
            if time.monotonic() - when < GPU_EVERY:
                return cached
            info = None
            try:
                proc = subprocess.run(
                    [self._nvidia, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=1.5, creationflags=NO_WINDOW)
                if proc.returncode == 0 and proc.stdout.strip():
                    parts = [p.strip() for p in proc.stdout.strip().splitlines()[0].split(",")]
                    if len(parts) >= 4:
                        info = GPUInfo(model=parts[0], percent=float(parts[1]),
                                       memory_used_mb=float(parts[2]), memory_total_mb=float(parts[3]))
            except Exception:
                info = None
            self._gpu_cache = (time.monotonic(), info)
            return info

    def get_cpu_temperature(self) -> Optional[float]:
        """CPU temperature in °C where the system exposes it (Linux, some PCs). Windows doesn't without admin
        rights or vendor tools, so it is usually None there and the panel hides it."""
        if psutil is None or not hasattr(psutil, "sensors_temperatures"):
            return None
        try:
            temps = psutil.sensors_temperatures() or {}
        except Exception:
            return None
        for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz", "zenpower"):
            for entry in temps.get(key, []):
                if entry.current and 0 < entry.current < 125:
                    return round(float(entry.current), 1)
        return None

    def get_disk(self) -> Optional[Dict[str, float]]:
        """Use of the drive that holds the user's files (C: on most Windows laptops)."""
        try:
            usage = shutil.disk_usage(Path.home())
        except Exception:
            return None
        gb = 1024 ** 3
        return {"percent": round(usage.used * 100 / usage.total, 1) if usage.total else 0.0,
                "free_gb": round(usage.free / gb, 1), "total_gb": round(usage.total / gb, 1)}

    def get_network(self) -> Optional[Dict[str, float]]:
        """Download and upload speed in KB/s since the previous reading (None on the very first reading)."""
        if psutil is None:
            return None
        try:
            io = psutil.net_io_counters()
        except Exception:
            return None
        now = time.monotonic()
        with self._lock:
            last, self._net_last = self._net_last, (now, io.bytes_recv, io.bytes_sent)
        if last is None or now - last[0] <= 0:
            return None
        secs = now - last[0]
        return {"down_kbps": round(max(io.bytes_recv - last[1], 0) / 1024 / secs, 1),
                "up_kbps": round(max(io.bytes_sent - last[2], 0) / 1024 / secs, 1)}

    def get_uptime_seconds(self) -> float:
        """Time since the computer started."""
        if psutil is not None:
            try:
                return round(time.time() - psutil.boot_time(), 1)
            except Exception:
                pass
        if _IS_WINDOWS:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.GetTickCount64.restype = ctypes.c_ulonglong
            return round(k32.GetTickCount64() / 1000.0, 1)
        return round(time.monotonic(), 1)

    def get_telemetry(self) -> Dict[str, Any]:
        ram, gpu = self.get_ram_info(), self.get_gpu_info()
        return {
            "cpu": self.get_cpu_percent(),
            "ram": ram.to_dict() if ram else None,
            "gpu": gpu.to_dict() if gpu else None,
            "temp_c": self.get_cpu_temperature(),
            "disk": self.get_disk(),
            "net": self.get_network(),
            "uptime_seconds": self.get_uptime_seconds(),
            "platform": sys.platform,
        }


telemetry_service = SystemTelemetryService()
