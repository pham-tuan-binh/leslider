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

    # Travel-speed cap (raw ticks/s) for the slider in position mode, written to
    # Goal_Velocity. 0 disables the cap and moves at full speed -- which is slower,
    # not faster: uncapped moves command full PWM, peg Present_Load at 1000, and trip
    # the servo's overload protection after ~2s, dropping torque to 20% for the rest
    # of the move. Measured over a 5.7-turn stroke: 2000 -> 11.9s (load ~670, clean),
    # 2400 -> 10.0s (load ~790, no margin), 2800 and uncapped -> trip at ~2s, 38s.
    slider_goal_speed: int = 2000

    # When True, adds `{motor}.current` (raw mA from Present_Current) for every
    # motor to each observation. Disabled by default to avoid extra bus reads.
    read_current: bool = False
