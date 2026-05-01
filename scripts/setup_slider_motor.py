"""Set the Feetech bus ID on the slider motor.

Connect ONLY the slider motor to the controller before running this script.
The bus is opened with just the slider so no other motors are scanned or
disturbed. After the ID is written, daisy-chain everything back together.

Run with:
    uv run scripts/setup_slider_motor.py \\
        --port=/dev/tty.usbmodemFOLLOWER \\
        --slider-id=7
"""
from __future__ import annotations

import argparse

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True, help="Serial port the slider is connected to.")
    p.add_argument("--slider-id", type=int, default=7, help="Target Feetech ID for the slider motor (must be outside 1..6).")
    args = p.parse_args()

    if args.slider_id in range(1, 7):
        raise ValueError(f"--slider-id={args.slider_id} collides with an SO-101 arm motor (IDs 1..6).")

    bus = FeetechMotorsBus(
        port=args.port,
        motors={"slider": Motor(args.slider_id, "sts3215", MotorNormMode.RANGE_M100_100)},
    )

    print("Connect ONLY the slider motor to the controller, then press ENTER.")
    input()

    try:
        bus.setup_motor("slider")
        print(f"Slider motor ID set to {args.slider_id}.")
    finally:
        if bus.is_connected:
            bus.disconnect()


if __name__ == "__main__":
    main()
