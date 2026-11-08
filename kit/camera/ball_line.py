"""Read the ball-line (impact marker) lie test from a photo of the club face.

Draw a straight vertical line on the back of a ball, set the line facing the
club, hit off a hard flat surface, then photograph the face square-on. Click the
two ends of the transferred line and a point at the heel and toe ends of the
leading edge (the face's ground line).

tilt > 0: the top of the transferred line leans toward the toe -> the club was
toe-up at impact (too upright); the golfer needs flatter. tilt < 0: needs more
upright. Face angle at impact also rotates the mark, and a toe-up club closes
the face, so the mark leans further toward the toe: it exaggerates the lie
error. Average several shots and treat the tilt as an indication, not a
measurement.
"""
from __future__ import annotations

import math


def tilt_deg(line_bottom, line_top, heel, toe):
    """Angle between the transferred line and the face's vertical, + toward the toe.

    Points are image pixels (x right, y down). heel/toe: two points on the
    leading edge, heel end and toe end.
    """
    ex, ey = toe[0] - heel[0], toe[1] - heel[1]          # along the leading edge, toward toe
    lx, ly = line_top[0] - line_bottom[0], line_top[1] - line_bottom[1]
    edge_len = math.hypot(ex, ey)
    ux, uy = ex / edge_len, ey / edge_len                 # unit toward toe
    nx, ny = uy, -ux                                      # face "up" (perpendicular, pointing up in image)
    if ny > 0:
        nx, ny = -nx, -ny
    along_toe = lx * ux + ly * uy
    along_up = lx * nx + ly * ny
    if along_up < 0:              # points given in either order
        along_toe, along_up = -along_toe, -along_up
    return round(math.degrees(math.atan2(along_toe, along_up)), 1)
