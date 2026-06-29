import logging
import time
from functools import cached_property

from lerobot.cameras import make_cameras_from_configs
from lerobot.motors import Motor, MotorCalibration, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus, OperatingMode
from lerobot.robots.robot import Robot
from lerobot.robots.utils import ensure_safe_goal_position
from lerobot.types import RobotAction, RobotObservation
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected

from .config import SO101SliderPosFollowerConfig

logger = logging.getLogger(__name__)

SLIDER = "slider"
ARM_MOTORS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
ALL_MOTORS = (*ARM_MOTORS, SLIDER)

# A Feetech STS3215 enters multi-turn ("extended position") mode when *both* angle
# limits are zero. Present_Position / Goal_Position then span the 15-bit
# sign-magnitude range (~±7 turns) instead of wrapping every revolution, so the
# slider's leadscrew can travel several turns end to end.
MULTITURN_LIMIT = 0


class SO101SliderPosFollower(Robot):
    """SO-101 arm on a linear slider, slider driven in extended (multi-turn) position.

    All seven motors share one Feetech bus and are commanded with normalized
    ``.pos`` actions. The slider's STS3215 runs in position mode with multi-turn
    enabled, so the leadscrew can travel several revolutions without the encoder
    wrapping. The lower bound is a fixed constant (``config.slider_range_min``,
    default the most-negative multi-turn position); calibration only records the
    upper limit as ``range_max``. Software normalization then maps that range onto a
    unified ``slider.pos`` in ``0..100`` (0 = fixed minimum, 100 = upper limit).

    Unlike the velocity-mode `so101_slider_follower`, the action and observation
    dicts here are uniform: every joint (including the slider) is a normalized
    ``.pos`` key.
    """

    config_class = SO101SliderPosFollowerConfig
    name = "so101_slider_pos_follower"

    def __init__(self, config: SO101SliderPosFollowerConfig):
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
                # Slider is a normalized 0..100 position joint: 0 = home, 100 = upper limit.
                SLIDER: Motor(config.slider_id, "sts3215", MotorNormMode.RANGE_0_100),
            },
            calibration=self.calibration,
        )
        self.cameras = make_cameras_from_configs(config.cameras)

    @property
    def _motors_ft(self) -> dict[str, type]:
        ft = {f"{motor}.pos": float for motor in ALL_MOTORS}
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
        return {f"{motor}.pos": float for motor in ALL_MOTORS}

    @property
    def is_connected(self) -> bool:
        return self.bus.is_connected and all(cam.is_connected for cam in self.cameras.values())

    @property
    def is_calibrated(self) -> bool:
        on_motor = self.bus.read_calibration()
        cached = self.calibration
        if set(cached) != set(ALL_MOTORS):
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
        # The slider runs in multi-turn mode (angle limits forced to 0 by
        # configure()), so its on-motor limits never match the recorded travel.
        # We only require that a saved slider calibration exists.
        return SLIDER in cached

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

    def _enable_slider_multiturn(self) -> None:
        """Put the slider in position mode with multi-turn (extended position) on.

        Zeroing both angle limits is what makes the STS3215 report and accept
        positions across multiple turns instead of wrapping at one revolution.
        """
        self.bus.write("Operating_Mode", SLIDER, OperatingMode.POSITION.value)
        self.bus.write("Homing_Offset", SLIDER, 0, normalize=False)
        self.bus.write("Min_Position_Limit", SLIDER, MULTITURN_LIMIT, normalize=False)
        self.bus.write("Max_Position_Limit", SLIDER, MULTITURN_LIMIT, normalize=False)

    def calibrate(self) -> None:
        """Calibrate all seven joints. Arm joints use the stock SO-101 procedure.

        The slider's lower bound is fixed (``config.slider_range_min``), so only its
        upper limit (the "cap") is recorded. The slider is first driven to the fixed
        minimum under torque (``slider.pos = 0``), then torque is released so it can be
        free-rolled by hand to its upper limit (``slider.pos = 100``), which is
        recorded as ``range_max``. Software normalization maps everything in between,
        so control is unified with the arm joints.
        """
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
        for motor in ARM_MOTORS:
            self.bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)
        # Enable multi-turn now so the slider's Present_Position accumulates past
        # one revolution while the user moves it by hand during calibration.
        self._enable_slider_multiturn()

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

        # Slider: the lower bound is a fixed constant (config.slider_range_min), so only
        # the upper limit (the "cap") is calibrated. First drive the slider to that
        # fixed minimum under torque (this is slider.pos = 0), then release torque and
        # free-roll it by hand to its upper limit (slider.pos = 100), recording that
        # encoder value as the cap.
        slider_min = self.config.slider_range_min
        input(
            f"The slider is about to move to its minimum ({slider_min}, slider.pos = 0). "
            "Clear the path and press ENTER to start...."
        )
        self.bus.enable_torque([SLIDER])
        self.bus.write("Goal_Position", SLIDER, slider_min, normalize=False)
        input("Slider moving to its minimum. Wait for it to bottom out, then press ENTER....")
        self.bus.disable_torque([SLIDER])
        input(
            "Torque released. Move the slider by hand to its upper limit "
            "(slider.pos = 100) and press ENTER...."
        )
        upper_raw = int(self.bus.read("Present_Position", SLIDER, normalize=False))
        slider_max = upper_raw
        if slider_max <= slider_min:
            raise ValueError(
                f"Slider upper-limit reading ({slider_max}) is not above the fixed minimum "
                f"({slider_min}). Move the slider toward increasing encoder counts, or adjust "
                "slider_range_min in the config."
            )

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
        self.calibration[SLIDER] = MotorCalibration(
            id=self.bus.motors[SLIDER].id,
            drive_mode=0,
            homing_offset=0,
            range_min=slider_min,
            range_max=slider_max,
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

            # write_calibration() above (in calibrate / connect) writes the slider's
            # recorded travel into the angle-limit registers, which would cap it to a
            # single turn. Re-enable multi-turn here so extended position works.
            self._enable_slider_multiturn()
            self.bus.write("P_Coefficient", SLIDER, 16)
            self.bus.write("I_Coefficient", SLIDER, 0)
            self.bus.write("D_Coefficient", SLIDER, 32)
            if self.config.slider_goal_speed:
                # In position mode Goal_Velocity acts as the travel-speed limit.
                self.bus.write("Goal_Velocity", SLIDER, self.config.slider_goal_speed, normalize=False)

    def setup_motors(self) -> None:
        for motor in reversed(list(self.bus.motors)):
            input(f"Connect the controller board to the '{motor}' motor only and press enter.")
            self.bus.setup_motor(motor)
            print(f"'{motor}' motor id set to {self.bus.motors[motor].id}")

    @check_if_not_connected
    def get_observation(self) -> RobotObservation:
        start = time.perf_counter()
        pos = self.bus.sync_read("Present_Position", list(ALL_MOTORS))
        obs_dict = {f"{motor}.pos": val for motor, val in pos.items()}

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
            key.removesuffix(".pos"): val for key, val in action.items() if key.endswith(".pos")
        }

        if self.config.max_relative_target is not None and goal_pos:
            present_pos = self.bus.sync_read("Present_Position", list(goal_pos))
            goal_present_pos = {k: (g, present_pos[k]) for k, g in goal_pos.items()}
            goal_pos = ensure_safe_goal_position(goal_present_pos, self.config.max_relative_target)

        if goal_pos:
            self.bus.sync_write("Goal_Position", goal_pos)

        return {f"{m}.pos": v for m, v in goal_pos.items()}

    @check_if_not_connected
    def disconnect(self):
        self.bus.disconnect(self.config.disable_torque_on_disconnect)
        for cam in self.cameras.values():
            cam.disconnect()
        logger.info(f"{self} disconnected.")
