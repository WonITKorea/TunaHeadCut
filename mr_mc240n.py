"""Minimal MR-MC240N PCIe adapter for one HG-KR13 / MR-J4-B axis.

ABI and status bits verified against the installed Mitsubishi mc2xxstd.h
Ver.2.40 and mc2xxstd_x64.dll 2.4.0.0. Vendor binaries are not bundled.
"""

import ctypes as ct
import os
from pathlib import Path

from servo_axis import AxisConfig, AxisStatus, integer_in_range


def find_api_dll() -> Path:
    """Find the installed API library for this Python's bitness, not test-tool DLLs."""
    bits = ct.sizeof(ct.c_void_p) * 8
    filename = "mc2xxstd_x64.dll" if bits == 64 else "mc2xxstd.dll"
    candidates = []
    for variable in ("ProgramFiles(x86)", "ProgramW6432", "ProgramFiles"):
        root = os.environ.get(variable)
        if root:
            candidate = (Path(root) / "Position Board" / "MR-MC2XX" /
                         "API Library" / "Library" / filename).resolve()
            if candidate not in candidates:
                candidates.append(candidate)
    found = [candidate for candidate in candidates if candidate.is_file()]
    if len(found) > 1:
        raise RuntimeError("Multiple Mitsubishi API installations found; select one with --dll: "
                           + ", ".join(map(str, found)))
    if not found:
        raise FileNotFoundError(
            f"No installed {filename} for {bits}-bit Python. Install the matching "
            "Position Board Utility2 API Library or specify its path with --dll. "
            "Searched: " + ", ".join(map(str, candidates)))
    return found[0]


class PointData(ct.Structure):
    # Windows long is 32-bit, including with a 64-bit Python interpreter.
    _fields_ = [
        ("position", ct.c_int32), ("speed", ct.c_uint32),
        ("actime", ct.c_uint16), ("dctime", ct.c_uint16),
        ("dwell", ct.c_uint16), ("subcmd", ct.c_uint16),
        ("oas_num", ct.c_uint8 * 2), ("reserve1", ct.c_uint8 * 2),
        ("s_curve", ct.c_uint8), ("reserve2", ct.c_uint8 * 3),
        ("sub_axnum", ct.c_uint8 * 3), ("reserve3", ct.c_uint8 * 5),
    ]


class MrMc240nBackend:
    CHANNEL = 1
    READY = 0x0001
    RUNNING = 0x000A
    SON = 513
    SEMI = 17
    EMIO = 273
    STATUS_BITS = {
        "servo_ready": 769,
        "in_position": 770,
        "servo_alarm": 774,
        "servo_warning": 775,
        "encoder_error": 776,
        "operating": 777,
        "operation_alarm": 782,
        "operation_complete": 783,
        "interlock_stop": 801,
        "home_required": 807,
    }

    def __init__(self, dll_path: str | Path | None = None, *, board_id: int = 0,
                 axis_number: int = 1, point_number: int = 0):
        integer_in_range("board_id", board_id, 0, 3)
        integer_in_range("axis_number", axis_number, 1, 32)
        integer_in_range("point_number", point_number, 0, 319)
        self.dll_path = Path(dll_path).expanduser().resolve() if dll_path is not None else None
        self.board_id = board_id
        self.axis_number = axis_number
        self.point_number = point_number
        self._library = None
        self._opened = False

    def _bind(self) -> None:
        signatures = {
            "sscOpen": [ct.c_int],
            "sscClose": [ct.c_int],
            "sscGetLastError": [],
            "sscGetSystemStatusCode": [ct.c_int, ct.c_int, ct.POINTER(ct.c_int16)],
            "sscGetCurrentFbPositionFast": [ct.c_int, ct.c_int, ct.c_int,
                                            ct.POINTER(ct.c_int32)],
            "sscGetStatusBitSignalEx": [ct.c_int] * 4 + [ct.POINTER(ct.c_int)],
            "sscSetCommandBitSignalEx": [ct.c_int] * 5,
            "sscSetPointDataEx": [ct.c_int] * 4 + [ct.POINTER(PointData)],
            "sscCheckPointDataEx": [ct.c_int] * 4 + [ct.POINTER(PointData)],
            "sscCheckPointOffset": [ct.c_int] * 3 + [ct.POINTER(ct.c_int16)],
            "sscAutoStart": [ct.c_int] * 5,
            "sscDriveRapidStop": [ct.c_int] * 4,
            "sscCheckParameterEx": [ct.c_int] * 3 + [ct.c_uint16, ct.POINTER(ct.c_uint32)],
            "sscChange2Parameter": [ct.c_int] * 3 + [ct.POINTER(ct.c_int16),
                                                       ct.POINTER(ct.c_int16), ct.POINTER(ct.c_uint8)],
            "sscLoadAllParameterFromFlashROM": [ct.c_int] * 3,
            "sscSystemStart": [ct.c_int] * 3,
        }
        for name, arguments in signatures.items():
            function = getattr(self._library, name, None)
            if function is None:
                raise RuntimeError(f"API DLL is missing {name}; use the matching Utility2 API.")
            function.argtypes = arguments
            function.restype = ct.c_int

    def _invoke(self, name: str, *args) -> None:
        if not self._opened and name != "sscOpen":
            raise RuntimeError("MR-MC240N connection is closed.")
        result = getattr(self._library, name)(*args)
        if result != 0:
            detail = self._library.sscGetLastError() & 0xFFFFFFFF
            hints = {
                0x00021010: "Board not found; check the board ID and Device Manager.",
                0x00021011: "Cannot read board channels; check Device Manager.",
                0x00021012: "API and device driver versions are incompatible.",
                0x00023000: "Device driver access failed; check the installed board driver.",
            }
            hint = hints.get(detail, "")
            raise RuntimeError(f"{name} failed: status={result}, detail=0x{detail:08X}, "
                               f"board={self.board_id}, axis={self.axis_number}. {hint}".rstrip())

    def load_api(self) -> None:
        """Load and bind the vendor API without opening or commanding a board."""
        if os.name != "nt":
            raise RuntimeError("MR-MC240N PCIe requires Windows.")
        if self._library is None:
            if self.dll_path is None:
                self.dll_path = find_api_dll()
            if not self.dll_path.is_file():
                raise FileNotFoundError(f"Mitsubishi API DLL not found: {self.dll_path}")
            try:
                # Resolve dependencies beside this DLL and in Windows' default safe dirs.
                # Never substitute another DLL if the selected installation cannot load.
                self._library = ct.WinDLL(str(self.dll_path), winmode=0x00001100)
            except OSError as exc:
                bits = ct.sizeof(ct.c_void_p) * 8
                hint = (f"Use a {bits}-bit API DLL matching this Python."
                        if getattr(exc, "winerror", None) == 193 else
                        "Check the Utility2 API installation and its runtime dependencies.")
                raise OSError(f"Cannot load Mitsubishi API DLL {self.dll_path}: {exc}. {hint}") from exc
        try:
            self._bind()
        except Exception:
            self._library = None
            raise

    def api_info(self) -> dict:
        self.load_api()
        return {"dll_path": str(self.dll_path),
                "python_bits": ct.sizeof(ct.c_void_p) * 8,
                "point_data_bytes": ct.sizeof(PointData),
                "api_loaded": True}

    def open(self) -> None:
        if self._opened:
            return
        self.load_api()
        self._invoke("sscOpen", self.board_id)
        self._opened = True
        # Opening alone never starts the system, changes parameters, or sends SON.

    def close(self) -> None:
        if self._opened:
            self._invoke("sscClose", self.board_id)
            self._opened = False

    def _bit(self, bit_number: int, *, axis: int | None = None) -> bool:
        value = ct.c_int()
        self._invoke("sscGetStatusBitSignalEx", self.board_id, self.CHANNEL,
                     self.axis_number if axis is None else axis,
                     bit_number, ct.byref(value))
        return bool(value.value)

    def system_status_code(self) -> int:
        system = ct.c_int16()
        self._invoke("sscGetSystemStatusCode", self.board_id, self.CHANNEL,
                     ct.byref(system))
        return system.value & 0xFFFF

    def read_parameter(self, axis: int, number: int) -> int:
        value = ct.c_uint32()
        self._invoke("sscCheckParameterEx", self.board_id, self.CHANNEL,
                     axis, number, ct.byref(value))
        return value.value

    def check_all_axes_off(self) -> None:
        for axis in range(1, 33):
            if (self._bit(self.STATUS_BITS["servo_ready"], axis=axis)
                    or self._bit(self.STATUS_BITS["operating"], axis=axis)):
                raise RuntimeError(f"Motor test requires all servos OFF and idle; axis {axis} is active.")

    def read_speed_limit(self) -> int:
        lower = self.read_parameter(self.axis_number, 0x0222)
        upper = self.read_parameter(self.axis_number, 0x0223)
        return lower | (upper << 16)

    def set_speed_limit(self, speed: int) -> int:
        """Set/read back both SPL words together with all servos OFF; return the old limit."""
        integer_in_range("speed limit", speed, 1, 2**31 - 1)
        if self.system_status_code() not in (self.READY, self.RUNNING):
            raise RuntimeError("Speed limit changes require READY or RUNNING.")
        self.check_all_axes_off()
        previous = self.read_speed_limit()
        if previous == speed:
            return previous
        # The vendor requires simultaneous writes for paired 32-bit parameters.
        numbers = (ct.c_int16 * 2)(0x0222, 0x0223)
        values = (ct.c_int16 * 2)(speed & 0xFFFF, speed >> 16)
        statuses = (ct.c_uint8 * 2)()
        self._invoke("sscChange2Parameter", self.board_id, self.CHANNEL, self.axis_number,
                     numbers, values, statuses)
        if list(statuses) != [0x01, 0x01]:  # PWFIN, with no PWEN/PWED bits.
            raise RuntimeError(f"Speed limit parameter write was not confirmed: {list(statuses)}.")
        actual = self.read_speed_limit()
        if actual != speed:
            raise RuntimeError(f"Speed limit verification failed: expected={speed}, actual={actual}.")
        return previous

    def load_flash_parameters(self) -> None:
        if self.system_status_code() != self.READY:
            raise RuntimeError("Flash parameters can only be loaded in READY (0x0001).")
        self.check_all_axes_off()
        self._invoke("sscLoadAllParameterFromFlashROM", self.board_id, self.CHANNEL, 30000)

    def start_system(self) -> None:
        if self.system_status_code() != self.READY:
            raise RuntimeError("System Start requires READY (0x0001).")
        self.check_all_axes_off()
        # Clear any stale SON request before the system can enable axis 1.
        self.set_servo(False)
        self._invoke("sscSystemStart", self.board_id, self.CHANNEL, 30000)

    def read_status(self) -> AxisStatus:
        system = self.system_status_code()
        values = {name: self._bit(bit) for name, bit in self.STATUS_BITS.items()}
        forced_stop = self._bit(self.EMIO, axis=0)
        position = ct.c_int32()
        self._invoke("sscGetCurrentFbPositionFast", self.board_id, self.CHANNEL,
                     self.axis_number, ct.byref(position))
        return AxisStatus(position_counts=position.value,
                          system_running=system == self.RUNNING,
                          forced_stop=forced_stop, **values)

    def set_servo(self, enabled: bool) -> None:
        self._invoke("sscSetCommandBitSignalEx", self.board_id, self.CHANNEL,
                     self.axis_number, self.SON, int(enabled))

    def start_absolute(self, target: int, config: AxisConfig) -> None:
        integer_in_range("target", target, config.min_position_counts,
                         config.max_position_counts)
        self.read_status().check(config, idle=True)
        # A point offset can redirect AutoStart to a different point-table entry.
        offset = ct.c_int16()
        self._invoke("sscCheckPointOffset", self.board_id, self.CHANNEL,
                     self.axis_number, ct.byref(offset))
        if offset.value != 0:
            raise RuntimeError("Point offset must be zero; adjust it in Utility2 before use.")
        point = PointData()
        point.position = target
        point.speed = config.speed
        point.actime = config.acceleration_ms
        point.dctime = config.deceleration_ms
        # ABS (0x0000) | in-position stop (0x0000); no other-axis start.
        point.subcmd = 0
        self._invoke("sscSetPointDataEx", self.board_id, self.CHANNEL,
                     self.axis_number, self.point_number, ct.byref(point))
        readback = PointData()
        self._invoke("sscCheckPointDataEx", self.board_id, self.CHANNEL,
                     self.axis_number, self.point_number, ct.byref(readback))
        if bytes(point) != bytes(readback):
            raise RuntimeError("Point data verification failed; no start command was sent.")
        self._invoke("sscAutoStart", self.board_id, self.CHANNEL, self.axis_number,
                     self.point_number, self.point_number)

    def stop(self) -> None:
        try:
            self._invoke("sscDriveRapidStop", self.board_id, self.CHANNEL,
                         self.axis_number, 3000)
            if self._bit(self.STATUS_BITS["operating"]):
                raise RuntimeError("Rapid stop returned while OP was still ON.")
        except Exception as primary_error:
            # Same final fallback as CTR_ClampRing: latch system forced stop.
            # This application never clears SEMI or bypasses external stops.
            try:
                self._invoke("sscSetCommandBitSignalEx", self.board_id, self.CHANNEL,
                             0, self.SEMI, 1)
                if not self._bit(self.EMIO, axis=0):
                    raise RuntimeError("System forced stop was not confirmed.")
            except Exception as fallback_error:
                raise RuntimeError(
                    f"Rapid stop failed ({primary_error}); forced stop failed ({fallback_error})."
                ) from primary_error
