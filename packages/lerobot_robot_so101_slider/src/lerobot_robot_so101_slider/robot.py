import logging
import time
from functools import cached_property

from lerobot.cameras import make_cameras_from_configs
from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.robots.robot import Robot
from lerobot.robots.utils import ensure_safe_goal_position
from lerobot.lerobot_types import RobotAction, RobotObservation
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected

from .config import SO101SliderFollowerConfig

logger = logging.getLogger(__name__)

SLIDER = "slider"
ARM_MOTORS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
ALL_MOTORS = (*ARM_MOTORS, SLIDER)


class SO101SliderFollower(Robot):
    """SO-101 arm mounted on a linear slider driven by an extra STS3215.

    All seven motors share one Feetech bus. The six arm motors behave exactly
    like a stock SO-101 (position mode, normalized .pos actions). The slider
    motor is placed in wheel/velocity mode so it can turn continuously; it
    accepts raw tick/s velocity commands via the `slider.vel` action key.
    """

    config_class = SO101SliderFollowerConfig
    name = "so101_slider_follower"

    def __init__(self, config: SO101SliderFollowerConfig):
        super().__init__(config)
        self.config = config

        if config.slider_id in range(1, 7):
            raise ValueError(
                f"slider_id={config.slider_id} collides with an SO-101 arm motor (IDs 1..6)."
            )

        arm_norm = MotorNormMode.DEGREES if config.use_degrees else MotorNormMode.RANGE_M100_100
        self.bus = FeetechMotorsBus(
            port=self.config.port,
            motors={
                "shoulder_pan": Motor(1, "sts3215", arm_norm),
                "shoulder_lift": Motor(2, "sts3215", arm_norm),
                "elbow_flex": Motor(3, "sts3215", arm_norm),
                "wrist_flex": Motor(4, "sts3215", arm_norm),
                "wrist_roll": Motor(5, "sts3215", arm_norm),
                "gripper": Motor(6, "sts3215", MotorNormMode.RANGE_0_100),
                # Slider is commanded by raw velocity; norm_mode is unused because
                # Goal_Velocity/Present_Velocity are not in the normalized-register list.
                SLIDER: Motor(config.slider_id, "sts3215", MotorNormMode.RANGE_M100_100),
            },
            calibration=self.calibration,
        )
        self.cameras = make_cameras_from_configs(config.cameras)

    @property
    def _motors_ft(self) -> dict[str, type]:
        ft = {f"{motor}.pos": float for motor in ARM_MOTORS}
        # Slider is in wheel/velocity mode; Present_Position wraps and is not
        # meaningful as an absolute position, so only velocity is reported.
        ft[f"{SLIDER}.vel"] = float  # raw ticks/s from Present_Velocity
        if self.config.read_current:
            for motor in ALL_MOTORS:
                ft[f"{motor}.current"] = float  # raw mA, read from Present_Current
        return ft

    @property
    def _cameras_ft(self) -> dict[str, tuple]:
        return {
            cam: (self.config.cameras[cam].height, self.config.cameras[cam].width, 3)
            for cam in self.cameras
        }

    @cached_property
    def observation_features(self) -> dict[str, type | tuple]:
        return {**self._motors_ft, **self._cameras_ft}

    @cached_property
    def action_features(self) -> dict[str, type]:
        return {f"{motor}.pos": float for motor in ARM_MOTORS} | {f"{SLIDER}.vel": float}

    @property
    def is_connected(self) -> bool:
        return self.bus.is_connected and all(cam.is_connected for cam in self.cameras.values())

    @property
    def is_calibrated(self) -> bool:
        # Only the arm joints are calibrated. The slider is a continuous-rotation
        # motor driven in velocity mode, so its position calibration is meaningless;
        # we skip it instead of writing synthetic limits.
        on_motor = self.bus.read_calibration()
        cached = self.calibration
        if set(cached) != set(ARM_MOTORS):
            return False
        for motor in ARM_MOTORS:
            if motor not in on_motor:
                return False
            if (
                cached[motor].range_min != on_motor[motor].range_min
                or cached[motor].range_max != on_motor[motor].range_max
                or cached[motor].homing_offset != on_motor[motor].homing_offset
            ):
                return False
        return True

    @check_if_already_connected
    def connect(self, calibrate: bool = True) -> None:
        self.bus.connect()
        if not self.is_calibrated and calibrate:
            logger.info(
                "Mismatch between calibration values in the motor and the calibration file or no calibration file found"
            )
            self.calibrate()

        for cam in self.cameras.values():
            cam.connect()

        self.configure()
        logger.info(f"{self} connected.")

    def calibrate(self) -> None:
        """Calibrate the arm joints the same way as SO-101. The slider motor is
        continuous-rotation so we skip homing/range discovery for it and write
        a full-range pass-through calibration instead."""
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
        # Only put the arm in position mode for calibration; the slider stays as-is
        # (configure() will set it to velocity mode afterwards).
        for motor in ARM_MOTORS:
            self.bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)

        input(f"Move {self} arm to the middle of its range of motion and press ENTER....")
        homing_offsets = self.bus.set_half_turn_homings(list(ARM_MOTORS))

        full_turn_motor = "wrist_roll"
        unknown_range_motors = [m for m in ARM_MOTORS if m != full_turn_motor]
        print(
            f"Move all arm joints except '{full_turn_motor}' sequentially through their "
            "entire ranges of motion.\nRecording positions. Press ENTER to stop..."
        )
        range_mins, range_maxes = self.bus.record_ranges_of_motion(unknown_range_motors)
        range_mins[full_turn_motor] = 0
        range_maxes[full_turn_motor] = 4095

        self.calibration = {}
        for motor in ARM_MOTORS:
            m = self.bus.motors[motor]
            self.calibration[motor] = MotorCalibration(
                id=m.id,
                drive_mode=0,
                homing_offset=homing_offsets[motor],
                range_min=range_mins[motor],
                range_max=range_maxes[motor],
            )

        self.bus.write_calibration(self.calibration)
        self._save_calibration()
        print("Calibration saved to", self.calibration_fpath)

    def configure(self) -> None:
        with self.bus.torque_disabled():
            self.bus.configure_motors()
            for motor in ARM_MOTORS:
                self.bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)
                self.bus.write("P_Coefficient", motor, 16)
                self.bus.write("I_Coefficient", motor, 0)
                self.bus.write("D_Coefficient", motor, 32)

                if motor == "gripper":
                    self.bus.write("Max_Torque_Limit", motor, 500)
                    self.bus.write("Protection_Current", motor, 250)
                    self.bus.write("Overload_Torque", motor, 25)

            # Slider runs in velocity (wheel) mode so it can spin continuously.
            self.bus.write("Operating_Mode", SLIDER, OperatingMode.VELOCITY.value)
            # Make sure we start from zero velocity before torque is re-enabled.
            self.bus.write("Goal_Velocity", SLIDER, 0, normalize=False)

    def setup_motors(self) -> None:
        for motor in reversed(list(self.bus.motors)):
            input(f"Connect the controller board to the '{motor}' motor only and press enter.")
            self.bus.setup_motor(motor)
            print(f"'{motor}' motor id set to {self.bus.motors[motor].id}")

    @check_if_not_connected
    def get_observation(self) -> RobotObservation:
        start = time.perf_counter()
        arm_pos = self.bus.sync_read("Present_Position", list(ARM_MOTORS))
        obs_dict = {f"{motor}.pos": val for motor, val in arm_pos.items()}

        slider_vel = self.bus.read("Present_Velocity", SLIDER, normalize=False)
        obs_dict[f"{SLIDER}.vel"] = float(slider_vel)

        if self.config.read_current:
            currents = self.bus.sync_read("Present_Current", list(ALL_MOTORS), normalize=False)
            for motor, val in currents.items():
                obs_dict[f"{motor}.current"] = float(val)

        dt_ms = (time.perf_counter() - start) * 1e3
        logger.debug(f"{self} read state: {dt_ms:.1f}ms")

        for cam_key, cam in self.cameras.items():
            start = time.perf_counter()
            obs_dict[cam_key] = cam.read_latest()
            dt_ms = (time.perf_counter() - start) * 1e3
            logger.debug(f"{self} read {cam_key}: {dt_ms:.1f}ms")

        return obs_dict

    @check_if_not_connected
    def send_action(self, action: RobotAction) -> RobotAction:
        goal_pos = {
            key.removesuffix(".pos"): val
            for key, val in action.items()
            if key.endswith(".pos") and not key.startswith(f"{SLIDER}.")
        }

        if self.config.max_relative_target is not None and goal_pos:
            present_pos = self.bus.sync_read("Present_Position", list(goal_pos))
            goal_present_pos = {k: (g, present_pos[k]) for k, g in goal_pos.items()}
            goal_pos = ensure_safe_goal_position(goal_present_pos, self.config.max_relative_target)

        if goal_pos:
            self.bus.sync_write("Goal_Position", goal_pos)

        sent: RobotAction = {f"{m}.pos": v for m, v in goal_pos.items()}

        if f"{SLIDER}.vel" in action:
            vel = int(action[f"{SLIDER}.vel"])
            cap = self.config.slider_max_velocity
            vel = max(-cap, min(cap, vel))
            self.bus.write("Goal_Velocity", SLIDER, vel, normalize=False)
            sent[f"{SLIDER}.vel"] = float(vel)

        return sent

    @check_if_not_connected
    def disconnect(self):
        # Stop the slider before the arm drops torque so it doesn't coast on shutdown.
        try:
            self.bus.write("Goal_Velocity", SLIDER, 0, normalize=False)
        except Exception:
            logger.exception("Failed to zero slider velocity on disconnect.")

        self.bus.disconnect(self.config.disable_torque_on_disconnect)
        for cam in self.cameras.values():
            cam.disconnect()
        logger.info(f"{self} disconnected.")
