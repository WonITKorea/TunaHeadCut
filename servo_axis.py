"""Single-axis normalized absolute positioning, independent of the PCIe DLL."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
from threading import Lock
import time
from typing import Protocol


def integer_in_range(name: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}].")


def normalized_value(value: float | str) -> float:
    if isinstance(value, bool):
        raise ValueError("Input must be a finite number between 0 and 1.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Input must be a finite number between 0 and 1.") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError("Input must be a finite number between 0 and 1.")
    return number


@dataclass(frozen=True)
class AxisConfig:
    # These are board feedback/command units, not necessarily raw encoder pulses.
    min_position_counts: int
    max_position_counts: int
    speed: int  # Board speed units, as configured in Position Board Utility2.
    acceleration_ms: int = 500
    deceleration_ms: int = 500
    position_tolerance_counts: int = 10
    move_timeout_s: float = 30.0
    poll_interval_s: float = 0.02
    reverse_direction: bool = False  # Input 0 starts at max; input 1 ends at min.

    def __post_init__(self) -> None:
        for name in ("min_position_counts", "max_position_counts"):
            integer_in_range(name, getattr(self, name), -(2**31), 2**31 - 1)
        if self.min_position_counts >= self.max_position_counts:
            raise ValueError("min_position_counts must be below max_position_counts.")
        if type(self.reverse_direction) is not bool:
            raise ValueError("reverse_direction must be a boolean.")
        integer_in_range("speed", self.speed, 1, 2**31 - 1)
        for name in ("acceleration_ms", "deceleration_ms"):
            integer_in_range(name, getattr(self, name), 1, 20_000)
        integer_in_range("position_tolerance_counts", self.position_tolerance_counts,
                         0, self.max_position_counts - self.min_position_counts - 1)
        for name in ("move_timeout_s", "poll_interval_s"):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be a positive finite number.")
        if self.poll_interval_s > min(1.0, self.move_timeout_s):
            raise ValueError("poll_interval_s must be <= 1 s and <= move_timeout_s.")

    @classmethod
    def from_file(cls, path: str | Path) -> "AxisConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Axis configuration must be a JSON object.")
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValueError(f"Invalid axis configuration: {exc}") from exc

    def target_counts(self, value: float | str) -> int:
        number = normalized_value(value)
        # Round to the nearest board unit (Python's ties-to-even rule).
        start = self.max_position_counts if self.reverse_direction else self.min_position_counts
        end = self.min_position_counts if self.reverse_direction else self.max_position_counts
        return round(start + number * (end - start))


@dataclass(frozen=True)
class AxisStatus:
    position_counts: int
    system_running: bool
    servo_ready: bool
    operating: bool
    in_position: bool
    operation_complete: bool
    home_required: bool = False
    encoder_error: bool = False
    servo_alarm: bool = False
    operation_alarm: bool = False
    servo_warning: bool = False
    forced_stop: bool = False
    interlock_stop: bool = False

    def check(self, config: AxisConfig, *, ready: bool = True,
              idle: bool = False) -> None:
        problems = [name for name in (
            "home_required", "encoder_error", "servo_alarm", "operation_alarm",
            "servo_warning", "forced_stop", "interlock_stop") if getattr(self, name)]
        if not self.system_running:
            problems.append("system is not RUNNING; prepare the one-axis project first")
        if ready and not self.servo_ready:
            problems.append("servo is not ready")
        if idle and self.operating:
            problems.append("axis is already operating")
        tolerance = config.position_tolerance_counts
        if not (config.min_position_counts - tolerance <= self.position_counts
                <= config.max_position_counts + tolerance):
            problems.append("feedback position is outside the configured travel range")
        if problems:
            raise RuntimeError("Axis unavailable: " + ", ".join(problems))


class AxisBackend(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def read_status(self) -> AxisStatus: ...
    def set_servo(self, enabled: bool) -> None: ...
    def start_absolute(self, target: int, config: AxisConfig) -> None: ...
    def stop(self) -> None: ...


class NormalizedAxis:
    """Blocking point-to-point moves. One instance must exclusively own the axis."""

    def __init__(self, backend: AxisBackend, config: AxisConfig):
        self.backend = backend
        self.config = config
        self._connected = False
        self._armed = False
        self._servo_owned = False
        self._faulted = False
        self._lock = Lock()

    def connect(self) -> None:
        if not self._connected:
            self.backend.open()
            self._connected = True

    def status(self) -> AxisStatus:
        if not self._connected:
            raise RuntimeError("Connect the axis first.")
        return self.backend.read_status()

    def arm(self) -> None:
        """Explicitly enable servo torque; never changes the position reference."""
        if self._faulted:
            raise RuntimeError("Previous operation failed; close and create a new session.")
        if self._armed:
            return
        state = self.status()
        state.check(self.config, ready=False, idle=True)
        # Preserve a servo that was already ON before this session.
        self._servo_owned = not state.servo_ready
        try:
            if self._servo_owned:
                self.backend.set_servo(True)
            deadline = time.monotonic() + 5.0
            while True:
                state = self.status()
                state.check(self.config, ready=False, idle=True)
                if state.servo_ready:
                    self._armed = True
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("Servo ON did not reach READY within 5 s.")
                time.sleep(self.config.poll_interval_s)
        except BaseException:
            self._faulted = True
            # Keep ownership on failure so close() will retry cleanup.
            raise

    def move_normalized(self, value: float | str) -> int:
        target = self.config.target_counts(value)  # Reject bad input before any I/O.
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A move is already in progress.")
        try:
            if not self._armed or self._faulted:
                raise RuntimeError("Arm a healthy axis before moving.")
            # Status reads may fail while a prior command remains active.
            try:
                state = self.status()
                state.check(self.config, idle=True)
                if (abs(state.position_counts - target) <= self.config.position_tolerance_counts
                        and state.in_position and state.operation_complete):
                    return state.position_counts
                self.backend.start_absolute(target, self.config)
                deadline = time.monotonic() + self.config.move_timeout_s
                while True:
                    state = self.status()
                    state.check(self.config)
                    if (not state.operating and state.operation_complete and state.in_position
                            and abs(state.position_counts - target)
                            <= self.config.position_tolerance_counts):
                        return state.position_counts
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Move to {target} did not finish in time.")
                    time.sleep(self.config.poll_interval_s)
            except BaseException as exc:
                self._faulted = True
                self._armed = False
                try:
                    self.backend.stop()
                except BaseException as stop_error:
                    raise RuntimeError(
                        f"Move failed ({exc}); stop could not be confirmed ({stop_error})."
                    ) from exc
                raise
        finally:
            self._lock.release()

    def close(self) -> None:
        if not self._connected:
            return
        # Do not discard the connection if stop or servo OFF fails: allow retry.
        if self._armed or self._faulted or self._servo_owned:
            self.backend.stop()
        if self._servo_owned:
            self.backend.set_servo(False)
            deadline = time.monotonic() + 5.0
            while self.backend.read_status().servo_ready:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Servo OFF did not clear READY within 5 s.")
                time.sleep(self.config.poll_interval_s)
            self._servo_owned = False
        self.backend.close()
        self._connected = False
        self._armed = False

    def __enter__(self) -> "NormalizedAxis":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self.close()
        except BaseException as cleanup_error:
            if exc is not None:
                raise RuntimeError(f"{exc}; cleanup also failed: {cleanup_error}") from exc
            raise


class SimulatedBackend:
    """Instantaneous positioning for input/API checks; no motor dynamics model."""

    def __init__(self, initial_position: int = 0):
        self.position = initial_position
        self.ready = False
        self.opened = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def read_status(self) -> AxisStatus:
        if not self.opened:
            raise RuntimeError("Simulator is closed.")
        return AxisStatus(self.position, True, self.ready, False, True, True)

    def set_servo(self, enabled: bool) -> None:
        self.ready = enabled

    def start_absolute(self, target: int, config: AxisConfig) -> None:
        if not self.opened or not self.ready:
            raise RuntimeError("Simulator is not ready.")
        self.position = target

    def stop(self) -> None:
        pass
