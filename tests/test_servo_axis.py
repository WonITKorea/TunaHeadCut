from dataclasses import replace
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from servo_axis import AxisConfig, NormalizedAxis, SimulatedBackend


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.config = AxisConfig(-2000, 6000, 100)

    def test_endpoints_and_midpoint_use_absolute_coordinates(self):
        self.assertEqual([self.config.target_counts(v) for v in (0, 0.25, 0.5, 1)],
                         [-2000, 0, 2000, 6000])

    def test_invalid_values_are_rejected_instead_of_clamped(self):
        for value in (-0.001, 1.001, "NaN", "inf", "-inf", "", "abc", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.config.target_counts(value)

    def test_full_signed_board_position_range(self):
        config = AxisConfig(-(2**31), 2**31 - 1, 100)
        self.assertEqual(config.target_counts(0), -(2**31))
        self.assertEqual(config.target_counts(1), 2**31 - 1)

    def test_bad_calibration_and_motion_settings_are_rejected(self):
        for changes in (
            {"min_position_counts": None}, {"max_position_counts": 2**31},
            {"min_position_counts": -2000.0}, {"min_position_counts": 6000},
            {"max_position_counts": -2001}, {"speed": 0}, {"speed": True},
            {"acceleration_ms": 0}, {"deceleration_ms": 20001},
            {"move_timeout_s": float("nan")}, {"poll_interval_s": 2},
            {"position_tolerance_counts": 8000},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.config, **changes)

    def test_config_can_be_changed_without_editing_code(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "axis.json"
            config_file.write_text(
                '{"min_position_counts":100,"max_position_counts":200,"speed":10}',
                encoding="utf-8")
            self.assertEqual(AxisConfig.from_file(config_file).target_counts(0.5), 150)
            config_file.write_text('{"unexpected":1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                AxisConfig.from_file(config_file)


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.config = AxisConfig(0, 10000, 100, position_tolerance_counts=0)
        self.backend = SimulatedBackend()
        self.axis = NormalizedAxis(self.backend, self.config)

    def test_connect_is_read_only_and_motion_requires_arm(self):
        self.axis.connect()
        self.assertFalse(self.backend.ready)
        with self.assertRaises(RuntimeError):
            self.axis.move_normalized(0.5)
        self.axis.close()

    def test_repeated_input_does_not_accumulate_relative_moves(self):
        with self.axis:
            self.axis.arm()
            self.assertEqual(self.axis.move_normalized(0.5), 5000)
            self.assertEqual(self.axis.move_normalized(0.5), 5000)
            self.assertEqual(self.axis.move_normalized(0), 0)
            self.assertEqual(self.axis.move_normalized(1), 10000)
        self.assertFalse(self.backend.ready)
        self.assertFalse(self.backend.opened)

    def test_invalid_input_performs_no_io(self):
        with self.axis:
            self.axis.arm()
            with patch.object(self.backend, "read_status") as read:
                with self.assertRaises(ValueError):
                    self.axis.move_normalized(float("nan"))
                read.assert_not_called()

    def test_arm_rejects_missing_reference_alarms_stops_and_motion(self):
        self.axis.connect()
        healthy = self.backend.read_status()
        for key, value in (
            ("system_running", False), ("home_required", True), ("encoder_error", True),
            ("servo_alarm", True), ("operation_alarm", True), ("forced_stop", True),
            ("interlock_stop", True), ("servo_warning", True), ("operating", True),
            ("position_counts", -1),
        ):
            with self.subTest(key=key), patch.object(
                    self.backend, "read_status", return_value=replace(healthy, **{key: value})):
                with self.assertRaises(RuntimeError):
                    self.axis.arm()
                self.assertFalse(self.backend.ready)
        self.axis.close()

    def test_preexisting_servo_on_is_preserved_at_close(self):
        self.backend.ready = True
        with self.axis:
            self.axis.arm()
            self.axis.move_normalized(0.5)
        self.assertTrue(self.backend.ready)

    def test_unconfirmed_servo_off_keeps_connection_for_retry(self):
        self.axis.connect()
        self.axis.arm()
        with patch.object(self.backend, "set_servo"), \
                patch("servo_axis.time.monotonic", side_effect=[0, 6]):
            with self.assertRaisesRegex(TimeoutError, "Servo OFF"):
                self.axis.close()
        self.assertTrue(self.backend.opened)
        self.axis.close()
        self.assertFalse(self.backend.ready)

    def test_uncertain_servo_on_failure_is_cleaned_up(self):
        self.axis.connect()
        real_set_servo = self.backend.set_servo

        def failed_response(enabled):
            real_set_servo(enabled)
            raise OSError("SON response lost")

        with patch.object(self.backend, "set_servo", side_effect=failed_response):
            with self.assertRaisesRegex(OSError, "SON response lost"):
                self.axis.arm()
        self.assertTrue(self.backend.ready)
        self.axis.close()
        self.assertFalse(self.backend.ready)

    def test_stale_completion_flags_at_old_position_do_not_complete_move(self):
        self.axis.connect()
        self.axis.arm()
        old = self.backend.read_status()
        new = replace(old, position_counts=5000)
        with patch.object(self.backend, "read_status", side_effect=[old, old, new]) as read:
            self.assertEqual(self.axis.move_normalized(0.5), 5000)
            self.assertEqual(read.call_count, 3)
        self.axis.close()

    def test_timeout_stops_and_latches_failure(self):
        self.axis.connect()
        self.axis.arm()
        with patch.object(self.backend, "start_absolute"), \
                patch.object(self.backend, "stop") as stop, \
                patch("servo_axis.time.monotonic", side_effect=[0, 31]):
            with self.assertRaises(TimeoutError):
                self.axis.move_normalized(1)
            stop.assert_called_once()
        with self.assertRaises(RuntimeError):
            self.axis.arm()
        with self.assertRaises(RuntimeError):
            self.axis.move_normalized(0.2)
        self.axis.close()

    def test_start_failure_also_attempts_stop(self):
        self.axis.connect()
        self.axis.arm()
        with patch.object(self.backend, "start_absolute", side_effect=OSError("lost response")), \
                patch.object(self.backend, "stop") as stop:
            with self.assertRaisesRegex(OSError, "lost response"):
                self.axis.move_normalized(1)
            stop.assert_called_once()
        self.axis.close()

    def test_feedback_fault_and_ctrl_c_both_stop_motion(self):
        for error in (OSError("feedback failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                backend = SimulatedBackend()
                axis = NormalizedAxis(backend, self.config)
                axis.connect()
                axis.arm()
                old = backend.read_status()
                with patch.object(backend, "read_status", side_effect=[old, error]), \
                        patch.object(backend, "stop") as stop:
                    with self.assertRaises(type(error)):
                        axis.move_normalized(1)
                    stop.assert_called_once()
                axis.close()

    def test_failure_during_travel_stops(self):
        for changes in ({"encoder_error": True}, {"position_counts": 10001}):
            backend = SimulatedBackend()
            with NormalizedAxis(backend, self.config) as axis:
                axis.arm()
                old = backend.read_status()
                with patch.object(backend, "read_status",
                                  side_effect=[old, replace(old, **changes)]), \
                        patch.object(backend, "stop") as stop:
                    with self.assertRaises(RuntimeError):
                        axis.move_normalized(1)
                    stop.assert_called_once()

    def test_stop_failure_is_reported_and_connection_is_kept_for_retry(self):
        self.axis.connect()
        self.axis.arm()
        with patch.object(self.backend, "start_absolute", side_effect=OSError("dispatch")), \
                patch.object(self.backend, "stop", side_effect=OSError("stop")):
            with self.assertRaisesRegex(RuntimeError, "stop could not be confirmed"):
                self.axis.move_normalized(1)
            with self.assertRaisesRegex(OSError, "stop"):
                self.axis.close()
            self.assertTrue(self.backend.opened)
        self.axis.close()

    def test_concurrent_move_is_rejected(self):
        self.axis._lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, "already in progress"):
                self.axis.move_normalized(0.5)
        finally:
            self.axis._lock.release()


if __name__ == "__main__":
    unittest.main()
