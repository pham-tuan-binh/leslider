from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from ultralytics import YOLO

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    cx: float
    cy: float
    width: float
    height: float
    conf: float
    track_id: int | None
    class_id: int


class YOLOTracker:
    """Thin wrapper around `ultralytics.YOLO` that returns one tracked target.

    `target_class` may be a class name (e.g. "person") or a numeric class id.
    Once we lock onto a tracker id we prefer it on subsequent frames so the
    slider doesn't ping-pong between two same-class objects.
    """

    def __init__(self, model_path: str, target_class: str | int, conf: float = 0.35):
        self.model = YOLO(model_path)
        self.conf = conf
        self.target_class_id = self._resolve_class(target_class)
        self._locked_track_id: int | None = None

    def _resolve_class(self, target: str | int) -> int:
        names = self.model.names
        if isinstance(target, int):
            if target not in names:
                raise ValueError(f"class id {target} not in model.names")
            return target
        for cid, cname in names.items():
            if cname == target:
                return int(cid)
        raise ValueError(
            f"class name '{target}' not in model.names. Available: {sorted(names.values())[:20]}..."
        )

    def reset_lock(self) -> None:
        self._locked_track_id = None

    def detect(self, frame: np.ndarray) -> Detection | None:
        # `persist=True` keeps the tracker state across calls (BoT-SORT default).
        results = self.model.track(
            frame,
            persist=True,
            classes=[self.target_class_id],
            conf=self.conf,
            verbose=False,
        )
        if not results:
            return None
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return None

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        class_ids = boxes.cls.cpu().numpy().astype(int)
        track_ids = (
            boxes.id.cpu().numpy().astype(int) if boxes.id is not None else np.full(len(boxes), -1)
        )

        best_idx: int | None = None
        if self._locked_track_id is not None:
            for i, tid in enumerate(track_ids):
                if int(tid) == self._locked_track_id:
                    best_idx = i
                    break

        if best_idx is None:
            best_idx = int(np.argmax(confs))
            tid = int(track_ids[best_idx])
            if tid != -1:
                self._locked_track_id = tid
                logger.info("Locked onto track id %d", tid)

        x1, y1, x2, y2 = xyxy[best_idx]
        return Detection(
            cx=float((x1 + x2) / 2),
            cy=float((y1 + y2) / 2),
            width=float(x2 - x1),
            height=float(y2 - y1),
            conf=float(confs[best_idx]),
            track_id=int(track_ids[best_idx]) if track_ids[best_idx] != -1 else None,
            class_id=int(class_ids[best_idx]),
        )
