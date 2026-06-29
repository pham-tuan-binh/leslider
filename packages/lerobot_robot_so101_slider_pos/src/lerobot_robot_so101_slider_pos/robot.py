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
# limits are zero *and* bit 4 of the Phase register is set. Present_Position /
# Goal_Position then span the 15-bit sign-magnitude range (~±7 turns) instead of
# wrapping every revolution, so the slider's leadscrew can travel several turns end
# to end. Zeroing the limits alone is not enough: without the phase bit the servo
# stays single-turn and Present_Position wraps at 4095. This mode is not retained
# across power cycles, so it must be re-applied on every connect (see configure()).
MULTITURN_LIMIT = 0
MULTITURN_PHASE_BIT = 0x10


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

        Zeroing both angle limits *and* setting bit 4 of the Phase register is what
        makes the STS3215 report and accept positions across multiple turns instead
        of wrapping at one revolution. The phase bit is the easily-missed half:
        without it the servo stays single-turn and Present_Position wraps at 4095.
        """
        self.bus.write("Operating_Mode", SLIDER, OperatingMode.POSITION.value)
        self.bus.write("Homing_Offset", SLIDER, 0, normalize=False)
        self.bus.write("Min_Position_Limit", SLIDER, MULTITURN_LIMIT, normalize=False)
        self.bus.write("Max_Position_Limit", SLIDER, MULTITURN_LIMIT, normalize=False)
        phase = int(self.bus.read("Phase", SLIDER, normalize=False))
        self.bus.write("Phase", SLIDER, phase | MULTITURN_PHASE_BIT, normalize=False)

    def _write_calibration(self, calibration: dict[str, MotorCalibration]) -> None:
        """Write calibration to the bus without clobbering the slider's mode.

        The stock ``bus.write_calibration`` pushes every motor's ``range_min`` /
        ``range_max`` into its ``Min/Max_Position_Limit`` registers. That is wrong
        for the slider on two counts: it runs in multi-turn (extended-position)
        mode, where both limits must be ``0``, and its recorded ``range_min`` is a
        negative multi-turn tick (``config.slider_range_min``) that the Feetech
        serializer rejects outright ("Negative values are not allowed").

        So we write only the arm motors' limits to the bus and leave the slider's
        angle-limit registers to ``_enable_slider_multiturn()`` (called from
        ``configure()``). The full dict — slider included — is still cached on the
        bus so software normalization of ``slider.pos`` uses the recorded travel.
        """
        arm_only = {m: c for m, c in calibration.items() if m != SLIDER}
        self.bus.write_calibration(arm_only, cache=False)
        self.bus.calibration = dict(calibration)

    def _calibrate_arm(self) -> dict[str, MotorCalibration]:
        """Run the stock SO-101 arm calibration and return its calibration dict."""
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

        arm_calibration: dict[str, MotorCalibration] = {}
        for motor in ARM_MOTORS:
            m = self.bus.motors[motor]
            arm_calibration[motor] = MotorCalibration(
                id=m.id,
                drive_mode=0,
                homing_offset=homing_offsets[motor],
                range_min=range_mins[motor],
                range_max=range_maxes[motor],
            )
        return arm_calibration

    def _calibrate_slider(self) -> MotorCalibration:
        """Calibrate the slider's upper limit and return its calibration entry.

        The lower bound is a fixed constant (``config.slider_range_min``), so only
        the upper limit (the "cap") is calibrated. First drive the slider to that
        fixed minimum under torque (``slider.pos = 0``), then release torque and
        free-roll it by hand to its upper limit (``slider.pos = 100``), recording
        that encoder value as the cap.
        """
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
        slider_max = int(self.bus.read("Present_Position", SLIDER, normalize=False))
        if slider_max <= slider_min:
            raise ValueError(
                f"Slider upper-limit reading ({slider_max}) is not above the fixed minimum "
                f"({slider_min}). Move the slider toward increasing encoder counts, or adjust "
                "slider_range_min in the config."
            )
        return MotorCalibration(
            id=self.bus.motors[SLIDER].id,
            drive_mode=0,
            homing_offset=0,
            range_min=slider_min,
            range_max=slider_max,
        )

    def calibrate(self) -> None:
        """Calibrate all seven joints. Arm joints use the stock SO-101 procedure.

        The arm and slider are calibrated as two independent phases, and the arm
        calibration is written to disk as soon as it is collected. If the slider
        phase later fails (or is interrupted), the arm calibration survives: the
        next run detects the saved arm-only calibration and offers to reuse it and
        calibrate only the slider, so the arm sweep never has to be redone.
        """
        have_arm = all(m in self.calibration for m in ARM_MOTORS)
        have_slider = SLIDER in self.calibration

        if have_arm and have_slider:
            user_input = input(
                f"Press ENTER to use provided calibration file associated with the id {self.id}, "
                f"or type 'c' and press ENTER to run calibration: "
            )
            if user_input.strip().lower() != "c":
                logger.info(f"Writing calibration file associated with the id {self.id} to the motors")
                self._write_calibration(self.calibration)
                return

        # A saved arm calibration with no slider means a prior run was interrupted
        # after the arm phase (e.g. the slider step failed). Offer to keep it.
        reuse_arm = False
        if have_arm and not have_slider:
            user_input = input(
                "Found a saved arm calibration but no slider calibration "
                "(an earlier run was likely interrupted). Press ENTER to keep the "
                "arm calibration and calibrate only the slider, or type 'a' and "
                "press ENTER to recalibrate the arm as well: "
            )
            reuse_arm = user_input.strip().lower() != "a"

        logger.info(f"\nRunning calibration of {self}")
        self.bus.disable_torque()
        for motor in ARM_MOTORS:
            self.bus.write("Operating_Mode", motor, OperatingMode.POSITION.value)
        # Enable multi-turn now so the slider's Present_Position accumulates past
        # one revolution while the user moves it by hand during calibration.
        self._enable_slider_multiturn()

        if reuse_arm:
            arm_calibration = {m: self.calibration[m] for m in ARM_MOTORS}
            logger.info("Reusing saved arm calibration; calibrating slider only.")
        else:
            arm_calibration = self._calibrate_arm()
            # Persist the arm calibration immediately so a slider failure or
            # interruption never forces redoing the arm sweep.
            self.calibration = dict(arm_calibration)
            self._write_calibration(self.calibration)
            self._save_calibration()
            logger.info(f"Arm calibration saved to {self.calibration_fpath}")

        self.calibration = dict(arm_calibration)
        self.calibration[SLIDER] = self._calibrate_slider()
        self._write_calibration(self.calibration)
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
