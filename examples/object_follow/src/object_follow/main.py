from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

import cv2

from lerobot_robot_so101_slider import SO101SliderFollower, SO101SliderFollowerConfig

from .detector import YOLOTracker
from .pid import PID

logger = logging.getLogger("object_follow")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="YOLO + PID slider follower")
    p.add_argument("--robot.port", dest="robot_port", required=True)
    p.add_argument("--robot.id", dest="robot_id", required=True)
    p.add_argument("--slider-id", type=int, default=7)

    p.add_argument("--camera-index", type=int, default=0)
    p.add_argument("--frame-width", type=int, default=640)
    p.add_argument("--frame-height", type=int, default=480)

    p.add_argument("--model", default="yolo11n.pt")
    p.add_argument("--target-class", default="person")
    p.add_argument("--conf", type=float, default=0.35)

    p.add_argument("--kp", type=float, default=6.0)
    p.add_argument("--ki", type=float, default=0.0)
    p.add_argument("--kd", type=float, default=0.4)
    p.add_argument("--deadzone-px", type=float, default=25.0)
    p.add_argument("--max-velocity", type=int, default=2500)
    p.add_argument("--invert", action="store_true")

    p.add_argument("--loop-hz", type=float, default=30.0)
    p.add_argument("--no-preview", action="store_true")
    p.add_argument("--log-level", default="INFO")

    return p.parse_args()


def _try_int(value: str) -> str | int:
    try:
        return int(value)
    except ValueError:
        return value


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cap = cv2.VideoCapture(args.camera_index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.frame_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.frame_height)
    if not cap.isOpened():
        logger.error("Could not open camera index %d", args.camera_index)
        return 1

    tracker = YOLOTracker(
        model_path=args.model,
        target_class=_try_int(args.target_class),
        conf=args.conf,
    )

    robot = SO101SliderFollower(
        SO101SliderFollowerConfig(
            port=args.robot_port,
            id=args.robot_id,
            slider_id=args.slider_id,
            slider_max_velocity=args.max_velocity,
        )
    )
    robot.connect()

    pid = PID(
        kp=args.kp,
        ki=args.ki,
        kd=args.kd,
        out_min=-float(args.max_velocity),
        out_max=float(args.max_velocity),
    )

    stop = False

    def _on_sigint(signum, frame):  # noqa: ARG001
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _on_sigint)

    direction = -1.0 if args.invert else 1.0
    period = 1.0 / max(1.0, args.loop_hz)
    last_t = time.perf_counter()

    try:
        while not stop:
            loop_start = time.perf_counter()

            ok, frame = cap.read()
            if not ok:
                logger.warning("Camera read failed")
                continue

            h, w = frame.shape[:2]
            center_x = w / 2.0

            det = tracker.detect(frame)
            now = time.perf_counter()
            dt = now - last_t
            last_t = now

            if det is None:
                pid.reset()
                slider_vel = 0.0
                error = 0.0
            else:
                error = det.cx - center_x
                if abs(error) <= args.deadzone_px:
                    # Inside the dead zone we stop driving but keep integrator
                    # state, otherwise hovering near center constantly resets it.
                    slider_vel = 0.0
                else:
                    slider_vel = direction * pid.step(error, dt)

            robot.send_action({"slider.vel": float(slider_vel)})

            if not args.no_preview:
                _draw_preview(frame, det, center_x, error, slider_vel)
                cv2.imshow("object_follow", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    stop = True

            elapsed = time.perf_counter() - loop_start
            if elapsed < period:
                time.sleep(period - elapsed)
    finally:
        try:
            robot.send_action({"slider.vel": 0.0})
        except Exception:
            logger.exception("Failed to zero slider on shutdown")
        robot.disconnect()
        cap.release()
        if not args.no_preview:
            cv2.destroyAllWindows()

    return 0


def _draw_preview(frame, det, center_x: float, error: float, slider_vel: float) -> None:
    h, w = frame.shape[:2]
    cv2.line(frame, (int(center_x), 0), (int(center_x), h), (200, 200, 200), 1)
    if det is not None:
        x1 = int(det.cx - det.width / 2)
        y1 = int(det.cy - det.height / 2)
        x2 = int(det.cx + det.width / 2)
        y2 = int(det.cy + det.height / 2)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.circle(frame, (int(det.cx), int(det.cy)), 4, (0, 255, 0), -1)
        label = f"id={det.track_id} conf={det.conf:.2f}"
        cv2.putText(frame, label, (x1, max(0, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    cv2.putText(
        frame,
        f"err={error:+.1f}px  vel={slider_vel:+.0f}",
        (10, h - 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
    )


if __name__ == "__main__":
    sys.exit(main())
