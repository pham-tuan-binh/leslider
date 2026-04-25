from dataclasses import dataclass, field

from lerobot.cameras import CameraConfig
from lerobot.teleoperators.config import TeleoperatorConfig


@dataclass
class SO101WithSliderLeaderBaseConfig:
    # Serial port of the SO-101 leader arm (shared by all six arm motors).
    port: str

    # Leader position units. True for degrees; False for normalized RANGE_M100_100
    # (gripper is always 0..100).
    use_degrees: bool = True

    # Optional USB / ZMQ / RealSense cameras mounted on the leader side. When you run
    # `leslider-teleoperate --display_data=true`, frames are logged under
    # `observation.teleop.<name>` in Rerun alongside the follower's cameras.
    cameras: dict[str, CameraConfig] = field(default_factory=dict)

    # --- Slider keyboard controls (same semantics as the former keyboard-only teleop) ---
    # Raw-tick velocity magnitude when Left/Right is held (STS3215 Goal_Velocity scale).
    cruise_velocity: int = 1500

    # Amount added/removed by Up/Down arrows (per key press) in ticks/s.
    speed_increment: int = 250

    # Bounds for the interactive speed trim.
    min_velocity: int = 100
    max_velocity: int = 3000

    # If True, invert the Left/Right mapping for slider.vel.
    invert_direction: bool = False


@TeleoperatorConfig.register_subclass("so101_with_slider_leader")
@dataclass
class SO101WithSliderLeaderConfig(TeleoperatorConfig, SO101WithSliderLeaderBaseConfig):
    pass
