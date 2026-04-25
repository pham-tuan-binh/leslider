import logging
import os
import sys
import time
from typing import Any

from lerobot.teleoperators.teleoperator import Teleoperator
from lerobot.types import RobotAction
from lerobot.utils.decorators import check_if_already_connected, check_if_not_connected
from lerobot.utils.import_utils import is_package_available

from .config import KeyboardSliderLeaderConfig

logger = logging.getLogger(__name__)

PYNPUT_AVAILABLE = is_package_available("pynput")
keyboard = None
if PYNPUT_AVAILABLE:
    try:
        if ("DISPLAY" not in os.environ) and ("linux" in sys.platform):
            logger.info("No DISPLAY set. Skipping pynput import.")
            PYNPUT_AVAILABLE = False
        else:
            from pynput import keyboard
    except Exception as e:
        PYNPUT_AVAILABLE = False
        logger.info(f"Could not import pynput: {e}")


class KeyboardSliderLeader(Teleoperator):
    """Arrow-key teleop that emits slider.vel velocity commands.

    Controls:
        - Left / Right arrow: drive the slider in one direction / the other.
        - Up / Down arrow: trim the cruise velocity up / down (per key press).
        - Space: emergency stop (zero velocity while held).
        - ESC: disconnect the listener.

    The action dict is `{"slider.vel": <signed int ticks/s>}`, matching the
    SO-101-with-slider follower's expected action key.
    """

    config_class = KeyboardSliderLeaderConfig
    name = "keyboard_slider_leader"

    def __init__(self, config: KeyboardSliderLeaderConfig):
        if not is_package_available("pynput"):
            raise ImportError(
                "pynput is required for KeyboardSliderLeader. Install it with"
                " `uv add pynput` or add it to your environment."
            )
        super().__init__(config)
        self.config = config
        # Modified from the pynput listener thread; set add/discard is GIL-atomic.
        self._held: set = set()
        self._listener = None
        self._cruise = int(config.cruise_velocity)

    @property
    def action_features(self) -> dict[str, type]:
        return {"slider.vel": float}

    @property
    def feedback_features(self) -> dict:
        return {}

    @property
    def is_connected(self) -> bool:
        return (
            PYNPUT_AVAILABLE
            and isinstance(self._listener, keyboard.Listener)
            and self._listener.is_alive()
        )

    @property
    def is_calibrated(self) -> bool:
        return True

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    @check_if_already_connected
    def connect(self, calibrate: bool = True) -> None:  # noqa: ARG002 (match base signature)
        if not PYNPUT_AVAILABLE:
            raise RuntimeError(
                "pynput is not available in this environment; cannot run the keyboard leader."
            )
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        logger.info(
            "%s connected. Left/Right = drive, Up/Down = trim speed (cruise=%d), Space = stop, ESC = quit.",
            self,
            self._cruise,
        )

    def _on_press(self, key) -> None:
        # Up/Down are handled as edge-triggered speed trim so a short tap isn't
        # lost between `get_action()` calls and so a held key advances the trim
        # at the OS key-repeat rate.
        if key == keyboard.Key.up:
            new = min(self.config.max_velocity, self._cruise + self.config.speed_increment)
            if new != self._cruise:
                self._cruise = new
                logger.info("Slider cruise velocity raised to %d", self._cruise)
        elif key == keyboard.Key.down:
            new = max(self.config.min_velocity, self._cruise - self.config.speed_increment)
            if new != self._cruise:
                self._cruise = new
                logger.info("Slider cruise velocity lowered to %d", self._cruise)
        else:
            self._held.add(key)

    def _on_release(self, key) -> None:
        self._held.discard(key)
        if key == keyboard.Key.esc:
            logger.info("ESC pressed, disconnecting slider keyboard leader.")
            # disconnect is decorated with check_if_not_connected; guard against
            # a double-call if disconnect() has already run from the main thread.
            if self.is_connected:
                self.disconnect()

    @check_if_not_connected
    def get_action(self) -> RobotAction:
        start = time.perf_counter()

        # Space overrides everything as an emergency-stop while held.
        if keyboard.Key.space in self._held:
            logger.debug("slider teleop loop %.2fms", (time.perf_counter() - start) * 1e3)
            return {"slider.vel": 0.0}

        direction = -1 if self.config.invert_direction else 1
        velocity = 0
        if keyboard.Key.right in self._held:
            velocity += direction * self._cruise
        if keyboard.Key.left in self._held:
            velocity -= direction * self._cruise

        logger.debug("slider teleop loop %.2fms", (time.perf_counter() - start) * 1e3)
        return {"slider.vel": float(velocity)}

    def send_feedback(self, feedback: dict[str, Any]) -> None:
        pass

    @check_if_not_connected
    def disconnect(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
