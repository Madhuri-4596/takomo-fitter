"""Synthetic face-on 'swing' video with known timing, for the tempo browser test.

The public-domain portrait used for the sample photo moves up and down following a
hand-height curve with a 0.9 s backswing and 0.3 s downswing (then a higher finish), so
pose tracking sees a real person whose wrist track has known swing events. It is not a
real golf swing: it tests the pipeline (decode -> pose -> track -> kit tempo), not swing biomechanics.

  .venv/Scripts/python tools/make_swing_video.py [out.webm]
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "tests")]
from make_sample_photo import SRC_CROP, source  # noqa: E402
from test_swing import hand_y  # noqa: E402

BACK, DOWN, FPS, W, H = 0.9, 0.3, 30, 480, 720


def main(out: Path):
    x0, y0, x1, y1 = SRC_CROP
    person = cv2.resize(source()[y0:y1, x0:x1], None, fx=0.42, fy=0.42, interpolation=cv2.INTER_AREA)
    ph, pw = person.shape[:2]
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"VP80"), FPS, (W, H))
    for i in range(int((0.5 + BACK + DOWN + 0.9) * FPS)):
        dy = int(round((hand_y(i / FPS, BACK, DOWN) - 500.0) * 0.5))      # up to ~115 px up at the finish
        frame = np.full((H, W, 3), (196, 214, 226), np.uint8)
        top = H - ph - 10 + dy
        frame[max(0, top):top + ph, (W - pw) // 2:(W - pw) // 2 + pw] = person[max(0, -top):]
        writer.write(frame)
    writer.release()
    print(f"wrote {out} ({out.stat().st_size} bytes): backswing {BACK} s, downswing {DOWN} s, {FPS} fps")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "takomo_swing.webm")
