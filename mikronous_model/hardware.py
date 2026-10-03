"""Hardware detection: GPU (NVIDIA via nvidia-smi, AMD via rocm-smi, anything via vulkaninfo), RAM, CPU."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

MiB = 1024 * 1024


@dataclass
class GPU:
    vendor: str            # nvidia | amd | intel | unknown
    name: str
    vram_bytes: int        # total
    vram_used_bytes: int = 0
    driver: str = ""
    cuda_version: str = ""  # driver-reported CUDA version (NVIDIA only)

    @property
    def vram_free_bytes(self) -> int:
        return max(0, self.vram_bytes - self.vram_used_bytes)


@dataclass
class HardwareProfile:
    gpus: list[GPU] = field(default_factory=list)
    ram_bytes: int = 0
    ram_available_bytes: int = 0
    cpu_threads: int = 1
    cpu_name: str = ""

    @property
    def gpu(self) -> GPU | None:
        return max(self.gpus, key=lambda g: g.vram_bytes) if self.gpus else None

    def to_dict(self) -> dict:
        return {
            "gpus": [g.__dict__ for g in self.gpus],
            "ram_bytes": self.ram_bytes,
            "ram_available_bytes": self.ram_available_bytes,
            "cpu_threads": self.cpu_threads,
            "cpu_name": self.cpu_name,
        }


def _run(cmd: list[str], timeout: float = 8.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _nvidia() -> list[GPU]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader,nounits"])
    gpus: list[GPU] = []
    cuda = ""
    banner = _run(["nvidia-smi"])
    m = re.search(r"CUDA Version:\s*([0-9]+\.[0-9]+)", banner)
    if m:
        cuda = m.group(1)
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            gpus.append(GPU("nvidia", parts[0], int(float(parts[1])) * MiB, int(float(parts[2])) * MiB, parts[3], cuda))
        except ValueError:
            continue
    return gpus


def _amd() -> list[GPU]:
    if not shutil.which("rocm-smi"):
        return []
    out = _run(["rocm-smi", "--showmeminfo", "vram", "--showproductname", "--json"])
    gpus: list[GPU] = []
    try:
        data = json.loads(out) if out.strip() else {}
    except ValueError:
        return []
    for _card, info in data.items():
        if not isinstance(info, dict):
            continue
        total = info.get("VRAM Total Memory (B)") or info.get("vram_total")
        used = info.get("VRAM Total Used Memory (B)") or info.get("vram_used") or 0
        name = info.get("Card Series") or info.get("Card series") or info.get("Card model") or "AMD GPU"
        if total:
            gpus.append(GPU("amd", str(name), int(total), int(used)))
    return gpus


def _vulkan() -> list[GPU]:
    """Last resort: parse `vulkaninfo --summary` (device names) and `vulkaninfo` heap sizes."""
    if not shutil.which("vulkaninfo"):
        return []
    summary = _run(["vulkaninfo", "--summary"], timeout=15)
    names = re.findall(r"deviceName\s*=\s*(.+)", summary)
    types = re.findall(r"deviceType\s*=\s*(\S+)", summary)
    gpus: list[GPU] = []
    for i, name in enumerate(names):
        dtype = types[i] if i < len(types) else ""
        if "CPU" in dtype:
            continue
        low = name.lower()
        vendor = "nvidia" if "nvidia" in low else "amd" if ("amd" in low or "radeon" in low) else "intel" if "intel" in low else "unknown"
        # Heap sizes need the full dump; take the largest DEVICE_LOCAL heap we can find.
        full = _run(["vulkaninfo"], timeout=20)
        heaps = [int(h) for h in re.findall(r"size\s*=\s*(\d+)\s*\(0x[0-9a-f]+\)\s*\([^)]*\)\n\s*budget[^\n]*\n\s*usage[^\n]*\n\s*flags:[^\n]*\n\s*MEMORY_HEAP_DEVICE_LOCAL_BIT", full)]
        vram = max(heaps) if heaps else 0
        if "integrated" in dtype.lower() and vram == 0:
            vram = 0
        gpus.append(GPU(vendor, name.strip(), vram))
    return gpus


def _windows_memory() -> tuple[int, int]:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return int(st.ullTotalPhys), int(st.ullAvailPhys)
    return 0, 0


def detect() -> HardwareProfile:
    prof = HardwareProfile()
    prof.gpus = _nvidia() or _amd() or _vulkan()
    if sys.platform == "win32":
        prof.ram_bytes, prof.ram_available_bytes = _windows_memory()
        prof.cpu_threads = os.cpu_count() or 1
        prof.cpu_name = os.environ.get("PROCESSOR_IDENTIFIER", "")
        return prof
    try:
        meminfo = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.partition(":")
                meminfo[k.strip()] = int(v.strip().split()[0]) * 1024
        prof.ram_bytes = meminfo.get("MemTotal", 0)
        prof.ram_available_bytes = meminfo.get("MemAvailable", 0)
    except (OSError, ValueError):
        pass
    prof.cpu_threads = os.cpu_count() or 1
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("model name"):
                    prof.cpu_name = line.partition(":")[2].strip()
                    break
    except OSError:
        pass
    return prof


def fmt_bytes(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit not in ("B",) else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"
