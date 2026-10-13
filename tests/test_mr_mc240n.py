import ctypes as ct
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mr_mc240n import MrMc240nBackend, PointData, find_api_dll
from servo_axis import AxisConfig


class PcieTests(unittest.TestCase):
    """No tests load a DLL or access a board."""

    def setUp(self):
        self.backend = MrMc240nBackend("unused.dll", axis_number=1)
        self.point_bytes = None
        self.offset = 0
        self.bits = {769, 770, 783}

        def read_system(_board, _channel, pointer):
            pointer._obj.value = 0x000A
            return 0

        def read_bit(_board, _channel, _axis, bit, pointer):
            pointer._obj.value = int(bit in self.bits)
            return 0

        def read_position(_board, _channel, _axis, pointer):
            pointer._obj.value = 100
            return 0

        def write_point(_board, _channel, _axis, _point, pointer):
            self.point_bytes = bytes(pointer._obj)
            return 0

        def read_point(_board, _channel, _axis, _point, pointer):
            ct.memmove(pointer, self.point_bytes, len(self.point_bytes))
            return 0

        def read_offset(_board, _channel, _axis, pointer):
            pointer._obj.value = self.offset
            return 0

        self.dll = SimpleNamespace(**{name: Mock(return_value=0) for name in (
            "sscOpen", "sscClose", "sscGetLastError", "sscSetCommandBitSignalEx",
            "sscAutoStart", "sscDriveRapidStop", "sscCheckParameterEx",
            "sscLoadAllParameterFromFlashROM", "sscSystemStart", "sscChange2Parameter")})
        for name, function in {
            "sscGetSystemStatusCode": read_system,
            "sscGetStatusBitSignalEx": read_bit,
            "sscGetCurrentFbPositionFast": read_position,
            "sscSetPointDataEx": write_point,
            "sscCheckPointDataEx": read_point,
            "sscCheckPointOffset": read_offset,
        }.items():
            setattr(self.dll, name, Mock(side_effect=function))
        self.backend._library = self.dll
        self.backend._bind()
        self.backend._opened = True
        self.config = AxisConfig(0, 10000, 200, acceleration_ms=400, deceleration_ms=600)

    def test_point_layout_matches_vendor_32_byte_structure(self):
        self.assertEqual(ct.sizeof(PointData), 32)
        self.assertEqual(PointData.speed.offset, 4)
        self.assertEqual(PointData.subcmd.offset, 14)
        self.assertEqual(PointData.sub_axnum.offset, 24)

    def test_flash_restore_is_rejected_while_running_or_any_axis_active(self):
        with self.assertRaisesRegex(RuntimeError, "READY"):
            self.backend.load_flash_parameters()
        with patch.object(self.backend, "system_status_code", return_value=1):
            with self.assertRaisesRegex(RuntimeError, "axis 1 is active"):
                self.backend.load_flash_parameters()
        self.dll.sscLoadAllParameterFromFlashROM.assert_not_called()

    def test_restore_and_start_never_enable_servo_or_clear_forced_stop(self):
        self.bits.clear()
        with patch.object(self.backend, "system_status_code", return_value=1):
            self.backend.load_flash_parameters()
            self.backend.start_system()
        self.dll.sscLoadAllParameterFromFlashROM.assert_called_once_with(0, 1, 30000)
        self.dll.sscSystemStart.assert_called_once_with(0, 1, 30000)
        self.dll.sscSetCommandBitSignalEx.assert_called_once_with(0, 1, 1, 513, 0)
        self.dll.sscAutoStart.assert_not_called()

    def test_parameter_abi_uses_unsigned_windows_32_bit_value(self):
        def read(_board, _channel, _axis, _number, pointer):
            pointer._obj.value = 0xFFFFFFFF
            return 0
        self.dll.sscCheckParameterEx.side_effect = read
        self.assertEqual(self.backend.read_parameter(1, 0x024D), 0xFFFFFFFF)
        self.assertEqual(self.dll.sscCheckParameterEx.argtypes[-1], ct.POINTER(ct.c_uint32))

    def test_speed_limit_writes_both_words_and_checks_readback_without_motion(self):
        self.bits.clear()

        def write(_board, _channel, _axis, numbers, values, statuses):
            self.assertEqual(list(numbers), [0x0222, 0x0223])
            self.assertEqual([value & 0xFFFF for value in values], [0xF123, 1])
            statuses[:] = [1, 1]
            return 0

        self.dll.sscChange2Parameter.side_effect = write
        with patch.object(self.backend, "read_speed_limit", side_effect=[200, 0x1F123]):
            self.assertEqual(self.backend.set_speed_limit(0x1F123), 200)
        self.dll.sscChange2Parameter.assert_called_once()
        self.dll.sscSetCommandBitSignalEx.assert_not_called()
        self.dll.sscAutoStart.assert_not_called()

    def test_speed_limit_change_is_blocked_while_servo_is_on(self):
        with self.assertRaisesRegex(RuntimeError, "axis 1 is active"):
            self.backend.set_speed_limit(20000)
        self.dll.sscChange2Parameter.assert_not_called()

    def test_speed_limit_matching_value_does_not_write(self):
        self.bits.clear()
        with patch.object(self.backend, "read_speed_limit", return_value=20000):
            self.assertEqual(self.backend.set_speed_limit(20000), 20000)
        self.dll.sscChange2Parameter.assert_not_called()

    def test_speed_limit_rejects_bad_per_parameter_status_and_bad_readback(self):
        self.bits.clear()
        for statuses, readback, message in (([1, 5], 20000, "not confirmed"),
                                          ([1, 1], 200, "verification failed")):
            def write(_b, _ch, _axis, _numbers, _values, result):
                result[:] = statuses
                return 0
            self.dll.sscChange2Parameter.side_effect = write
            with self.subTest(statuses=statuses), \
                    patch.object(self.backend, "read_speed_limit", side_effect=[200, readback]), \
                    self.assertRaisesRegex(RuntimeError, message):
                self.backend.set_speed_limit(20000)
        self.dll.sscAutoStart.assert_not_called()

    def test_open_read_close_does_not_enable_or_move_the_axis(self):
        self.backend._opened = False
        with patch("mr_mc240n.os.name", "nt"):
            self.backend.open()
        self.backend.read_status()
        self.backend.close()
        self.dll.sscOpen.assert_called_once_with(0)
        self.dll.sscClose.assert_called_once_with(0)
        self.dll.sscSetCommandBitSignalEx.assert_not_called()
        self.dll.sscSetPointDataEx.assert_not_called()
        self.dll.sscAutoStart.assert_not_called()

    def test_api_info_loads_the_selected_dll_without_accessing_board(self):
        self.backend._library = None
        self.backend._opened = False
        with TemporaryDirectory() as directory:
            self.backend.dll_path = Path(directory) / "mc2xxstd_x64.dll"
            self.backend.dll_path.touch()
            with patch("mr_mc240n.os.name", "nt"), \
                    patch("mr_mc240n.ct.WinDLL", return_value=self.dll, create=True) as loader:
                info = self.backend.api_info()
                loader.assert_called_once_with(str(self.backend.dll_path), winmode=0x1100)
        self.assertTrue(info["api_loaded"])
        self.assertEqual(info["python_bits"], ct.sizeof(ct.c_void_p) * 8)
        self.assertEqual(info["point_data_bytes"], 32)
        self.assertFalse(self.backend._opened)
        for function in vars(self.dll).values():
            function.assert_not_called()

    def test_missing_explicit_dll_never_falls_back_to_another_installation(self):
        self.backend._library = None
        self.backend._opened = False
        with TemporaryDirectory() as directory:
            self.backend.dll_path = Path(directory) / "missing.dll"
            with patch("mr_mc240n.os.name", "nt"), patch("mr_mc240n.find_api_dll") as find, \
                    self.assertRaises(FileNotFoundError):
                self.backend.open()
        find.assert_not_called()
        self.dll.sscOpen.assert_not_called()

    def test_missing_export_blocks_open(self):
        self.backend._opened = False
        del self.dll.sscAutoStart
        with patch("mr_mc240n.os.name", "nt"), \
                self.assertRaisesRegex(RuntimeError, "missing sscAutoStart"):
            self.backend.open()
        self.dll.sscOpen.assert_not_called()
        self.assertIsNone(self.backend._library)

    def test_wrong_bitness_error_explains_python_requirement(self):
        self.backend._library = None
        self.backend._opened = False
        failure = OSError("Bad executable format")
        failure.winerror = 193
        with TemporaryDirectory() as directory:
            self.backend.dll_path = Path(directory) / "wrong.dll"
            self.backend.dll_path.touch()
            with patch("mr_mc240n.os.name", "nt"), \
                    patch("mr_mc240n.ct.WinDLL", side_effect=failure, create=True), \
                    self.assertRaisesRegex(OSError, "bit API DLL matching this Python"):
                self.backend.open()
        self.dll.sscOpen.assert_not_called()

    def test_missing_board_does_not_mark_connection_open(self):
        self.backend._opened = False
        self.dll.sscOpen.return_value = -1
        self.dll.sscGetLastError.return_value = 0x00021010
        with patch("mr_mc240n.os.name", "nt"), \
                self.assertRaisesRegex(RuntimeError, "Board not found"):
            self.backend.open()
        self.assertFalse(self.backend._opened)

    def test_signed_vendor_error_is_printed_as_unsigned_hex(self):
        self.dll.sscClose.return_value = -1
        self.dll.sscGetLastError.return_value = -1
        with self.assertRaisesRegex(RuntimeError, "detail=0xFFFFFFFF"):
            self.backend.close()

    def test_status_uses_signed_feedback_and_correct_home_flag(self):
        self.bits.add(807)
        state = self.backend.read_status()
        self.assertEqual(state.position_counts, 100)
        self.assertTrue(state.home_required)
        self.assertTrue(state.system_running)
        self.assertFalse(state.encoder_error)

    def test_one_axis_absolute_point_and_start(self):
        self.backend.start_absolute(5000, self.config)
        point = PointData.from_buffer_copy(self.point_bytes)
        self.assertEqual((point.position, point.speed, point.actime, point.dctime),
                         (5000, 200, 400, 600))
        self.assertEqual(point.subcmd, 0)
        self.assertEqual(list(point.oas_num), [0, 0])
        self.assertEqual(list(point.sub_axnum), [0, 0, 0])
        self.dll.sscAutoStart.assert_called_once_with(0, 1, 1, 0, 0)
        self.dll.sscSetCommandBitSignalEx.assert_not_called()

    def test_nonzero_point_offset_does_not_start_another_point(self):
        self.offset = 1
        with self.assertRaisesRegex(RuntimeError, "offset must be zero"):
            self.backend.start_absolute(5000, self.config)
        self.dll.sscAutoStart.assert_not_called()
        self.dll.sscSetPointDataEx.assert_not_called()

    def test_failed_readback_never_starts(self):
        self.dll.sscCheckPointDataEx.side_effect = None
        self.dll.sscCheckPointDataEx.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            self.backend.start_absolute(5000, self.config)
        self.dll.sscAutoStart.assert_not_called()

    def test_alarm_and_bad_target_block_point_write(self):
        self.bits.add(774)
        with self.assertRaises(RuntimeError):
            self.backend.start_absolute(5000, self.config)
        self.bits.remove(774)
        with self.assertRaises(ValueError):
            self.backend.start_absolute(10001, self.config)
        self.dll.sscSetPointDataEx.assert_not_called()

    def test_stop_falls_back_to_confirmed_system_forced_stop(self):
        self.dll.sscDriveRapidStop.return_value = -1
        self.bits.add(273)
        self.backend.stop()
        self.dll.sscSetCommandBitSignalEx.assert_called_once_with(0, 1, 0, 17, 1)

    def test_unconfirmed_stop_is_an_error(self):
        self.bits.add(777)
        with self.assertRaisesRegex(RuntimeError, "forced stop failed"):
            self.backend.stop()

    def test_close_failure_remains_retryable_and_reports_vendor_error(self):
        self.dll.sscClose.return_value = -1
        self.dll.sscGetLastError.return_value = 0x21010
        with self.assertRaisesRegex(RuntimeError, "0x00021010"):
            self.backend.close()
        self.assertTrue(self.backend._opened)
        self.dll.sscClose.return_value = 0
        self.backend.close()
        self.assertFalse(self.backend._opened)


class ApiDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def install(self, root, filename):
        path = root / "Position Board" / "MR-MC2XX" / "API Library" / "Library" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path.resolve()

    def test_installed_library_matches_python_bitness_and_duplicate_roots(self):
        for bits, filename in ((64, "mc2xxstd_x64.dll"), (32, "mc2xxstd.dll")):
            expected = self.install(self.root, filename)
            with self.subTest(bits=bits), \
                    patch.dict(os.environ, {"ProgramFiles(x86)": str(self.root),
                                            "ProgramFiles": str(self.root)}, clear=True), \
                    patch("mr_mc240n.ct.sizeof", return_value=bits // 8):
                self.assertEqual(find_api_dll(), expected)

    def test_test_tool_and_sample_copies_are_not_used(self):
        for folder in ("PbTest", "API Library/Sample/PollingDrive/ExecutableFile"):
            path = self.root / "Position Board" / "MR-MC2XX" / folder
            path.mkdir(parents=True)
            (path / "mc2xxstd_x64.dll").touch()
        with patch.dict(os.environ, {"ProgramFiles(x86)": str(self.root)}, clear=True), \
                self.assertRaisesRegex(FileNotFoundError, "specify its path with --dll"):
            find_api_dll()

    def test_ambiguous_installations_require_explicit_path(self):
        filename = "mc2xxstd_x64.dll" if ct.sizeof(ct.c_void_p) == 8 else "mc2xxstd.dll"
        self.install(self.root / "first", filename)
        self.install(self.root / "second", filename)
        with patch.dict(os.environ, {"ProgramFiles(x86)": str(self.root / "first"),
                                     "ProgramFiles": str(self.root / "second")}, clear=True), \
                self.assertRaisesRegex(RuntimeError, "Multiple Mitsubishi API installations"):
            find_api_dll()


if __name__ == "__main__":
    unittest.main()
