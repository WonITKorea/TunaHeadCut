"""Minimal MR-MC240N PCIe adapter for one HG-KR13 / MR-J4-B axis.

ABI and status bits follow CTR_ClampRing/hardware.py and Mitsubishi's
mc2xxstd.h / MR-MC200 API Library manual. Vendor binaries are not bundled.
"""

import ctypes as ct
import os
from pathlib import Path

from servo_axis import AxisConfig, AxisStatus, integer_in_range


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

    def __init__(self, dll_path: str | Path, *, board_id: int = 0,
                 axis_number: int = 1, point_number: int = 0):
        integer_in_range("board_id", board_id, 0, 3)
        integer_in_range("axis_number", axis_number, 1, 32)
        integer_in_range("point_number", point_number, 0, 319)
        self.dll_path = Path(dll_path).expanduser().resolve()
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
        }
        for name, arguments in signatures.items():
            function = getattr(self._library, name, None)
            if function is None:
                raise RuntimeError(f"API DLL is missing {name}; use the matching Utility2 API.")
            function.argtypes = arguments
            function.restype = ct.c_uint32 if name == "sscGetLastError" else ct.c_int

    def _invoke(self, name: str, *args) -> None:
        if not self._opened and name != "sscOpen":
            raise RuntimeError("MR-MC240N connection is closed.")
        result = getattr(self._library, name)(*args)
        if result != 0:
            detail = self._library.sscGetLastError()
            raise RuntimeError(f"{name} failed: status={result}, detail=0x{detail:08X}")

    def open(self) -> None:
        if self._opened:
            return
        if os.name != "nt":
            raise RuntimeError("MR-MC240N PCIe requires Windows.")
        if self._library is None:
            if not self.dll_path.is_file():
                raise FileNotFoundError(f"Mitsubishi API DLL not found: {self.dll_path}")
            # Do not search for or silently substitute a different runtime version.
            self._library = ct.WinDLL(str(self.dll_path))
        self._bind()
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

    def read_status(self) -> AxisStatus:
        system = ct.c_int16()
        self._invoke("sscGetSystemStatusCode", self.board_id, self.CHANNEL,
                     ct.byref(system))
        values = {name: self._bit(bit) for name, bit in self.STATUS_BITS.items()}
        forced_stop = self._bit(self.EMIO, axis=0)
        position = ct.c_int32()
        self._invoke("sscGetCurrentFbPositionFast", self.board_id, self.CHANNEL,
                     self.axis_number, ct.byref(position))
        return AxisStatus(position_counts=position.value,
                          system_running=(system.value & 0xFFFF) == self.RUNNING,
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
