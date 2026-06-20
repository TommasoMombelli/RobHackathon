"""Pure control law: YOLO bounding box -> drone/gimbal commands.

Extracted from go_to_helmet.py so it can be used without that file's heavy
dependencies (supervision / filterpy / simple_pid / ultralytics). This is the
stateless, numpy-only core: given one detected box it returns a yaw rate, a
forward velocity, an absolute gimbal pitch, and a target-reached flag.

main.py maps these onto the discrete Cyberwave commands (turn_left/right,
move_forward/backward, gimbal_rotate) since the DJI Mini driver does not expose
continuous Virtual Stick velocities.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BoundingBoxCommandConfig:
    """Calibration for :func:`compute_drone_commands`.

    DJI convention used by this function:
    - positive yaw rate rotates the aircraft to the right;
    - positive forward velocity moves it towards the target;
    - gimbal pitch is an absolute angle in degrees: 0 is level, negative points down.

    Confirm all three signs with a tiny manual test before using a live drone.
    """

    frame_width: int = 1920
    frame_height: int = 1080
    target_width_px: float = 400.0

    # Proportional gains (not PID): a pure function has no integrator/history.
    yaw_deg_s_per_px: float = 0.015
    forward_m_s_per_px: float = 0.002
    camera_pitch_deg_per_px: float = 0.040

    max_yaw_deg_s: float = 12.0
    max_forward_m_s: float = 0.20
    min_camera_pitch_deg: float = -85.0
    max_camera_pitch_deg: float = 20.0
    center_camera_pitch_deg: float = -45.0

    x_tolerance_px: float = 45.0
    y_tolerance_px: float = 45.0
    width_tolerance_px: float = 35.0

    yaw_sign: float = 1.0
    forward_sign: float = 1.0
    camera_pitch_sign: float = 1.0


@dataclass(frozen=True)
class DroneCommands:
    """Commands generated from one target bounding box.

    ``yaw_deg_s`` is an angular velocity [deg/s].
    ``forward_m_s`` is an aircraft velocity [m/s].
    ``camera_pitch_deg`` is the requested *absolute* gimbal pitch [degrees].
    """

    yaw_deg_s: float
    forward_m_s: float
    camera_pitch_deg: float
    error_x_px: float
    error_y_px: float
    error_width_px: float
    target_reached: bool


def compute_drone_commands(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    config: BoundingBoxCommandConfig = BoundingBoxCommandConfig(),
) -> DroneCommands:
    """Return drone/gimbal commands from a single detected bounding box.

    Parameters are in image pixels: ``x1, y1`` top-left, ``x2, y2`` bottom-right.
    Width is the distance proxy: a target narrower than ``target_width_px``
    produces a positive forward command.
    """

    width = x2 - x1
    height = y2 - y1
    if width <= 0 or height <= 0:
        raise ValueError(
            "Expected a valid box with x2 > x1 and y2 > y1 (top-left to bottom-right)."
        )

    x = x1 + width / 2.0
    y = y1 + height / 2.0

    error_x = x - config.frame_width / 2.0
    error_y = y - config.frame_height / 2.0
    error_width = config.target_width_px - width

    # Dead bands stop constant micro-corrections near the desired alignment.
    def outside_deadband(error: float, tolerance: float) -> float:
        if abs(error) <= tolerance:
            return 0.0
        return error - np.copysign(tolerance, error)

    yaw_input = outside_deadband(error_x, config.x_tolerance_px)
    width_input = outside_deadband(error_width, config.width_tolerance_px)
    y_input = outside_deadband(error_y, config.y_tolerance_px)

    yaw_deg_s = float(
        np.clip(
            config.yaw_sign * config.yaw_deg_s_per_px * yaw_input,
            -config.max_yaw_deg_s,
            config.max_yaw_deg_s,
        )
    )
    forward_m_s = float(
        np.clip(
            config.forward_sign * config.forward_m_s_per_px * width_input,
            -config.max_forward_m_s,
            config.max_forward_m_s,
        )
    )

    # When the target appears lower in the image (positive Y error), point the
    # gimbal further down (a more negative DJI pitch angle).
    camera_pitch_deg = float(
        np.clip(
            config.center_camera_pitch_deg
            - config.camera_pitch_sign * config.camera_pitch_deg_per_px * y_input,
            config.min_camera_pitch_deg,
            config.max_camera_pitch_deg,
        )
    )

    target_reached = (
        abs(error_x) <= config.x_tolerance_px
        and abs(error_y) <= config.y_tolerance_px
        and abs(error_width) <= config.width_tolerance_px
    )
    return DroneCommands(
        yaw_deg_s=yaw_deg_s,
        forward_m_s=forward_m_s,
        camera_pitch_deg=camera_pitch_deg,
        error_x_px=error_x,
        error_y_px=error_y,
        error_width_px=error_width,
        target_reached=target_reached,
    )
