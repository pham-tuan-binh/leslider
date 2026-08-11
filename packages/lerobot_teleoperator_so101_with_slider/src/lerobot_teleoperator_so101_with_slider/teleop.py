import logging
import os
import sys
import time
from typing import Any

from lerobot.cameras import make_cameras_from_configs
from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.teleoperators.teleoperator import Teleoperator
from lerobot.lerobot_types import RobotAction
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected
from lerobot.utils.import_utils import is_package_available

from .config import SO101WithSliderLeaderConfig

logger = logging.getLogger(__name__)

PYNPUT_AVAILABLE = is_package_available("pynput")
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


class SO101WithSliderLeader(Teleoperator):
    """SO-101 leader: all arm joints (including base) mirror the follower; slider from keyboard.

    - Leader joint positions pass through to the follower, including `shoulder_pan` (base).
    - Left/Right arrow (held): drive `slider.vel`; Up/Down: trim cruise speed; Space (held): stop slider.
    - Optional `cameras` on the teleop config supply frames for `leslider-teleoperate --display_data=true`.

    Action dict matches the follower: six arm `.pos` keys plus `slider.vel`.
    """

    config_class = SO101WithSliderLeaderConfig
    name = "so101_with_slider_leader"

    def __init__(self, config: SO101WithSliderLeaderConfig):
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
        self._cruise = int(config.cruise_velocity)
        self.cameras = make_cameras_from_configs(config.cameras)

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
        if not PYNPUT_AVAILABLE:
            raise RuntimeError(
                "pynput is required for SO101WithSliderLeader (arrow keys drive the slider). "
                "Install with `uv add pynput`."
            )
        self.bus.connect()
        if not self.is_calibrated and calibrate:
            logger.info(
                "Mismatch between calibration values in the motor and the calibration file or no calibration file found"
            )
            self.calibrate()
        self.configure()

        for cam in self.cameras.values():
            cam.connect()

        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        logger.info(
            "%s connected. Leader arm mirrors follower (including base). "
            "Left/Right = slider (cruise=%d), Up/Down = trim speed, Space = slider stop, ESC = quit.%s",
            self,
            self._cruise,
            f" Teleop cameras: {list(self.cameras)}." if self.cameras else "",
        )

    def _on_press(self, key) -> None:
        if key == keyboard.Key.up:
            new = min(self.config.max_velocity, self._cruise + self.config.speed_increment)
            if new != self._cruise:
                self._cruise = new
                logger.info("Slider cruise velocity raised to %d", self._cruise)
        elif key == keyboard.Key.down:
            new = max(self.config.min_velocity, self._cruise - self.config.speed_increment)
            if new != self._cruise:
                self._cruise = new
                logger.info("Slider cruise velocity lowered to %d", self._cruise)
        else:
            self._held.add(key)

    def _on_release(self, key) -> None:
        self._held.discard(key)
        if key == keyboard.Key.esc:
            logger.info("ESC pressed, disconnecting SO-101 slider leader.")
            if self.is_connected:
                self.disconnect()

    def get_visualization_observation(self) -> dict[str, Any]:
        """BGR frames for Rerun when using `leslider-teleoperate --display_data=true`."""
        if not self.cameras:
            return {}
        out: dict[str, Any] = {}
        for name, cam in self.cameras.items():
            frame = cam.read_latest()
            if frame is not None:
                out[f"teleop.{name}"] = frame
        return out

    @check_if_not_connected
    def get_action(self) -> RobotAction:
        start = time.perf_counter()
        leader_pos = self.bus.sync_read("Present_Position")

        action: RobotAction = {
            f"{motor}.pos": float(val) for motor, val in leader_pos.items()
        }

        if keyboard.Key.space in self._held:
            action["slider.vel"] = 0.0
        else:
            direction = -1 if self.config.invert_direction else 1
            velocity = 0
            if keyboard.Key.right in self._held:
                velocity += direction * self._cruise
            if keyboard.Key.left in self._held:
                velocity -= direction * self._cruise
            action["slider.vel"] = float(velocity)

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
        for cam in self.cameras.values():
            try:
                cam.disconnect()
            except Exception:
                logger.exception("Error disconnecting teleop camera.")
        self.bus.disconnect()
        logger.info(f"{self} disconnected.")
