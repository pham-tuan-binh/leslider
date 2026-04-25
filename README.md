# leslider — SO-101 on a slider, for LeRobot

An SO-101 arm mounted on a linear slider driven by a seventh STS3215 Feetech
motor on the same bus. Comes with three LeRobot plugins so the whole rig
behaves like one robot.

```
packages/
├── lerobot_robot_so101_slider/            # follower: SO-101 + slider motor
├── lerobot_teleoperator_slider_keyboard/  # leader:   arrow-key slider teleop
└── lerobot_teleoperator_so101_slider/     # leader:   SO-101 arm; base joint → slider velocity
3d/                                        # STL / STEP files for printable parts (see §3)
```

Plugin names start with `lerobot_robot_` / `lerobot_teleoperator_`, so LeRobot
auto-imports them at startup
(`lerobot.utils.import_utils.register_third_party_plugins`). No further
registration needed.

---

## Build flow

1. [Bill of Materials](#1-bill-of-materials) — what to buy
2. [3D-printed parts](#2-3d-printed-parts) — what to print
3. [Assembly](#3-assembly) — wiring and mounting
4. [Install the plugins](#4-install-the-plugins)
5. [Set the slider motor ID](#5-set-the-slider-motor-id)
6. [Calibrate](#6-calibrate)
7. [Run it](#7-run-it) — three teleop modes
8. [Record datasets](#8-record-datasets)
9. [Config reference](#9-config-reference)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Bill of Materials

> **TODO** — finalise once parts are confirmed. Quantities below cover one
> complete rig.

| Item | Qty | Notes |
| ---- | --- | ----- |
| SO-101 arm kit (6× STS3215, brackets, gripper) | 1 | stock LeRobot SO-101 |
| STS3215 Feetech servo (slider drive) | 1 | same model as the arm motors so it shares the bus |
| Linear rail + carriage | 1 | length sized to your reach (typical 300–500 mm) |
| Timing belt + matching pulleys | 1 set | belt closed-loop or open with clamps |
| M3 / M4 fasteners | — | for arm-to-carriage and rail-to-base mounting |
| 12 V power supply | 1 | sized for arm + slider current peak |
| USB-to-TTL adapter (Feetech / Waveshare) | 1 | the one shipped with SO-101 works |
| Cabling | — | bus extension cable to reach the slider motor |

Add or remove rows as the design settles.

---

## 2. 3D-printed parts

Printable files live in `3d/`. Drop STL/STEP files there; this table is the
index.

> **TODO** — fill in once parts are exported.

| File | Purpose | Material / infill | Qty |
| ---- | ------- | ----------------- | --- |
| `3d/carriage_top.stl` | Mounts the SO-101 base to the rail carriage | PETG / 40 % | 1 |
| `3d/motor_mount.stl` | Holds the slider STS3215 to one end of the rail | PETG / 40 % | 1 |
| `3d/idler_mount.stl` | Tensioning idler at the other end | PETG / 40 % | 1 |
| `3d/belt_clamp.stl` | Clamps the belt ends to the carriage | PETG / 50 % | 2 |

Print orientation, supports, and any hardware inserts go in the per-file
notes once the files are added.

---

## 3. Assembly

1. **Print the parts** in `3d/` and clean up any supports.
2. **Mount the slider motor** to one end of the rail using `motor_mount.stl`,
   the idler to the other end, and route the belt around both pulleys.
3. **Bolt the SO-101 base** onto `carriage_top.stl` and slide it onto the
   carriage. Clamp the belt to the carriage with `belt_clamp.stl` so the
   slider motor pulls the arm linearly.
4. **Daisy-chain the bus.** All seven STS3215s share one Feetech bus —
   chain the slider motor onto the SO-101 arm bus. The order on the chain
   doesn't matter; only the IDs do.
5. **Power.** Use the same 12 V supply if it has the headroom; if the slider
   stalls or browns the arm out, give the slider its own supply with a
   common ground.

---

## 4. Install the plugins

Both leader plugins import `pynput` for keyboard handling, so install
`lerobot` and the three packages in editable mode:

```bash
pip install lerobot
pip install -e packages/lerobot_robot_so101_slider
pip install -e packages/lerobot_teleoperator_slider_keyboard
pip install -e packages/lerobot_teleoperator_so101_slider
```

Verify discovery:

```bash
python -c "from lerobot.utils.import_utils import register_third_party_plugins; \
           register_third_party_plugins(); \
           from lerobot.robots.config import RobotConfig; \
           from lerobot.teleoperators.config import TeleoperatorConfig; \
           print('robots:',  sorted(RobotConfig.get_choices())); \
           print('teleops:', sorted(TeleoperatorConfig.get_choices()))"
```

You should see `so101_slider_follower`, `slider_keyboard_leader`, and
`so101_slider_leader` in the lists.

---

## 5. Set the slider motor ID

SO-101 already uses IDs 1..6, so the slider motor must be **7** (default) or
anything in 8..253. Connect *only* the slider motor to the controller, then:

```bash
lerobot-setup-motors \
    --robot.type=so101_slider_follower \
    --robot.port=/dev/tty.usbmodemXXXX
```

It walks each motor name in reverse. When it reaches `slider`, plug in the
slider motor alone and press ENTER. Disconnect between steps so the
broadcast-ping finds one motor at a time.

After that, daisy-chain everything back together.

---

## 6. Calibrate

Only the arm joints need calibration — the slider runs in continuous-rotation
velocity mode and is skipped.

```bash
lerobot-calibrate \
    --robot.type=so101_slider_follower \
    --robot.port=/dev/tty.usbmodemXXXX \
    --robot.id=my_arm
```

This is the stock SO-101 calibration (middle pose, range-of-motion sweep)
against IDs 1..6. The result lands in
`~/.cache/huggingface/lerobot/calibration/robots/so101_slider_follower/my_arm.json`.

If you also use the SO-101 leader arm (mode B / C below), calibrate it once:

```bash
lerobot-calibrate \
    --teleop.type=so101_leader \
    --teleop.port=/dev/tty.usbmodemLEADER \
    --teleop.id=my_leader
```

---

## 7. Run it

Three teleop modes ship out of the box.

### A. Slider only — keyboard

Drive just the slider with the arrow keys; the arm holds position.

```bash
lerobot-teleoperate \
    --robot.type=so101_slider_follower \
    --robot.port=/dev/tty.usbmodemFOLLOWER \
    --robot.id=my_arm \
    --teleop.type=slider_keyboard_leader \
    --teleop.id=slider_kb
```

| Key                | Effect                                                    |
| ------------------ | --------------------------------------------------------- |
| Left / Right arrow | Drive the slider one direction / the other               |
| Up / Down arrow    | Trim the cruise velocity ±`speed_increment`              |
| Space              | Emergency stop (zero velocity while held)                 |
| ESC                | Disconnect                                                |

### B. Full arm + slider, leader base drives the slider

Use the `so101_slider_leader` teleop. The leader's `shoulder_pan` (base joint)
becomes the slider throttle: under a ±20 dead zone the slider sits still,
above it velocity ramps linearly to the cap. The follower's *own* base
(`shoulder_pan.pos`) is **not** copied from the leader — it starts at 0 and is
adjusted from the keyboard. Every other arm joint is mirrored normally.

```bash
lerobot-teleoperate \
    --robot.type=so101_slider_follower \
    --robot.port=/dev/tty.usbmodemFOLLOWER \
    --robot.id=my_arm \
    --teleop.type=so101_slider_leader \
    --teleop.port=/dev/tty.usbmodemLEADER \
    --teleop.id=my_leader
```

| Key                | Effect                                                              |
| ------------------ | ------------------------------------------------------------------- |
| Leader base joint  | Drives `slider.vel` (0 inside ±`base_deadzone`, ramps to ±`slider_max_velocity` at ±`base_max`) |
| Left / Right arrow | Trim follower base target by `follower_base_increment`              |
| Space              | Reset follower base target to `follower_base_default` (0)           |
| ESC                | Disconnect                                                          |

### C. Full arm + slider, two-leader Python launcher

Stock SO-101 leader for the arm joints and the keyboard leader for the slider,
merged in Python:

```python
# run_both.py
import time
from lerobot_robot_so101_slider import SO101SliderFollower, SO101SliderFollowerConfig
from lerobot_teleoperator_slider_keyboard import (
    SliderKeyboardLeader, SliderKeyboardLeaderConfig,
)
from lerobot.teleoperators.so_leader import SOLeader, SOLeaderTeleopConfig

robot = SO101SliderFollower(SO101SliderFollowerConfig(
    port="/dev/tty.usbmodemFOLLOWER", id="my_arm", slider_id=7,
))
arm_leader = SOLeader(SOLeaderTeleopConfig(
    port="/dev/tty.usbmodemLEADER", id="my_leader",
))
slider_leader = SliderKeyboardLeader(SliderKeyboardLeaderConfig(
    id="slider_kb", cruise_velocity=1500,
))

robot.connect(); arm_leader.connect(); slider_leader.connect()
try:
    while slider_leader.is_connected:
        action = {**arm_leader.get_action(), **slider_leader.get_action()}
        robot.send_action(action)
        time.sleep(1 / 60)
finally:
    slider_leader.disconnect(); arm_leader.disconnect(); robot.disconnect()
```

---

## 8. Record datasets

Same `lerobot-record` invocation as a stock SO-101, just point at the new
robot type and pick whichever teleop you want:

```bash
lerobot-record \
    --robot.type=so101_slider_follower \
    --robot.port=/dev/tty.usbmodemFOLLOWER \
    --robot.id=my_arm \
    --teleop.type=so101_slider_leader \
    --teleop.port=/dev/tty.usbmodemLEADER \
    --teleop.id=my_leader \
    --dataset.repo_id=$USER/leslider_demo \
    --dataset.num_episodes=5 \
    --dataset.single_task="slide and grab"
```

The dataset's action space includes `slider.vel`; observations include
`slider.pos` and `slider.vel` alongside the six arm joints.

---

## 9. Config reference

### `SO101SliderFollowerConfig`

Inherits from `SOFollowerConfig` (port, cameras, `max_relative_target`,
`use_degrees`, `disable_torque_on_disconnect`) and adds:

| Field                 | Default | Description                                                                                |
| --------------------- | ------- | ------------------------------------------------------------------------------------------ |
| `slider_id`           | `7`     | Feetech bus ID for the slider motor. Must not be in 1..6. Validated at construction.       |
| `slider_max_velocity` | `3000`  | Clamp applied to `slider.vel` before writing `Goal_Velocity` (raw sign-magnitude ticks/s). |

### `SliderKeyboardLeaderConfig`

| Field              | Default | Description                                                         |
| ------------------ | ------- | ------------------------------------------------------------------- |
| `cruise_velocity`  | `1500`  | Starting magnitude applied when Left/Right is held, in raw ticks/s. |
| `speed_increment`  | `250`   | Amount Up/Down adds to/removes from the cruise velocity per press.  |
| `min_velocity`     | `100`   | Lower bound of cruise trim.                                         |
| `max_velocity`     | `3000`  | Upper bound of cruise trim.                                         |
| `invert_direction` | `False` | Swap Left ↔ Right if the slider is mounted flipped.                 |

### `SO101SliderLeaderConfig`

| Field                     | Default  | Description                                                                                            |
| ------------------------- | -------- | ------------------------------------------------------------------------------------------------------ |
| `port`                    | —        | Serial port of the SO-101 leader arm.                                                                  |
| `use_degrees`             | `True`   | Leader position unit. `base_deadzone` / `base_max` use the same unit.                                  |
| `base_deadzone`           | `20.0`   | Symmetric dead zone around 0 on the leader base. `\|shoulder_pan\| <= base_deadzone` → `slider.vel = 0`. |
| `base_max`                | `90.0`   | `\|shoulder_pan\|` at which the slider saturates to `±slider_max_velocity`.                            |
| `slider_max_velocity`     | `3000`   | Raw-tick velocity cap emitted on `slider.vel`.                                                         |
| `invert_direction`        | `False`  | Swap slider direction relative to leader base sign.                                                    |
| `follower_base_default`   | `0.0`    | Starting follower `shoulder_pan.pos` target (pre-keyboard).                                            |
| `follower_base_increment` | `5.0`    | Step per Left/Right arrow press, in the follower's unit.                                               |
| `follower_base_min`       | `-100.0` | Lower clamp for the follower base target.                                                              |
| `follower_base_max`       | `100.0`  | Upper clamp for the follower base target.                                                              |

---

## 10. Troubleshooting

- **`ValueError: slider_id=… collides with an SO-101 arm motor`** — pick a
  slider ID outside 1..6 (default is 7).
- **Slider doesn't move.** Check the slider is in velocity mode after
  `configure()`: `bus.read("Operating_Mode", "slider")` should return `1`. If
  torque is off, confirm `disable_torque_on_disconnect` from the previous
  session didn't leave it disabled.
- **Arrow keys do nothing.** `pynput` needs a display server on Linux — if
  `DISPLAY` is unset, the listener is skipped and a warning is logged. Run
  inside a graphical session (or over X/Wayland forwarding).
- **Slider kicks on startup.** The follower writes `Goal_Velocity = 0` inside
  `configure()` *before* re-enabling torque. If you still see motion, confirm
  the previous session's `disconnect()` zeroed the velocity (it tries to,
  inside a `try/except` so other disconnect work still runs).
- **Leader base feels too sensitive / not sensitive enough.** Tune
  `base_deadzone` and `base_max` on `SO101SliderLeaderConfig`. Wider dead
  zone = harder to start moving; smaller `base_max` = full speed reached
  sooner.
