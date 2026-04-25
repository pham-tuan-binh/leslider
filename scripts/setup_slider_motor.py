"""Set the Feetech bus ID on the slider motor.

`lerobot-setup-motors` hardcodes a whitelist that excludes the
`so101_slider_follower` type, so we set the slider motor's ID directly via
the follower's `setup_motors()` method. The walk goes in reverse, which
puts `slider` first; press Ctrl-C after that step since the SO-101 arm
motors are pre-configured by the kit.

Run with:
    uv run python scripts/setup_slider_motor.py \\
        --port=/dev/tty.usbmodemFOLLOWER \\
        --slider-id=7
"""
from __future__ import annotations

import argparse

from lerobot_robot_so101_slider import (
    SO101SliderFollower,
    SO101SliderFollowerConfig,
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True, help="Serial port of the SO-101 follower bus.")
    p.add_argument("--slider-id", type=int, default=7, help="Target Feetech ID for the slider motor (must be outside 1..6).")
    p.add_argument("--id", default="my_arm", help="Calibration ID (only used to satisfy lerobot's config; not written here).")
    args = p.parse_args()

    robot = SO101SliderFollower(
        SO101SliderFollowerConfig(
            port=args.port,
            id=args.id,
            slider_id=args.slider_id,
        )
    )
    print(
        "Disconnect every motor except the SLIDER, then press ENTER when prompted.\n"
        "After the slider's ID is set, press Ctrl-C; the SO-101 arm motors come\n"
        "pre-configured from the kit and don't need this step."
    )
    robot.bus.connect()
    try:
        robot.setup_motors()
    except KeyboardInterrupt:
        print("\n[setup] stopping; slider ID is already written.")
    finally:
        robot.bus.disconnect(disable_torque=True)


if __name__ == "__main__":
    main()
