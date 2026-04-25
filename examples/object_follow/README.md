# object_follow

A self-contained example: an Ultralytics YOLO detector pulls bounding boxes out
of a camera feed, and a PID loop converts the horizontal pixel error of a
tracked object into a slider velocity for the SO-101 slider robot. The slider
follows the object so it stays centered in the frame.

```
camera frame ──► YOLO detect+track ──► pixel error (x_target - x_center)
                                              │
                                              ▼
                                          PID loop  ──►  slider.vel  ──►  SO101SliderFollower
```

## Setup with uv

From this directory:

```bash
uv sync
```

That installs `lerobot`, `ultralytics`, `opencv-python`, and the local
`lerobot_robot_so101_slider` package in editable mode (it's referenced by
relative path in `pyproject.toml`).

The first run will download a YOLO weights file (default `yolo11n.pt`) into
this directory.

## Run it

```bash
uv run object-follow \
    --robot.port=/dev/tty.usbmodemFOLLOWER \
    --robot.id=my_arm \
    --camera-index=0 \
    --target-class=person
```

Press `q` in the preview window or `Ctrl-C` in the terminal to exit; the
slider is zeroed and the robot disconnected on the way out.

### Useful flags

| Flag | Default | Notes |
| ---- | ------- | ----- |
| `--robot.port` | required | Serial port of the SO-101 follower (with slider motor on the bus). |
| `--robot.id` | required | Calibration ID — the same one passed to `lerobot-calibrate`. |
| `--camera-index` | `0` | OpenCV camera index. |
| `--model` | `yolo11n.pt` | Any Ultralytics-compatible weights file. |
| `--target-class` | `person` | Class name (or numeric COCO id) the slider tracks. |
| `--conf` | `0.35` | Minimum detection confidence. |
| `--kp` / `--ki` / `--kd` | `6.0 / 0.0 / 0.4` | PID gains, mapping pixel error → ticks/s. |
| `--deadzone-px` | `25` | Inside this pixel band the slider commands zero. |
| `--max-velocity` | `2500` | Hard clamp on `slider.vel` (raw ticks/s). |
| `--invert` | off | Flip slider direction if the camera is mounted backwards. |
| `--no-preview` | off | Run headless (skip the OpenCV window). |

### What can it detect?

The default model (`yolo11n.pt`) is YOLO11-nano pretrained on COCO, so it
recognizes 80 classes. A few useful ones for this rig:

- **People & animals** — person, bird, cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe
- **Vehicles** — bicycle, car, motorcycle, airplane, bus, train, truck, boat
- **Sports** — frisbee, sports ball, skateboard, surfboard, tennis racket, baseball bat/glove
- **Kitchen / desk** — bottle, wine glass, cup, fork, knife, spoon, bowl, cell phone, laptop, mouse, keyboard, book, scissors
- **Food** — banana, apple, sandwich, orange, broccoli, carrot, pizza, donut, cake
- **Furniture** — chair, couch, bed, dining table, potted plant
- **Misc** — backpack, umbrella, handbag, tie, suitcase, teddy bear, clock, vase

Pass any of these names (or the numeric COCO id) to `--target-class`. Full
list with `python -c "from ultralytics import YOLO; print(YOLO('yolo11n.pt').names)"`.
For something outside COCO, swap `--model` for a custom-trained `.pt` — the
rest of the pipeline doesn't care.

## How it works

1. **Detection** — `ultralytics.YOLO` runs `model.track()` per frame so a
   single object keeps its identity across frames. We pick the highest-confidence
   detection of `target-class`; if it has a tracker id we lock onto it and
   prefer its future detections to avoid jumping to other objects of the same
   class.
2. **Error** — `error_px = x_target - frame_width / 2`. Positive means the
   object is right of center.
3. **PID** — gains map pixel error to `slider.vel` ticks/s. The derivative
   term is low-pass-filtered against measurement noise from jittery boxes.
4. **Output** — clamped to `±max-velocity`, sign-flipped if `--invert`,
   sent through `SO101SliderFollower.send_action({"slider.vel": v})`.

When no target is visible the PID is reset and `slider.vel = 0` so the slider
coasts to a stop instead of running away on stale state.
