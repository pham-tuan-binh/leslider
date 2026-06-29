from dataclasses import dataclass

from lerobot.robots.config import RobotConfig
from lerobot.robots.so_follower.config_so_follower import SOFollowerConfig


@RobotConfig.register_subclass("so101_slider_pos_follower")
@dataclass
class SO101SliderPosFollowerConfig(RobotConfig, SOFollowerConfig):
    # Feetech ID for the extra STS3215 driving the slider.
    # SO-101 uses IDs 1..6, so 7 is the lowest free ID on the same bus.
    slider_id: int = 7

    # Fixed lower bound (raw multi-turn ticks) of the slider's normalized range.
    # Only the upper limit is calibrated; this is the constant `range_min` used for
    # software normalization. Default sits near the most-negative multi-turn position
    # the STS3215 can represent, so slider.pos = 0 maps to the full-retract extreme
    # and slider.pos = 100 maps to the calibrated upper limit.
    slider_range_min: int = -28762

    # Optional speed cap (raw ticks/s) for the slider in position mode, written to
    # Goal_Velocity. 0 (default) keeps the STS3215 default of moving to the goal at
    # full speed. Set a positive value to slow the slider's travel.
    slider_goal_speed: int = 0

    # When True, adds `{motor}.current` (raw mA from Present_Current) for every
    # motor to each observation. Disabled by default to avoid extra bus reads.
    read_current: bool = False
