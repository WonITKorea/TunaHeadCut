import contextlib
from dataclasses import replace
import io
import unittest
from unittest.mock import Mock, call

from motor_bench import (ROOT, MAX_TEST_SPEED, SPEED_PARAMETERS, COUNTS_PER_REVOLUTION,
                         bench_parameters, motor_degrees, prepare_motor_test, signed_parameter_pair)
from mr_mc240n import MrMc240nBackend
from servo_axis import AxisConfig, AxisStatus


class MotorBenchTests(unittest.TestCase):
    def setUp(self):
        self.config = AxisConfig.from_file(ROOT / "position_board" / "axis_config.utility2.json")
        self.expected = bench_parameters(self.config)
        self.backend = Mock(spec=MrMc240nBackend)
        self.backend.board_id, self.backend.axis_number, self.backend.point_number = 0, 1, 0
        self.backend.READY, self.backend.RUNNING = 1, 10
        self.backend.system_status_code.return_value = 10
        self.backend.read_parameter.side_effect = lambda axis, number: self.expected[axis, number]
        self.backend.read_status.return_value = AxisStatus(2790402, True, False, False, True, True)

    def prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            prepare_motor_test(self.backend, self.config, self.expected)

    def test_running_system_is_verified_without_restarting_or_reloading(self):
        self.prepare()
        self.assertEqual(self.backend.read_parameter.call_count, len(self.expected) - len(SPEED_PARAMETERS))
        self.backend.set_speed_limit.assert_called_once_with(self.config.speed)
        self.backend.load_flash_parameters.assert_not_called()
        self.backend.start_system.assert_not_called()
        self.backend.set_servo.assert_not_called()

    def test_ready_restores_then_verifies_before_start(self):
        self.backend.system_status_code.return_value = 1
        self.prepare()
        calls = self.backend.mock_calls
        loaded = calls.index(call.load_flash_parameters())
        started = calls.index(call.start_system())
        read_indices = [i for i, c in enumerate(calls) if c[0] == "read_parameter"]
        self.assertTrue(all(loaded < i < started for i in read_indices))
        self.assertLess(calls.index(call.set_speed_limit(self.config.speed)), started)
        self.backend.set_servo.assert_not_called()

    def test_wrong_flash_reference_blocks_system_start(self):
        self.backend.system_status_code.return_value = 1
        self.backend.read_parameter.side_effect = lambda a, n: (
            0 if (a, n) == (1, 0x0241) else self.expected[a, n])
        with self.assertRaisesRegex(RuntimeError, "parameter=0x0241"):
            self.prepare()
        self.backend.start_system.assert_not_called()
        self.backend.set_speed_limit.assert_not_called()
        self.backend.set_servo.assert_not_called()

    def test_active_other_axis_blocks_restore(self):
        self.backend.system_status_code.return_value = 1
        self.backend.check_all_axes_off.side_effect = RuntimeError("axis 2 is active")
        with self.assertRaisesRegex(RuntimeError, "axis 2"):
            self.prepare()
        self.backend.load_flash_parameters.assert_not_called()
        self.backend.start_system.assert_not_called()

    def test_alarms_are_reported_without_reboot_or_reset(self):
        self.backend.system_status_code.return_value = 0xE400
        with self.assertRaisesRegex(RuntimeError, "0xE400"):
            self.prepare()
        self.backend.load_flash_parameters.assert_not_called()
        self.backend.start_system.assert_not_called()

    def test_forced_stop_and_invalid_abs_block_arming(self):
        for flag in ("forced_stop", "home_required", "encoder_error"):
            state = AxisStatus(2790402, True, False, False, True, True)
            self.backend.read_status.return_value = replace(state, **{flag: True})
            with self.subTest(flag=flag), self.assertRaisesRegex(RuntimeError, flag):
                self.prepare()
        self.backend.set_servo.assert_not_called()
        self.backend.set_speed_limit.assert_not_called()

    def test_configured_speed_20000_is_applied_after_reference_validation(self):
        self.config = replace(self.config, speed=20000, move_timeout_s=900)
        self.expected = bench_parameters(self.config)
        self.assertEqual(self.expected[1, 0x0222], 20000)
        self.assertEqual(self.expected[1, 0x0223], 0)
        self.prepare()
        self.backend.set_speed_limit.assert_called_once_with(20000)
        calls = self.backend.mock_calls
        self.assertLess(calls.index(call.read_parameter(1, 0x0241)),
                        calls.index(call.set_speed_limit(20000)))

    def test_failed_speed_write_blocks_startup(self):
        self.backend.system_status_code.return_value = 1
        self.backend.set_speed_limit.side_effect = RuntimeError("speed readback failed")
        with self.assertRaisesRegex(RuntimeError, "speed readback failed"):
            self.prepare()
        self.backend.start_system.assert_not_called()
        self.backend.set_servo.assert_not_called()

    def test_manual_rotation_outside_range_blocks_speed_write_and_servo(self):
        self.backend.read_status.return_value = AxisStatus(
            self.config.max_position_counts + 100, True, False, False, True, True)
        with self.assertRaisesRegex(RuntimeError, "outside the configured travel range"):
            self.prepare()
        self.backend.set_speed_limit.assert_not_called()
        self.backend.set_servo.assert_not_called()

    def test_profile_rejects_changed_range_speed_or_short_timeout(self):
        for changes in ({"min_position_counts": 0}, {"speed": MAX_TEST_SPEED + 1},
                        {"reverse_direction": False}, {"move_timeout_s": 30}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                bench_parameters(replace(self.config, **changes))

    def test_four_revolutions_match_signed_software_limits_and_preserve_midpoint(self):
        lower = signed_parameter_pair(self.expected, 0x022A)
        upper = signed_parameter_pair(self.expected, 0x0228)
        self.assertEqual((lower, upper), (-5598206, 11179010))
        self.assertEqual(upper - lower, 4 * COUNTS_PER_REVOLUTION)
        self.assertEqual(motor_degrees(upper - lower), 1440)
        self.assertEqual(self.config.target_counts(0.5), 2790402)
        self.assertEqual(signed_parameter_pair(self.expected, 0x0246), 2790402)
        self.assertEqual([self.config.target_counts(v) for v in (0, 0.25, 0.5, 0.75, 1)],
                         [11179010, 6984706, 2790402, -1403902, -5598206])

    def test_lowering_speed_requires_timeout_for_the_entire_four_turn_range(self):
        with self.assertRaisesRegex(ValueError, "move_timeout_s >= 844.9"):
            bench_parameters(replace(self.config, speed=20000))


if __name__ == "__main__":
    unittest.main()
