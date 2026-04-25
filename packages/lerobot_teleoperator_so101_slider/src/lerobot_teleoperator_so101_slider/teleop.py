import logging
import os
import sys
import time
from typing import Any

from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.teleoperators.teleoperator import Teleoperator
from lerobot.types import RobotAction
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected
from lerobot.utils.import_utils import _pynput_available

from .config import SO101SliderLeaderConfig

logger = logging.getLogger(__name__)

PYNPUT_AVAILABLE = _pynput_available
keyboard = None
if PYNPUT_AVAILABLE:
    try:
        if ("DISPLAY" not in os.environ) and ("linux" in sys.platform):
            logger.info("No DISPLAY set. Skipping pynput import.")
            PYNPUT_AVAILABLE = False
        else:
            from pynput import keyboard
    except Exception as e:
        PYNPUT_AVAILABLE = False
        logger.info(f"Could not import pynput: {e}")


ARM_MOTORS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")


class SO101SliderLeader(Teleoperator):
    """SO-101 leader where the base joint drives a slider instead of the follower's base.

    The leader's shoulder_pan position is converted to a slider velocity with a
    symmetric dead zone (|pos| <= base_deadzone → vel = 0). The follower's
    shoulder_pan.pos is not taken from the leader — it is a keyboard-set target,
    starting at `follower_base_default` and adjusted by Left/Right arrow keys.
    All other arm joints are passed through from the leader as usual.

    Action dict:
      - shoulder_pan.pos    keyboard-controlled target for follower base
      - shoulder_lift.pos   leader pass-through
      - elbow_flex.pos      leader pass-through
      - wrist_flex.pos      leader pass-through
      - wrist_roll.pos      leader pass-through
      - gripper.pos         leader pass-through
      - slider.vel          derived from leader shoulder_pan with dead zone
    """

    config_class = SO101SliderLeaderConfig
    name = "so101_slider_leader"

    def __init__(self, config: SO101SliderLeaderConfig):
        super().__init__(config)
        self.config = config
        norm_mode = MotorNormMode.DEGREES if config.use_degrees else MotorNormMode.RANGE_M100_100
        self.bus = FeetechMotorsBus(
            port=config.port,
            motors={
                "shoulder_pan": Motor(1, "sts3215", norm_mode),
                "shoulder_lift": Motor(2, "sts3215", norm_mode),
                "elbow_flex": Motor(3, "sts3215", norm_mode),
                "wrist_flex": Motor(4, "sts3215", norm_mode),
                "wrist_roll": Motor(5, "sts3215", norm_mode),
                "gripper": Motor(6, "sts3215", MotorNormMode.RANGE_0_100),
            },
            calibration=self.calibration,
        )
        self._held: set = set()
        self._listener = None
        self._follower_base_pos: float = float(config.follower_base_default)

    @property
    def action_features(self) -> dict[str, type]:
        return {f"{motor}.pos": float for motor in ARM_MOTORS} | {"slider.vel": float}

    @property
    def feedback_features(self) -> dict[str, type]:
        return {}

    @property
    def is_connected(self) -> bool:
        return self.bus.is_connected

    @property
    def is_calibrated(self) -> bool:
        return self.bus.is_calibrated

    def calibrate(self) -> None:
        if self.calibration:
            user_input = input(
                f"Press ENTER to use provided calibration file associated with the id {self.id}, "
                f"or type 'c' and press ENTER to run calibration: "
            )
            if user_input.strip().lower() != "c":
                logger.info(f"Writing calibration file associated with the id {self.id} to the motors")
                self.bus.write_calibration(self.calibration)
                return

        logger.info(f"\nRunning calibration of {self}")
        self.bus.disable_torque()
        for motor in self.bus.motors:
            self.bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)

        input(f"Move {self} to the middle of its range of motion and press ENTER....")
        homing_offsets = self.bus.set_half_turn_homings()

        full_turn_motor = "wrist_roll"
        unknown_range_motors = [m for m in self.bus.motors if m != full_turn_motor]
        print(
            f"Move all joints except '{full_turn_motor}' sequentially through their "
            "entire ranges of motion.\nRecording positions. Press ENTER to stop..."
        )
        range_mins, range_maxes = self.bus.record_ranges_of_motion(unknown_range_motors)
        range_mins[full_turn_motor] = 0
        range_maxes[full_turn_motor] = 4095

        self.calibration = {}
        for motor, m in self.bus.motors.items():
            self.calibration[motor] = MotorCalibration(
                id=m.id,
                drive_mode=0,
                homing_offset=homing_offsets[motor],
                range_min=range_mins[motor],
                range_max=range_maxes[motor],
            )

        self.bus.write_calibration(self.calibration)
        self._save_calibration()
        print(f"Calibration saved to {self.calibration_fpath}")

    def configure(self) -> None:
        self.bus.disable_torque()
        self.bus.configure_motors()
        for motor in self.bus.motors:
            self.bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)

    def setup_motors(self) -> None:
        for motor in reversed(self.bus.motors):
            input(f"Connect the controller board to the '{motor}' motor only and press enter.")
            self.bus.setup_motor(motor)
            print(f"'{motor}' motor id set to {self.bus.motors[motor].id}")

    @check_if_already_connected
    def connect(self, calibrate: bool = True) -> None:
        self.bus.connect()
        if not self.is_calibrated and calibrate:
            logger.info(
                "Mismatch between calibration values in the motor and the calibration file or no calibration file found"
            )
            self.calibrate()
        self.configure()

        if PYNPUT_AVAILABLE:
            self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            self._listener.start()
            logger.info(
                "%s connected. Leader base → slider.vel (dead zone ±%.1f). "
                "Left/Right = trim follower base by %.2f, Space = reset to %.2f, ESC = disconnect.",
                self,
                self.config.base_deadzone,
                self.config.follower_base_increment,
                self.config.follower_base_default,
            )
        else:
            logger.warning(
                "%s connected without pynput — follower base will stay at %.2f.",
                self,
                self._follower_base_pos,
            )

    def _on_press(self, key) -> None:
        if key == keyboard.Key.left:
            new = max(
                self.config.follower_base_min,
                self._follower_base_pos - self.config.follower_base_increment,
            )
            if new != self._follower_base_pos:
                self._follower_base_pos = new
                logger.info("Follower shoulder_pan target = %.2f", self._follower_base_pos)
        elif key == keyboard.Key.right:
            new = min(
                self.config.follower_base_max,
                self._follower_base_pos + self.config.follower_base_increment,
            )
            if new != self._follower_base_pos:
                self._follower_base_pos = new
                logger.info("Follower shoulder_pan target = %.2f", self._follower_base_pos)
        elif key == keyboard.Key.space:
            self._follower_base_pos = float(self.config.follower_base_default)
            logger.info("Follower shoulder_pan target reset to %.2f", self._follower_base_pos)
        else:
            self._held.add(key)

    def _on_release(self, key) -> None:
        self._held.discard(key)
        if key == keyboard.Key.esc:
            logger.info("ESC pressed, disconnecting SO-101 slider leader.")
            if self.is_connected:
                self.disconnect()

    def _base_to_slider_vel(self, base_pos: float) -> float:
        deadzone = self.config.base_deadzone
        if abs(base_pos) <= deadzone:
            return 0.0
        span = max(1e-6, self.config.base_max - deadzone)
        magnitude = min(abs(base_pos) - deadzone, span)
        sign = 1.0 if base_pos > 0 else -1.0
        direction = -1.0 if self.config.invert_direction else 1.0
        return sign * direction * (magnitude / span) * self.config.slider_max_velocity

    @check_if_not_connected
    def get_action(self) -> RobotAction:
        start = time.perf_counter()
        leader_pos = self.bus.sync_read("Present_Position")

        base_pos = float(leader_pos.get("shoulder_pan", 0.0))
        slider_vel = self._base_to_slider_vel(base_pos)

        action: RobotAction = {
            f"{motor}.pos": float(val)
            for motor, val in leader_pos.items()
            if motor != "shoulder_pan"
        }
        action["shoulder_pan.pos"] = float(self._follower_base_pos)
        action["slider.vel"] = float(slider_vel)

        dt_ms = (time.perf_counter() - start) * 1e3
        logger.debug("%s get_action: %.2fms", self, dt_ms)
        return action

    def send_feedback(self, feedback: dict[str, Any]) -> None:
        pass

    @check_if_not_connected
    def disconnect(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self.bus.disconnect()
        logger.info(f"{self} disconnected.")
