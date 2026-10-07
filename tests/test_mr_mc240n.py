import ctypes as ct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mr_mc240n import MrMc240nBackend, PointData
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
            "sscAutoStart", "sscDriveRapidStop")})
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


if __name__ == "__main__":
    unittest.main()
