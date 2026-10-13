"""Explicit startup for the commissioned, uncoupled board-0 / axis-1 motor."""

from pathlib import Path
import time

from mr_mc240n import MrMc240nBackend
from servo_axis import AxisConfig


ROOT = Path(__file__).resolve().parent
PROJECT = ROOT / "position_board" / "TunaHeadCut-MotorBench.prm2"
MAX_TEST_SPEED = 20_000
SPEED_PARAMETERS = {(1, 0x0222), (1, 0x0223)}
# HG-KR13: 22-bit encoder; the verified MotorBench electronic gear is 1:1.
COUNTS_PER_REVOLUTION = 4_194_304


def motor_degrees(counts: int) -> float:
    """Motor-shaft angle for this specific HG-KR13 / 1:1 bench profile."""
    return counts * 360 / COUNTS_PER_REVOLUTION


def bench_parameters(config: AxisConfig) -> dict[tuple[int, int], int]:
    # Check assignment, units/gearing, travel, ABS reference and input settings.
    keys = {(0, number) for number in (0x0002, 0x000E)}
    keys.update((axis, 0x0200) for axis in range(1, 33))
    keys.update((1, number) for number in (
        0x0203, *range(0x020A, 0x0210), 0x0219, 0x021D, 0x021E,
        0x0220, 0x0222, 0x0223, *range(0x0228, 0x022C),
        0x0240, 0x0241, *range(0x0246, 0x024A), *range(0x024D, 0x0250),
        0x1102, 0x1103))
    expected = {}
    for line in PROJECT.read_text(encoding="utf-8-sig").splitlines():
        axis, number, value = (int(field, 0) for field in line.split(","))
        if (axis, number) in keys:
            if (axis, number) in expected:
                raise ValueError("Duplicate MotorBench parameter.")
            expected[axis, number] = value
    if expected.keys() != keys or expected[1, 0x0241] != 1:
        raise ValueError("MotorBench project is missing a registered ABS reference.")
    if expected[1, 0x0200] != 0x1001 or any(expected[a, 0x0200] for a in range(2, 33)):
        raise ValueError("MotorBench must control only axis 1.")
    if config.min_position_counts != 2786203 or config.max_position_counts != 2794600:
        raise ValueError("Motor test requires endpoints 2786203..2794600.")
    if not config.reverse_direction:
        raise ValueError("Motor test requires reverse_direction=true.")
    if config.speed > MAX_TEST_SPEED:
        raise ValueError(f"Motor test speed={config.speed} count/s exceeds the bench maximum "
                         f"of {MAX_TEST_SPEED} count/s.")
    # This is a per-session speed setting, not a change to the ABS calibration.
    expected[1, 0x0222] = config.speed & 0xFFFF
    expected[1, 0x0223] = config.speed >> 16
    minimum_timeout = ((config.max_position_counts - config.min_position_counts) / config.speed
                       + (config.acceleration_ms + config.deceleration_ms) / 1000 + 5)
    if config.move_timeout_s < minimum_timeout:
        raise ValueError(f"Motor test requires move_timeout_s >= {minimum_timeout:.1f} at this speed.")
    return expected


def verify_parameters(backend: MrMc240nBackend, expected: dict[tuple[int, int], int]) -> None:
    for (axis, number), value in sorted(expected.items()):
        actual = backend.read_parameter(axis, number)
        if actual != value:
            raise RuntimeError(
                f"MotorBench parameter mismatch: axis={axis}, parameter=0x{number:04X}, "
                f"expected=0x{value:04X}, actual=0x{actual:04X}. "
                "Restore the registered TunaHeadCut-MotorBench project in Utility2.")


def prepare_motor_test(backend: MrMc240nBackend, config: AxisConfig,
                       expected: dict[tuple[int, int], int]) -> None:
    """Restore saved parameters only in READY; never re-home or clear stops."""
    if (backend.board_id, backend.axis_number, backend.point_number) != (0, 1, 0):
        raise ValueError("Motor test is commissioned for board 0, axis 1, point 0 only.")
    code = backend.system_status_code()
    if code not in (backend.READY, backend.RUNNING):
        raise RuntimeError(f"Board status 0x{code:04X}; inspect alarms/SSCNET in Utility2 before retrying.")
    backend.check_all_axes_off()
    if code == backend.READY:
        print("MOTOR TEST: restoring saved Flash parameters...", flush=True)
        backend.load_flash_parameters()
    verify_parameters(backend, {key: value for key, value in expected.items()
                                if key not in SPEED_PARAMETERS})
    if code == backend.RUNNING:
        backend.read_status().check(config, ready=False, idle=True)
    previous_speed = backend.set_speed_limit(config.speed)
    print(f"MOTOR TEST: speed limit {previous_speed} -> {config.speed} count/s (RAM; Flash unchanged).",
          flush=True)
    if code == backend.READY:
        print("MOTOR TEST: parameters verified; starting system with servo OFF...", flush=True)
        backend.start_system()
        # Allow the status image to settle after synchronous SystemStart.
        deadline = time.monotonic() + 5.0
        while True:
            state = backend.read_status()
            try:
                state.check(config, ready=False, idle=True)
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
    backend.read_status().check(config, ready=False, idle=True)
    print("MOTOR TEST: ABS reference and travel verified; ready to arm.", flush=True)
