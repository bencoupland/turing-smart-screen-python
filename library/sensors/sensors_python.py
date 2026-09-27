# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# This file will use Python libraries (psutil, GPUtil, etc.) to get hardware sensors
# For all platforms (Linux, Windows, macOS) but not all HW is supported

import glob
import math
import os
import platform
import sys
from collections import namedtuple
from enum import IntEnum, auto
from typing import Tuple

# Nvidia GPU
import GPUtil
# CPU & disk sensors
import psutil

import library.sensors.sensors as sensors
from library.log import logger

# AMD GPU on Linux
try:
    import pyamdgpuinfo
except:
    pyamdgpuinfo = None

# AMD GPU on Windows
try:
    import pyadl
except:
    pyadl = None

PNIC_BEFORE = {}


class GpuType(IntEnum):
    UNSUPPORTED = auto()
    AMD = auto()
    NVIDIA = auto()


DETECTED_GPU = GpuType.UNSUPPORTED


# Function inspired of psutil/psutil/_pslinux.py:sensors_fans()
# Adapted to also get fan speed percentage instead of raw value
def sensors_fans():
    """Return hardware fans info (for CPU and other peripherals) as a
    dict including hardware label and current speed.

    Implementation notes:
    - /sys/class/hwmon looks like the most recent interface to
      retrieve this info, and this implementation relies on it
      only (old distros will probably use something else)
    - lm-sensors on Ubuntu 16.04 relies on /sys/class/hwmon
    """
    from psutil._common import bcat, cat
    import collections, glob, os

    ret = collections.defaultdict(list)
    basenames = glob.glob('/sys/class/hwmon/hwmon*/fan*_*')
    if not basenames:
        # CentOS has an intermediate /device directory:
        # https://github.com/giampaolo/psutil/issues/971
        basenames = glob.glob('/sys/class/hwmon/hwmon*/device/fan*_*')

    basenames = sorted(set([x.split('_')[0] for x in basenames]))
    for base in basenames:
        try:
            current_rpm = int(bcat(base + '_input'))

            try:
                max_rpm = int(bcat(base + '_max'))
            except:
                max_rpm = False  # Real maximum speed not found
            if not max_rpm:
                if current_rpm > 2200:
                    max_rpm = 3000  # AIO Pumps are usualy 3000 RPM
                elif current_rpm > 1500:
                    max_rpm = 2200  # High speed fans are usualy 2200 RPM
                else:
                    max_rpm = 1500  # Approximated: max fan speed is 1500 RPM

            try:
                min_rpm = int(bcat(base + '_min'))
            except:
                min_rpm = 0  # Approximated: min fan speed is 0 RPM
            percent = int((current_rpm - min_rpm) / (max_rpm - min_rpm) * 100)
        except (IOError, OSError) as err:
            continue
        unit_name = cat(os.path.join(os.path.dirname(base), 'name')).strip()
        label = cat(base + '_label', fallback=os.path.basename(base)).strip()

        custom_sfan = namedtuple('sfan', ['label', 'current', 'percent'])
        ret[unit_name].append(custom_sfan(label, current_rpm, percent))

    return dict(ret)


def is_cpu_fan(label: str) -> bool:
    return ("cpu" in label.lower()) or ("proc" in label.lower())


_CORE_GROUPS = None
_CPU_USAGE_MODE_LOGGED = False
_CPU_FREQ_LOGGED = False


def _parse_cpu_list(text: str):
    """Parse a sysfs CPU list such as '0,44' or '0-3,8'."""
    cpus = []
    for part in text.replace("\n", "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            cpus.extend(range(int(start), int(end) + 1))
        else:
            cpus.append(int(part))
    return cpus


def physical_core_groups():
    """Logical CPUs that share a physical core, one tuple per core.

    On this machine each tuple is a hyper-thread pair, 44 pairs for 88 threads.
    """
    global _CORE_GROUPS
    if _CORE_GROUPS is not None:
        return _CORE_GROUPS

    groups = []
    seen = set()
    paths = glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/thread_siblings_list")

    def cpu_index(path):
        # .../cpu12/topology/thread_siblings_list
        cpu_dir = os.path.basename(os.path.dirname(os.path.dirname(path)))
        return int(cpu_dir[3:])

    for path in sorted(paths, key=cpu_index):
        with open(path, "r", encoding="ascii") as handle:
            members = tuple(sorted(set(_parse_cpu_list(handle.read()))))
        if members and members not in seen:
            seen.add(members)
            groups.append(members)

    _CORE_GROUPS = groups
    return groups


def core_utilization(per_cpu, groups) -> float:
    """Usage across physical cores, on a 0–100 scale.

    Each core contributes the sum of its threads' busy time, capped at 100.
    One busy thread on a hyper-thread pair therefore counts as that core being
    fully in use, and the second thread does not push the core past 100.
    The core figures are then averaged, so all 44 cores busy reads as 100
    rather than about 50.
    """
    if not per_cpu:
        return math.nan
    if not groups:
        return sum(per_cpu) / float(len(per_cpu))

    loads = []
    for group in groups:
        total = 0.0
        counted = False
        for index in group:
            if 0 <= index < len(per_cpu):
                total += per_cpu[index]
                counted = True
        if counted:
            if total > 100.0:
                total = 100.0
            loads.append(total)

    if not loads:
        return sum(per_cpu) / float(len(per_cpu))
    return sum(loads) / float(len(loads))


def fastest_frequency(currents) -> float:
    """Highest current clock in the readings, in MHz.

    psutil.cpu_freq() with no per-CPU flag averages every logical processor,
    so idle cores near the minimum clock hide a core that is boosting. The
    highest reading is that boost bin. When every core is busy the clocks
    settle together and this falls to the all-core clock. Zero and missing
    readings, such as an offline CPU, are ignored.
    """
    valid = []
    for value in currents:
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(value) or value <= 0.0:
            continue
        valid.append(value)
    if not valid:
        return math.nan
    return max(valid)


def cpu_usage_mode() -> str:
    """config.yaml CPU_USAGE: 'cores' (physical cores) or 'logical' (every thread)."""
    global _CPU_USAGE_MODE_LOGGED
    mode = "logical"
    try:
        import library.config as config
        raw = config.CONFIG_DATA.get("config", {}).get("CPU_USAGE", "logical")
        mode = str(raw).strip().lower()
    except Exception:
        mode = "logical"

    if mode in ("core", "cores", "physical"):
        mode = "cores"
    else:
        mode = "logical"

    if not _CPU_USAGE_MODE_LOGGED:
        if mode == "cores":
            logger.info(
                "CPU usage scale: cores (%d physical cores)"
                % (len(physical_core_groups()) or psutil.cpu_count(logical=False) or 0)
            )
        else:
            logger.info("CPU usage scale: logical processors")
        _CPU_USAGE_MODE_LOGGED = True
    return mode


class Cpu(sensors.Cpu):
    @staticmethod
    def percentage(interval: float) -> float:
        try:
            if cpu_usage_mode() == "cores":
                per_cpu = psutil.cpu_percent(interval=interval, percpu=True)
                return core_utilization(per_cpu, physical_core_groups())
            return psutil.cpu_percent(interval=interval)
        except:
            return math.nan

    @staticmethod
    def frequency() -> float:
        global _CPU_FREQ_LOGGED
        try:
            per_cpu = psutil.cpu_freq(percpu=True)
            if per_cpu:
                fastest = fastest_frequency(cpu.current for cpu in per_cpu)
                if not _CPU_FREQ_LOGGED and not math.isnan(fastest):
                    logger.info("CPU frequency: fastest logical processor")
                    _CPU_FREQ_LOGGED = True
                return fastest
            freq = psutil.cpu_freq()
            if freq is None:
                return math.nan
            return freq.current
        except:
            return math.nan

    @staticmethod
    def load() -> Tuple[float, float, float]:  # 1 / 5 / 15min avg (%):
        try:
            return psutil.getloadavg()
        except:
            return math.nan, math.nan, math.nan

    @staticmethod
    def temperature() -> float:
        cpu_temp = math.nan
        try:
            sensors_temps = psutil.sensors_temperatures()
            if 'coretemp' in sensors_temps:
                # Intel CPU
                cpu_temp = sensors_temps['coretemp'][0].current
            elif 'k10temp' in sensors_temps:
                # AMD CPU
                cpu_temp = sensors_temps['k10temp'][0].current
            elif 'cpu_thermal' in sensors_temps:
                # ARM CPU
                cpu_temp = sensors_temps['cpu_thermal'][0].current
            elif 'zenpower' in sensors_temps:
                # AMD CPU with zenpower (k10temp is in blacklist)
                cpu_temp = sensors_temps['zenpower'][0].current
        except:
            # psutil.sensors_temperatures not available on Windows / MacOS
            pass
        return cpu_temp

    @staticmethod
    def fan_percent(fan_name: str = None) -> float:
        try:
            fans = sensors_fans()
            if fans:
                for name, entries in fans.items():
                    for entry in entries:
                        if fan_name is not None and fan_name == "%s/%s" % (name, entry.label):
                            # Manually selected fan
                            return entry.percent
                        elif is_cpu_fan(entry.label) or is_cpu_fan(name):
                            # Auto-detected fan
                            return entry.percent
        except:
            pass

        return math.nan


class Gpu(sensors.Gpu):
    @staticmethod
    def stats() -> Tuple[
        float, float, float, float, float]:  # load (%) / used mem (%) / used mem (Mb) / total mem (Mb) / temp (°C)
        if DETECTED_GPU == GpuType.AMD:
            return GpuAmd.stats()
        elif DETECTED_GPU == GpuType.NVIDIA:
            return GpuNvidia.stats()
        else:
            return math.nan, math.nan, math.nan, math.nan, math.nan

    @staticmethod
    def fps() -> int:
        if DETECTED_GPU == GpuType.AMD:
            return GpuAmd.fps()
        elif DETECTED_GPU == GpuType.NVIDIA:
            return GpuNvidia.fps()
        else:
            return -1

    @staticmethod
    def fan_percent() -> float:
        if DETECTED_GPU == GpuType.AMD:
            return GpuAmd.fan_percent()
        elif DETECTED_GPU == GpuType.NVIDIA:
            return GpuNvidia.fan_percent()
        else:
            return math.nan

    @staticmethod
    def frequency() -> float:
        if DETECTED_GPU == GpuType.AMD:
            return GpuAmd.frequency()
        elif DETECTED_GPU == GpuType.NVIDIA:
            return GpuNvidia.frequency()
        else:
            return math.nan

    @staticmethod
    def is_available() -> bool:
        global DETECTED_GPU
        # Always use Nvidia GPU if available
        if GpuNvidia.is_available():
            logger.info("Detected Nvidia GPU(s)")
            DETECTED_GPU = GpuType.NVIDIA
        # Otherwise, use the AMD GPU / APU if available
        elif GpuAmd.is_available():
            logger.info("Detected AMD GPU(s)")
            DETECTED_GPU = GpuType.AMD
        else:
            logger.warning("No supported GPU found")
            DETECTED_GPU = GpuType.UNSUPPORTED
            if sys.version_info >= (3, 11) and (platform.system() == "Linux" or platform.system() == "Darwin"):
                logger.warning("If you have an AMD GPU, you may need to install some  libraries manually: see "
                               "https://github.com/mathoudebine/turing-smart-screen-python/wiki/Troubleshooting#linux--macos-no-supported-gpu-found-with-an-amd-gpu-and-python-311")

        return DETECTED_GPU != GpuType.UNSUPPORTED


class GpuNvidia(sensors.Gpu):
    @staticmethod
    def stats() -> Tuple[
        float, float, float, float, float]:  # load (%) / used mem (%) / used mem (Mb) / total mem (Mb) / temp (°C)
        # Unlike other sensors, Nvidia GPU with GPUtil pulls in all the stats at once
        nvidia_gpus = GPUtil.getGPUs()

        try:
            memory_used_all = [item.memoryUsed for item in nvidia_gpus]
            memory_used_mb = sum(memory_used_all) / len(memory_used_all)
        except:
            memory_used_mb = math.nan

        try:
            memory_total_all = [item.memoryTotal for item in nvidia_gpus]
            memory_total_mb = sum(memory_total_all) / len(memory_total_all)
        except:
            memory_total_mb = math.nan

        try:
            memory_percentage = (memory_used_mb / memory_total_mb) * 100
        except:
            memory_percentage = math.nan

        try:
            load_all = [item.load for item in nvidia_gpus]
            load = (sum(load_all) / len(load_all)) * 100
        except:
            load = math.nan

        try:
            temperature_all = [item.temperature for item in nvidia_gpus]
            temperature = sum(temperature_all) / len(temperature_all)
        except:
            temperature = math.nan

        return load, memory_percentage, memory_used_mb, memory_total_mb, temperature

    @staticmethod
    def fps() -> int:
        # Not supported by Python libraries
        return -1

    @staticmethod
    def fan_percent() -> float:
        try:
            fans = sensors_fans()
            if fans:
                for name, entries in fans.items():
                    for entry in entries:
                        if "gpu" in (entry.label.lower() or name.lower()):
                            return entry.percent
        except:
            pass

        return math.nan

    @staticmethod
    def frequency() -> float:
        # Not supported by Python libraries
        return math.nan

    @staticmethod
    def is_available() -> bool:
        try:
            return len(GPUtil.getGPUs()) > 0
        except:
            return False


class GpuAmd(sensors.Gpu):
    @staticmethod
    def stats() -> Tuple[
        float, float, float, float, float]:  # load (%) / used mem (%) / used mem (Mb) / total mem (Mb) / temp (°C)
        if pyamdgpuinfo:
            # Unlike other sensors, AMD GPU with pyamdgpuinfo pulls in all the stats at once
            pyamdgpuinfo.detect_gpus()
            amd_gpu = pyamdgpuinfo.get_gpu(0)

            try:
                memory_used_bytes = amd_gpu.query_vram_usage()
                memory_used = memory_used_bytes / 1024 / 1024
            except:
                memory_used_bytes = math.nan
                memory_used = math.nan

            try:
                memory_total_bytes = amd_gpu.memory_info["vram_size"]
                memory_total = memory_total_bytes / 1024 / 1024
            except:
                memory_total_bytes = math.nan
                memory_total = math.nan

            try:
                memory_percentage = (memory_used_bytes / memory_total_bytes) * 100
            except:
                memory_percentage = math.nan

            try:
                load = amd_gpu.query_load() * 100
            except:
                load = math.nan

            try:
                temperature = amd_gpu.query_temperature()
            except:
                temperature = math.nan

            return load, memory_percentage, memory_used, memory_total, temperature
        elif pyadl:
            amd_gpu = pyadl.ADLManager.getInstance().getDevices()[0]

            try:
                load = amd_gpu.getCurrentUsage()
            except:
                load = math.nan

            try:
                temperature = amd_gpu.getCurrentTemperature()
            except:
                temperature = math.nan

            # GPU memory data not supported by pyadl
            return load, math.nan, math.nan, math.nan, temperature

    @staticmethod
    def fps() -> int:
        # Not supported by Python libraries
        return -1

    @staticmethod
    def fan_percent() -> float:
        try:
            # Try with psutil fans
            fans = sensors_fans()
            if fans:
                for name, entries in fans.items():
                    for entry in entries:
                        if "gpu" in (entry.label.lower() or name.lower()):
                            return entry.percent

            # Try with pyadl if psutil did not find GPU fan
            if pyadl:
                return pyadl.ADLManager.getInstance().getDevices()[0].getCurrentFanSpeed(
                    pyadl.ADL_DEVICE_FAN_SPEED_TYPE_PERCENTAGE)
        except:
            pass

        return math.nan

    @staticmethod
    def frequency() -> float:
        try:
            if pyamdgpuinfo:
                pyamdgpuinfo.detect_gpus()
                return pyamdgpuinfo.get_gpu(0).query_sclk() / 1000000
            elif pyadl:
                return pyadl.ADLManager.getInstance().getDevices()[0].getCurrentEngineClock()
            else:
                return math.nan
        except:
            return math.nan

    @staticmethod
    def is_available() -> bool:
        try:
            if pyamdgpuinfo and pyamdgpuinfo.detect_gpus() > 0:
                return True
            elif pyadl and len(pyadl.ADLManager.getInstance().getDevices()) > 0:
                return True
            else:
                return False
        except:
            return False


class Memory(sensors.Memory):
    @staticmethod
    def swap_percent() -> float:
        try:
            return psutil.swap_memory().percent
        except:
            return math.nan

    @staticmethod
    def virtual_percent() -> float:
        try:
            return psutil.virtual_memory().percent
        except:
            return math.nan

    @staticmethod
    def virtual_used() -> int:  # In bytes
        try:
            # Do not use psutil.virtual_memory().used: from https://psutil.readthedocs.io/en/latest/#memory
            # "It is calculated differently depending on the platform and designed for informational purposes only"
            return psutil.virtual_memory().total - psutil.virtual_memory().available
        except:
            return -1

    @staticmethod
    def virtual_free() -> int:  # In bytes
        try:
            # Do not use psutil.virtual_memory().free: from https://psutil.readthedocs.io/en/latest/#memory
            # "note that this doesn’t reflect the actual memory available (use available instead)."
            return psutil.virtual_memory().available
        except:
            return -1


class Disk(sensors.Disk):
    @staticmethod
    def disk_usage_percent() -> float:
        try:
            return psutil.disk_usage("/").percent
        except:
            return math.nan

    @staticmethod
    def disk_used() -> int:  # In bytes
        try:
            return psutil.disk_usage("/").used
        except:
            return -1

    @staticmethod
    def disk_free() -> int:  # In bytes
        try:
            return psutil.disk_usage("/").free
        except:
            return -1


class Net(sensors.Net):
    @staticmethod
    def stats(if_name, interval) -> Tuple[
        int, int, int, int]:  # up rate (B/s), uploaded (B), dl rate (B/s), downloaded (B)
        try:
            # Get current counters
            pnic_after = psutil.net_io_counters(pernic=True)

            upload_rate = 0
            uploaded = 0
            download_rate = 0
            downloaded = 0

            if if_name != "":
                if if_name in pnic_after:
                    try:
                        upload_rate = (pnic_after[if_name].bytes_sent - PNIC_BEFORE[if_name].bytes_sent) / interval
                        uploaded = pnic_after[if_name].bytes_sent
                        download_rate = (pnic_after[if_name].bytes_recv - PNIC_BEFORE[if_name].bytes_recv) / interval
                        downloaded = pnic_after[if_name].bytes_recv
                    except:
                        # Interface might not be in PNIC_BEFORE for now
                        pass

                    PNIC_BEFORE.update({if_name: pnic_after[if_name]})
                else:
                    logger.warning("Network interface '%s' not found. Check names in config.yaml." % if_name)

            return upload_rate, uploaded, download_rate, downloaded
        except:
            return -1, -1, -1, -1
