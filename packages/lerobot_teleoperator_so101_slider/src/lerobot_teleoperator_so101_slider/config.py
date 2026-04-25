from dataclasses import dataclass

from lerobot.teleoperators.config import TeleoperatorConfig


@dataclass
class SO101SliderLeaderBaseConfig:
    # Serial port of the SO-101 leader arm (shared by all six arm motors).
    port: str

    # Leader position units. True → degrees; False → normalized RANGE_M100_100
    # (gripper is always 0..100). `base_deadzone` and `base_max` use the same unit.
    use_degrees: bool = True

    # Dead zone around 0 for the leader's shoulder_pan (base) joint. When
    # |shoulder_pan| <= base_deadzone the slider is commanded to zero velocity.
    base_deadzone: float = 20.0

    # |shoulder_pan| at which slider velocity saturates to ±slider_max_velocity.
    # Larger magnitudes are clamped to the saturation velocity.
    base_max: float = 90.0

    # Raw-tick velocity cap emitted on slider.vel (the follower also clamps).
    slider_max_velocity: int = 3000

    # Swap slider direction relative to leader base sign.
    invert_direction: bool = False

    # Default follower shoulder_pan.pos target used before any keyboard input.
    follower_base_default: float = 0.0

    # Step applied per Left/Right key press to the follower base target,
    # in the follower's own unit (degrees if follower.use_degrees else RANGE_M100_100).
    follower_base_increment: float = 5.0

    # Clamp range for the follower base target.
    follower_base_min: float = -100.0
    follower_base_max: float = 100.0


@TeleoperatorConfig.register_subclass("so101_slider_leader")
@dataclass
class SO101SliderLeaderConfig(TeleoperatorConfig, SO101SliderLeaderBaseConfig):
    pass
