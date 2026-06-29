from dataclasses import dataclass, field

from lerobot.cameras import CameraConfig
from lerobot.teleoperators.config import TeleoperatorConfig


@dataclass
class SO101WithSliderPosLeaderBaseConfig:
    # Serial port of the SO-101 leader arm (shared by all six arm motors).
    port: str

    # Leader position units. True for degrees; False for normalized RANGE_M100_100
    # (gripper is always 0..100).
    use_degrees: bool = True

    # Optional USB / ZMQ / RealSense cameras mounted on the leader side. When you run
    # `leslider-teleoperate --display_data=true`, frames are logged under
    # `observation.teleop.<name>` in Rerun alongside the follower's cameras.
    cameras: dict[str, CameraConfig] = field(default_factory=dict)

    # --- Slider keyboard controls (position target, matches the unified slider.pos) ---
    # The follower's slider is a normalized 0..100 position joint, so the keyboard
    # integrates a target: holding Left/Right ramps slider.pos by `slider_step` units
    # per get_action() call (called once per teleop loop ~= per frame).
    slider_step: float = 1.0

    # Amount Up/Down arrows add/remove from `slider_step` per key press.
    step_increment: float = 0.25

    # Bounds for the interactive step trim.
    min_step: float = 0.1
    max_step: float = 5.0

    # Starting slider target on connect (0 = home, 100 = upper limit).
    initial_position: float = 0.0

    # If True, invert the Left/Right mapping for the slider.
    invert_direction: bool = False


@TeleoperatorConfig.register_subclass("so101_with_slider_pos_leader")
@dataclass
class SO101WithSliderPosLeaderConfig(TeleoperatorConfig, SO101WithSliderPosLeaderBaseConfig):
    pass
