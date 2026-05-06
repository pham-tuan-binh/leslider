from dataclasses import dataclass

from lerobot.robots.config import RobotConfig
from lerobot.robots.so_follower.config_so_follower import SOFollowerConfig


@RobotConfig.register_subclass("so101_slider_follower")
@dataclass
class SO101SliderFollowerConfig(RobotConfig, SOFollowerConfig):
    # Feetech ID for the extra STS3215 driving the slider.
    # SO-101 uses IDs 1..6, so 7 is the lowest free ID on the same bus.
    slider_id: int = 7

    # Raw-tick velocity cap applied to slider.vel before sync_write. The STS3215
    # Goal_Velocity register is sign-magnitude in 12-bit resolution steps per
    # second; 3000 is roughly the comfortable upper bound.
    slider_max_velocity: int = 3000

    # When True, adds `{motor}.current` (raw mA from Present_Current) for every
    # motor to each observation. Disabled by default to avoid extra bus reads.
    read_current: bool = False
