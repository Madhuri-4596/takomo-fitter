"""Golf shot physics: club delivery -> launch -> 3D ball flight.

Two layers:
  1. impact(): a semi-empirical impact model (club speed, dynamic loft, attack
     angle, face, path, strike location -> ball speed, launch, spin, spin axis).
  2. fly(): a vectorised 3D trajectory integrator with drag, Magnus lift and
     spin decay.

Both are calibrated against published TrackMan tour averages (see
docs/SIMULATOR.md). They are plausible, internally consistent models, not a
replacement for a launch monitor. Units in the public API: mph, degrees, rpm,
yards, millimetres. Right-handed sign convention: positive = right of target.
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------- constants
MPH = 0.44704              # m/s per mph
YD = 0.9144                # m per yard
BALL_MASS = 0.04593        # kg (rules maximum)
BALL_DIAM = 0.04267        # m (rules minimum)
BALL_R = BALL_DIAM / 2
AREA = np.pi * BALL_R ** 2
G = 9.81
RHO_STD = 1.204            # kg/m^3, 20 C at sea level

# Aerodynamic and impact coefficients. Fitted by tools/calibrate_physics.py
# against TrackMan PGA and LPGA tour iron averages; values frozen here.
AERO = dict(cd0=0.2543, cd1=0.2487, cl1=0.4590, cl_pow=0.30, spin_tau_s=25.0)
IMPACT = dict(
    smash0=1.620, smash_slope=0.0113,  # smash = smash0 - slope*spin_loft
    launch_dl=0.80, launch_aoa=0.10,    # launch = a*dynamic_loft + b*attack
    spin_k=1.456, spin_pow=1.24,        # spin = k*club_speed*spin_loft^p
    face_weight=0.75,                   # start direction = w*face + (1-w)*path
)


def spin_loft(dynamic_loft, attack):
    return np.asarray(dynamic_loft) - np.asarray(attack)


def impact(club_speed_mph, dynamic_loft_deg, attack_deg, face_deg=0.0, path_deg=0.0,
           offset_mm=0.0, height_mm=0.0, forgiveness=3.0):
    """Return dict of ball launch conditions for arrays of club deliveries.

    offset_mm: strike location along the face, + toward toe, - toward heel.
    height_mm: strike location vertically from centre, + high, - low.
    forgiveness: head forgiveness rating 1-5 (Takomo scale); scales mishit loss.
    """
    v = np.asarray(club_speed_mph, float)
    dl = np.asarray(dynamic_loft_deg, float)
    aoa = np.asarray(attack_deg, float)
    face = np.asarray(face_deg, float)
    path = np.asarray(path_deg, float)
    off = np.asarray(offset_mm, float)
    hgt = np.asarray(height_mm, float)
    fg = np.asarray(forgiveness, float)

    sl = np.clip(dl - aoa, 5.0, 60.0)
    smash_c = IMPACT["smash0"] - IMPACT["smash_slope"] * sl
    # mishit loss: quadratic in distance from the sweet spot; low-forgiveness heads lose more
    r2 = off ** 2 + (1.2 * hgt) ** 2
    loss = 0.00022 * r2 * (1.25 - 0.10 * fg)
    smash = smash_c * np.clip(1.0 - loss, 0.55, 1.0)
    ball = v * smash

    # vertical gear effect: low strikes add spin and launch slightly lower
    launch = IMPACT["launch_dl"] * dl + IMPACT["launch_aoa"] * aoa + 0.04 * hgt
    spin = IMPACT["spin_k"] * v * sl ** IMPACT["spin_pow"] * (1.0 - 0.012 * hgt)
    spin = np.clip(spin, 800.0, 13000.0)

    w = IMPACT["face_weight"]
    start = w * face + (1 - w) * path
    f2p = face - path
    # D-plane approximation of spin-axis tilt, plus horizontal gear effect
    axis = np.degrees(np.arctan2(np.sin(np.radians(f2p)), np.sin(np.radians(sl))))
    axis = axis - 0.25 * off * (1.25 - 0.10 * fg)   # toe strike -> draw tilt (RH)
    return dict(ball_speed_mph=ball, smash=smash, launch_deg=launch,
                launch_dir_deg=start, spin_rpm=spin, spin_axis_deg=axis,
                spin_loft_deg=sl)


def fly(ball_speed_mph, launch_deg, spin_rpm, spin_axis_deg=0.0, launch_dir_deg=0.0,
        rho=RHO_STD, dt=0.02, t_max=12.0, aero=None):
    """Vectorised RK4 flight to landing (ground at launch height).

    Returns dict with carry_yd, side_yd (+ right), apex_yd, land_angle_deg,
    flight_time_s, total_yd (carry plus a simple roll model).
    """
    a = dict(AERO if aero is None else aero)
    vb = np.atleast_1d(np.asarray(ball_speed_mph, float)) * MPH
    n = vb.size
    la = np.radians(np.broadcast_to(launch_deg, (n,)).astype(float))
    ld = np.radians(np.broadcast_to(launch_dir_deg, (n,)).astype(float))
    ax = np.radians(np.broadcast_to(spin_axis_deg, (n,)).astype(float))
    w0 = np.broadcast_to(spin_rpm, (n,)).astype(float) * 2 * np.pi / 60

    # x downrange, y up, z right
    vel = np.stack([vb * np.cos(la) * np.cos(ld), vb * np.sin(la), vb * np.cos(la) * np.sin(ld)], 1)
    pos = np.zeros((n, 3))
    # backspin: angular velocity along +z (right) when the ball travels +x.
    # Positive tilt (right) rotates the axis so the Magnus force gains a +z
    # (rightward) component: a fade/slice for a right-handed golfer.
    spin_dir = np.stack([np.zeros(n), -np.sin(ax), np.cos(ax)], 1)
    k = 0.5 * rho * AREA / BALL_MASS

    def acc(v, t):
        speed = np.linalg.norm(v, axis=1, keepdims=True)
        w = (w0 * np.exp(-t / a["spin_tau_s"]))[:, None]
        S = np.clip(w * BALL_R / np.maximum(speed, 1e-6), 0, 1.0)
        cd = a["cd0"] + a["cd1"] * S
        cl = a["cl1"] * S ** a["cl_pow"]
        drag = -k * cd * speed * v
        lift_dir = np.cross(spin_dir, v)
        lift_dir /= np.maximum(np.linalg.norm(lift_dir, axis=1, keepdims=True), 1e-9)
        lift = k * cl * speed ** 2 * lift_dir
        g = np.array([0.0, -G, 0.0])
        return drag + lift + g

    landed = np.zeros(n, bool)
    land_pos = np.zeros((n, 3))
    land_vel = np.zeros((n, 3))
    land_t = np.zeros(n)
    apex = np.zeros(n)
    t = 0.0
    steps = int(t_max / dt)
    for _ in range(steps):
        active = ~landed
        if not active.any():
            break
        tt = np.full(n, t)
        k1v = acc(vel, tt); k1p = vel
        k2v = acc(vel + 0.5 * dt * k1v, tt + dt / 2); k2p = vel + 0.5 * dt * k1v
        k3v = acc(vel + 0.5 * dt * k2v, tt + dt / 2); k3p = vel + 0.5 * dt * k2v
        k4v = acc(vel + dt * k3v, tt + dt); k4p = vel + dt * k3v
        new_vel = vel + dt / 6 * (k1v + 2 * k2v + 2 * k3v + k4v)
        new_pos = pos + dt / 6 * (k1p + 2 * k2p + 2 * k3p + k4p)
        apex = np.maximum(apex, new_pos[:, 1])
        crossed = active & (new_pos[:, 1] <= 0) & (t > 0.05)
        if crossed.any():
            f = pos[crossed, 1] / (pos[crossed, 1] - new_pos[crossed, 1])
            land_pos[crossed] = pos[crossed] + f[:, None] * (new_pos[crossed] - pos[crossed])
            land_vel[crossed] = vel[crossed] + f[:, None] * (new_vel[crossed] - vel[crossed])
            land_t[crossed] = t + f * dt
            landed |= crossed
        upd = ~landed
        vel[upd] = new_vel[upd]
        pos[upd] = new_pos[upd]
        t += dt
    # any not landed (shouldn't happen) take final state
    nl = ~landed
    land_pos[nl] = pos[nl]; land_vel[nl] = vel[nl]; land_t[nl] = t

    hspeed = np.hypot(land_vel[:, 0], land_vel[:, 2])
    land_angle = np.degrees(np.arctan2(-land_vel[:, 1], hspeed))
    carry = np.hypot(land_pos[:, 0], land_pos[:, 2]) / YD
    side = land_pos[:, 2] / YD
    # simple iron roll model: steeper landing and more spin -> less roll
    w_land = w0 * np.exp(-land_t / a["spin_tau_s"]) * 60 / (2 * np.pi)
    roll = np.clip(0.60 * (hspeed / MPH) * np.cos(np.radians(land_angle)) ** 2
                   * (1.0 - 0.6 * np.clip(w_land / 9000.0, 0, 1.0)) - 1.0, 0.0, 40.0)
    return dict(carry_yd=carry, side_yd=side, apex_yd=apex / YD,
                land_angle_deg=land_angle, flight_time_s=land_t, total_yd=carry + roll)


def shot(club_speed_mph, dynamic_loft_deg, attack_deg, face_deg=0.0, path_deg=0.0,
         offset_mm=0.0, height_mm=0.0, forgiveness=3.0, rho=RHO_STD):
    """impact() followed by fly(); returns merged dict."""
    im = impact(club_speed_mph, dynamic_loft_deg, attack_deg, face_deg, path_deg,
                offset_mm, height_mm, forgiveness)
    fl = fly(im["ball_speed_mph"], im["launch_deg"], im["spin_rpm"],
             im["spin_axis_deg"], im["launch_dir_deg"], rho=rho)
    im.update(fl)
    return im


def lie_face_effect_deg(lie_error_deg, dynamic_loft_deg):
    """Horizontal face-direction change caused by a toe-up (+) lie error.

    A toe-up (too upright) club points the lofted face left: negative (RH).
    Uses the exact relation atan(tan(loft) * sin(lie_error)).
    """
    le = np.radians(np.asarray(lie_error_deg, float))
    dl = np.radians(np.asarray(dynamic_loft_deg, float))
    return -np.degrees(np.arctan(np.tan(dl) * np.sin(le)))
