from dataclasses import dataclass

from lerobot.teleoperators.config import TeleoperatorConfig


@TeleoperatorConfig.register_subclass("keyboard_slider_leader")
@dataclass
class KeyboardSliderLeaderConfig(TeleoperatorConfig):
    # Starting raw-tick velocity magnitude sent when an arrow key is held.
    # STS3215 Goal_Velocity is sign-magnitude; positive goes one way, negative the other.
    cruise_velocity: int = 1500

    # Amount added/removed by Up/Down arrows (tapped as speed trim) in ticks/s.
    speed_increment: int = 250

    # Bounds for the interactive speed trim.
    min_velocity: int = 100
    max_velocity: int = 3000

    # If True, invert the Left/Right mapping (useful when the slider is mounted flipped).
    invert_direction: bool = False
