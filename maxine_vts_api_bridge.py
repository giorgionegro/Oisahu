#!/usr/bin/env python3
import argparse
from collections import deque
import errno
import json
import math
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import websocket

SCRIPT_DIR = Path(__file__).resolve().parent
ARKIT_BLENDSHAPES = [
    "eyeBlinkLeft",
    "eyeLookDownLeft",
    "eyeLookInLeft",
    "eyeLookOutLeft",
    "eyeLookUpLeft",
    "eyeSquintLeft",
    "eyeWideLeft",
    "eyeBlinkRight",
    "eyeLookDownRight",
    "eyeLookInRight",
    "eyeLookOutRight",
    "eyeLookUpRight",
    "eyeSquintRight",
    "eyeWideRight",
    "jawForward",
    "jawLeft",
    "jawRight",
    "jawOpen",
    "mouthClose",
    "mouthFunnel",
    "mouthPucker",
    "mouthLeft",
    "mouthRight",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    "noseSneerLeft",
    "noseSneerRight",
    "tongueOut",
]

MAXINE_EXPR_INDEX_NAMES = [
    "browDown_L",
    "browDown_R",
    "browInnerUp_L",
    "browInnerUp_R",
    "browOuterUp_L",
    "browOuterUp_R",
    "cheekPuff_L",
    "cheekPuff_R",
    "cheekSquint_L",
    "cheekSquint_R",
    "eyeBlink_L",
    "eyeBlink_R",
    "eyeLookDown_L",
    "eyeLookDown_R",
    "eyeLookIn_L",
    "eyeLookIn_R",
    "eyeLookOut_L",
    "eyeLookOut_R",
    "eyeLookUp_L",
    "eyeLookUp_R",
    "eyeSquint_L",
    "eyeSquint_R",
    "eyeWide_L",
    "eyeWide_R",
    "jawForward",
    "jawLeft",
    "jawOpen",
    "jawRight",
    "mouthClose",
    "mouthDimple_L",
    "mouthDimple_R",
    "mouthFrown_L",
    "mouthFrown_R",
    "mouthFunnel",
    "mouthLeft",
    "mouthLowerDown_L",
    "mouthLowerDown_R",
    "mouthPress_L",
    "mouthPress_R",
    "mouthPucker",
    "mouthRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthSmile_L",
    "mouthSmile_R",
    "mouthStretch_L",
    "mouthStretch_R",
    "mouthUpperUp_L",
    "mouthUpperUp_R",
    "noseSneer_L",
    "noseSneer_R",
]

VTS_DEFAULT_PARAMETER_IDS = [
    "FacePositionX",
    "FacePositionY",
    "FacePositionZ",
    "FaceAngleX",
    "FaceAngleY",
    "FaceAngleZ",
    "MouthSmile",
    "MouthOpen",
    "Brows",
    "EyeOpenLeft",
    "EyeOpenRight",
    "EyeLeftX",
    "EyeLeftY",
    "EyeRightX",
    "EyeRightY",
    "BrowLeftY",
    "BrowRightY",
    "MouthX",
]

VTS_DEFAULT_01_IDS = {
    "MouthSmile",
    "MouthOpen",
    "Brows",
    "EyeOpenLeft",
    "EyeOpenRight",
    "BrowLeftY",
    "BrowRightY",
}

VTS_DEFAULT_SIGNED_IDS = {
    "EyeLeftX",
    "EyeLeftY",
    "EyeRightX",
    "EyeRightY",
    "MouthX",
}


class VTSAPIError(RuntimeError):
    pass


def clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def clamp(value: float, min_value: float, max_value: float) -> float:
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp_between(value: float, min_or_max_1: float, min_or_max_2: float) -> float:
    low = min(min_or_max_1, min_or_max_2)
    high = max(min_or_max_1, min_or_max_2)
    if value < low:
        return low
    if value > high:
        return high
    return value


def map_value(value: float, from_source: float, to_source: float, from_target: float, to_target: float) -> float:
    if from_source == to_source:
        return (from_target + to_target) / 2.0
    if from_target == to_target:
        return from_target
    return (value - from_source) / (to_source - from_source) * (to_target - from_target) + from_target


def map_and_clamp(value: float, from_source: float, to_source: float, from_target: float, to_target: float) -> float:
    return clamp_between(
        map_value(value, from_source, to_source, from_target, to_target),
        from_target,
        to_target,
    )


def average(value1: float, value2: float) -> float:
    return (value1 + value2) / 2.0


class SlidingWindowQueue:
    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self.values: List[float] = []
        self._average = 0.0
        self._average_exact = 0.0
        self._exact_dirty = False

    @property
    def average(self) -> float:
        return self._average

    @property
    def average_exact(self) -> float:
        if self._exact_dirty:
            if not self.values:
                self._average_exact = 0.0
            else:
                self._average_exact = sum(self.values) / float(len(self.values))
            self._exact_dirty = False
        return self._average_exact

    def enqueue(self, new_value: float) -> float:
        old_count = len(self.values)
        new_count = old_count + 1
        self.values.append(float(new_value))
        avg_candidate = ((float(old_count) * self._average) + float(new_value)) / float(new_count)

        removed_sum = 0.0
        removed = False
        while len(self.values) > self.capacity:
            removed_sum += self.values.pop(0)
            removed = True

        if removed:
            self._average = ((float(new_count) * avg_candidate) - removed_sum) / float(len(self.values))
        else:
            self._average = avg_candidate
        self._exact_dirty = True
        return self._average


def quaternion_normalize(q: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n <= 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    inv = 1.0 / n
    return (x * inv, y * inv, z * inv, w * inv)


def quaternion_multiply(
    q1: Tuple[float, float, float, float], q2: Tuple[float, float, float, float]
) -> Tuple[float, float, float, float]:
    x1, y1, z1, w1 = q1
    x2, y2, z2, w2 = q2
    return (
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    )


def quaternion_inverse(q: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x, y, z, w = q
    n2 = x * x + y * y + z * z + w * w
    if n2 <= 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    inv = 1.0 / n2
    return (-x * inv, -y * inv, -z * inv, w * inv)


def euler_to_quaternion(x_deg: float, y_deg: float, z_deg: float) -> Tuple[float, float, float, float]:
    fx = math.radians(x_deg) * 0.5
    fy = math.radians(y_deg) * 0.5
    fz = math.radians(z_deg) * 0.5
    sx, cx = math.sin(fx), math.cos(fx)
    sy, cy = math.sin(fy), math.cos(fy)
    sz, cz = math.sin(fz), math.cos(fz)
    return (
        cy * sx * cz + sy * cx * sz,
        sy * cx * cz - cy * sx * sz,
        cy * cx * sz - sy * sx * cz,
        cy * cx * cz + sy * sx * sz,
    )


ROTATION_OFFSET_QUAT = euler_to_quaternion(0.0, 0.0, -90.0)


def correct_osf_quaternion(raw_quaternion: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    q = quaternion_multiply(
        (-raw_quaternion[1], -raw_quaternion[0], raw_quaternion[2], raw_quaternion[3]),
        ROTATION_OFFSET_QUAT,
    )
    mirrored = (-q[0], -q[1], -q[2], q[3])
    return quaternion_inverse(mirrored)


def quaternion_to_euler_vts(q_in: Tuple[float, float, float, float]) -> Tuple[float, float, float]:
    q = quaternion_normalize(q_in)
    x, y, z, w = q
    norm_sq = x * x + y * y + z * z + w * w
    singularity_test = x * w - y * z
    if singularity_test > 0.4995 * norm_sq:
        ex = math.pi / 2.0
        ey = 2.0 * math.atan2(y, x)
        ez = 0.0
    elif singularity_test < -0.4995 * norm_sq:
        ex = -math.pi / 2.0
        ey = -2.0 * math.atan2(y, x)
        ez = 0.0
    else:
        ex = math.asin(2.0 * (w * x - y * z))
        ey = math.atan2(2.0 * w * y + 2.0 * z * x, 1.0 - 2.0 * (x * x + y * y))
        ez = math.atan2(2.0 * w * z + 2.0 * x * y, 1.0 - 2.0 * (z * z + x * x))
    k = 57.29578

    ex = math.fmod(ex * k, 360.0)
    ey = math.fmod(ey * k, 360.0)
    ez = math.fmod(ez * k, 360.0)
    return (ex, ey, ez)


class MXPacket:
    def __init__(
        self,
        frame: Dict[str, float],
        mx_values: Dict[str, float],
        meta: Dict[str, float],
        raw_quat: Tuple[float, float, float, float],
        tracker_pos: Tuple[float, float, float],
        landmarks: List[float],
        pose: Dict[str, float],
        position: Dict[str, float],
    ) -> None:
        self.frame = frame
        self.mx_values = mx_values
        self.meta = meta
        self.raw_quat = raw_quat
        self.tracker_pos = tracker_pos
        self.landmarks = landmarks
        self.pose = pose
        self.position = position


class MaxineVTSV3Converter:
    def __init__(self) -> None:
        self.eye_x_queue_l = SlidingWindowQueue(2)
        self.eye_x_queue_r = SlidingWindowQueue(2)
        self.eye_y_queue_l = SlidingWindowQueue(2)
        self.eye_y_queue_r = SlidingWindowQueue(2)
        self.mouth_open_queue = SlidingWindowQueue(2)
        self.eye_left_queue = SlidingWindowQueue(3)
        self.eye_right_queue = SlidingWindowQueue(3)
        self.smoothing_pos_x = SlidingWindowQueue(5)
        self.smoothing_pos_y = SlidingWindowQueue(5)
        self.smoothing_pos_z = SlidingWindowQueue(6)
        self.smoothing_angle_x = SlidingWindowQueue(11)
        self.smoothing_angle_y = SlidingWindowQueue(9)
        self.smoothing_angle_z = SlidingWindowQueue(9)
        self.blink_y_fix_queue = SlidingWindowQueue(9)
        self.smile_fix_queue = SlidingWindowQueue(8)
        self.mouth_x_fix_queue = SlidingWindowQueue(8)
        self.brow_left_queue = SlidingWindowQueue(4)
        self.brow_right_queue = SlidingWindowQueue(4)
        self.eye_bind_queue_left = SlidingWindowQueue(6)
        self.eye_bind_queue_right = SlidingWindowQueue(6)

        self.blink_sensitivity = 0.5
        self.eye_open_sensitivity = 0.5
        self.mouth_open_sensitivity = 0.5
        self.mouth_smile_sensitivity = 0.5
        self.brow_sensitivity = 0.5
        self.eye_blink_linking = 2  # 0 Never, 1 Always, 2 OnlyWhenRotated

        self.mouth_open_calibration: Optional[float] = None
        self.angle_calibration: Optional[Tuple[float, float, float, float]] = None

    def _blend(self, values: Dict[str, float], name: str) -> float:
        return float(values.get(name, 0.0))

    def _landmark_distance(self, tracking_dots: List[float], distance_type: str) -> float:
        if not tracking_dots or len(tracking_dots) < 188 or tracking_dots[0] == 0.0:
            return 0.0

        def dist(i1: int, i2: int) -> float:
            if i1 + 1 >= len(tracking_dots) or i2 + 1 >= len(tracking_dots):
                return 0.0
            return math.hypot(
                tracking_dots[i1] - tracking_dots[i2],
                tracking_dots[i1 + 1] - tracking_dots[i2 + 1],
            )

        if distance_type == "EyeLeft":
            return abs((dist(130, 142) + dist(132, 140) + dist(134, 138)) / 3.0)
        if distance_type == "EyeRight":
            return abs((dist(164, 176) + dist(166, 174) + dist(168, 172)) / 3.0)
        if distance_type == "Mouth":
            return abs((dist(222, 234) + dist(224, 232) + dist(226, 230)) / 3.0)
        if distance_type == "Mouth_Outer":
            return abs((dist(200, 216) + dist(202, 214) + dist(204, 212)) / 3.0)
        if distance_type == "Mouth_TopBottomAll":
            return abs((dist(200, 218) + dist(202, 214) + dist(204, 212)) / 3.0)
        if distance_type == "EyeLeft_Open":
            den = dist(144, 152)
            if den <= 1e-9:
                return 0.0
            return abs(dist(132, 140) / den)
        if distance_type == "EyeRight_Open":
            den = dist(178, 186)
            if den <= 1e-9:
                return 0.0
            return abs(dist(166, 174) / den)
        return 0.0

    def process(self, packet: MXPacket) -> Dict[str, float]:
        mx = packet.mx_values
        raw_q = quaternion_normalize(packet.raw_quat)
        if self.angle_calibration is None and packet.meta.get("face_found_mx", 0.0) > 0.5:
            self.angle_calibration = raw_q
        if self.angle_calibration is not None:
            raw_q = quaternion_multiply(raw_q, quaternion_inverse(self.angle_calibration))

        corrected = correct_osf_quaternion(raw_q)
        vector = quaternion_to_euler_vts(corrected)

        head_rotation_state = 1
        if vector[0] > 21.0:
            head_rotation_state = 2
        elif vector[0] < -21.0:
            head_rotation_state = 3

        # Mouth open ratio (from landmarks branch)
        mouth_ratio = 0.0
        landmark_distance = self._landmark_distance(packet.landmarks, "Mouth_TopBottomAll")
        landmark_distance2 = self._landmark_distance(packet.landmarks, "Mouth")
        if self.mouth_open_calibration is None and landmark_distance > 1e-6 and packet.meta.get("face_found_mx", 0.0) > 0.5:
            self.mouth_open_calibration = landmark_distance2 / landmark_distance
        if self.mouth_open_calibration is not None and landmark_distance > 1e-6:
            mouth_ratio = landmark_distance2 / landmark_distance
            mouth_ratio -= self.mouth_open_calibration
            mouth_ratio -= 0.035
            mouth_ratio = map_and_clamp(mouth_ratio, 0.0, 0.65, 0.0, 1.0)

        # Brows
        brow_l = self._blend(mx, "browInnerUp_L") * 1.3 - self._blend(mx, "browDown_L")
        brow_r = self._blend(mx, "browInnerUp_R") * 1.3 - self._blend(mx, "browDown_R")
        brow_sensitivity_map = map_and_clamp(self.brow_sensitivity, 0.0, 1.0, 0.2, 1.8)
        brow_mixed = (brow_l + brow_r) / 2.0
        brow_l = brow_mixed * 0.2 + brow_l * 0.8
        brow_r = brow_mixed * 0.2 + brow_r * 0.8
        brow_mean = average(brow_l, brow_r)
        brow_left_offset = brow_l - brow_mean
        brow_right_offset = brow_r - brow_mean
        brow_r -= brow_left_offset * 0.35
        brow_l -= brow_right_offset * 0.35
        brow_l *= 3.04
        brow_r *= 3.04
        self.brow_left_queue.enqueue(brow_l)
        self.brow_right_queue.enqueue(brow_r)
        brow_l = self.brow_left_queue.average_exact
        brow_r = self.brow_right_queue.average_exact
        brow_mixed = (brow_l + brow_r) / 2.0

        brow_left_y = map_and_clamp(brow_l * brow_sensitivity_map, -1.0, 1.0, 0.0, 1.0)
        brow_right_y = map_and_clamp(brow_r * brow_sensitivity_map, -1.0, 1.0, 0.0, 1.0)
        brows = map_and_clamp(brow_mixed * brow_sensitivity_map, -1.0, 1.0, 0.0, 1.0)

        # MouthX
        mouth_x_diff = self._blend(mx, "mouthLeft") - self._blend(mx, "mouthRight")
        mouth_x_diff *= 1.6
        mouth_x = mouth_x_diff

        # Smile base
        smile_base = (
            (
                2.0
                - (self._blend(mx, "mouthFrown_L") + self._blend(mx, "mouthFrown_R") + self._blend(mx, "mouthPucker")) / 1.0
                + (
                    self._blend(mx, "mouthSmile_R")
                    + self._blend(mx, "mouthSmile_L")
                    + (self._blend(mx, "mouthDimple_L") + self._blend(mx, "mouthDimple_R")) / 2.0
                )
                / 1.0
            )
            / 4.0
        )
        smile_base *= 0.99
        mouth_smile = smile_base * map_and_clamp(self.mouth_smile_sensitivity, 0.0, 1.0, 0.9, 1.1) * 1.37

        self.smoothing_angle_x.enqueue(vector[0])
        self.smoothing_angle_y.enqueue(-vector[1])
        self.smoothing_angle_z.enqueue(-vector[2] - 90.0)
        average_exact = self.smoothing_angle_x.average_exact
        average_exact2 = self.smoothing_angle_y.average_exact
        average_exact3 = self.smoothing_angle_z.average_exact
        average_exact *= 0.95
        average_exact2 *= 1.0
        angle_z_component = average_exact3 * 0.63
        blink_avg = average(self._blend(mx, "eyeBlink_L"), self._blend(mx, "eyeBlink_R"))
        self.blink_y_fix_queue.enqueue(blink_avg * -3.78)
        average_exact4 = self.blink_y_fix_queue.average_exact
        new_value = smile_base * 13.3
        self.smile_fix_queue.enqueue(new_value)
        new_value = self.smile_fix_queue.average_exact
        new_value2 = mouth_x_diff * 1.6
        self.mouth_x_fix_queue.enqueue(new_value2)
        new_value2 = self.mouth_x_fix_queue.average_exact
        face_angle_x = average_exact
        face_angle_y = average_exact2 + average_exact4 + new_value + smile_base
        face_angle_z = angle_z_component + new_value2

        # Mouth open calculations
        jaw_mouth_blend = (
            1.3 * self._blend(mx, "jawOpen")
            - self._blend(mx, "mouthClose")
            + (self._blend(mx, "mouthLowerDown_L") + self._blend(mx, "mouthLowerDown_R")) / 5.0
        )
        jaw_mouth_blend *= 1.1
        blend_mouth = (
            jaw_mouth_blend * map_and_clamp(self.mouth_open_sensitivity, 0.0, 1.0, 0.4, 1.6)
            + map_and_clamp(self.mouth_open_sensitivity, 0.0, 0.5, -0.1, -0.001)
        )
        new_value3 = mouth_ratio * map_and_clamp(self.mouth_open_sensitivity, 0.0, 1.0, 0.5, 1.5)
        self.mouth_open_queue.enqueue(new_value3)
        new_value3 = self.mouth_open_queue.average_exact
        mouth_open_weight = 0.481
        mouth_open = mouth_open_weight * new_value3 + (1.0 - mouth_open_weight) * blend_mouth

        # Eye gaze
        new_value4 = (self._blend(mx, "eyeLookOut_L") - self._blend(mx, "eyeLookIn_L")) * 1.92 * -1.0
        new_value5 = (self._blend(mx, "eyeLookOut_R") - self._blend(mx, "eyeLookIn_R")) * 1.92
        new_value6 = (self._blend(mx, "eyeLookUp_L") - self._blend(mx, "eyeLookDown_L")) * 3.1
        new_value7 = (self._blend(mx, "eyeLookUp_R") - self._blend(mx, "eyeLookDown_R")) * 3.1
        self.eye_x_queue_l.enqueue(new_value4)
        self.eye_x_queue_r.enqueue(new_value5)
        self.eye_y_queue_l.enqueue(new_value6)
        self.eye_y_queue_r.enqueue(new_value7)
        eye_left_x = self.eye_x_queue_l.average
        eye_right_x = self.eye_x_queue_r.average
        eye_left_y = self.eye_y_queue_l.average
        eye_right_y = self.eye_y_queue_r.average

        landmark_distance3 = self._landmark_distance(packet.landmarks, "EyeLeft_Open")
        landmark_distance4 = self._landmark_distance(packet.landmarks, "EyeRight_Open")
        # Eye open base values
        eye_open_l = 0.5 + (self._blend(mx, "eyeBlink_L") * -0.9 + self._blend(mx, "eyeWide_L") * 1.1) - 0.125
        eye_open_r = 0.5 + (self._blend(mx, "eyeBlink_R") * -0.9 + self._blend(mx, "eyeWide_R") * 1.1) - 0.125
        eye_open_l += 0.06
        eye_open_r += 0.06
        eye_scale = map_and_clamp(smile_base, 0.52, 1.0, 1.0, 1.15)
        eye_lift = map_and_clamp(smile_base, 0.52, 1.0, 0.0, 0.05)
        eye_open_l *= eye_scale
        eye_open_r *= eye_scale
        eye_open_l += eye_lift
        eye_open_r += eye_lift
        eye_sensitivity_offset = clamp01(map_and_clamp(self.eye_open_sensitivity, 0.0, 1.0, -0.1, 0.1))
        eye_sensitivity_scale = clamp_between(map_and_clamp(self.eye_open_sensitivity, 0.0, 1.0, 0.9, 1.2), 0.95, 1.15)
        eye_open_l *= eye_sensitivity_scale
        eye_open_r *= eye_sensitivity_scale
        eye_open_l += eye_sensitivity_offset
        eye_open_r += eye_sensitivity_offset
        mouth_x_asym_factor = abs(clamp_between(mouth_x_diff, -1.0, 1.0) * 0.1)
        eye_open_mean = average(eye_open_l, eye_open_r)
        eye_open_l = mouth_x_asym_factor * eye_open_mean + (1.0 - mouth_x_asym_factor) * eye_open_l
        eye_open_r = mouth_x_asym_factor * eye_open_mean + (1.0 - mouth_x_asym_factor) * eye_open_r
        eye_open_l += abs(clamp_between(mouth_x_diff, 0.0, 1.0)) * 0.2
        eye_open_r += abs(clamp_between(mouth_x_diff, -1.0, 0.0)) * 0.2
        eye_landmark_mix_factor = abs(clamp_between(mouth_x_diff, -1.0, 1.0) * 0.2)
        eye_open_r = eye_landmark_mix_factor * landmark_distance4 + (1.0 - eye_landmark_mix_factor) * eye_open_r
        eye_open_l = eye_landmark_mix_factor * landmark_distance3 + (1.0 - eye_landmark_mix_factor) * eye_open_l
        eye_open_r -= map_and_clamp(brow_mixed, -1.0, 1.0, 0.123, -0.123)
        eye_open_l -= map_and_clamp(brow_mixed, -1.0, 1.0, 0.123, -0.123)
        blink_threshold = map_and_clamp(self.blink_sensitivity, 0.0, 1.0, 0.001, 0.06)
        if eye_open_l < blink_threshold:
            eye_open_l = 0.0
        if eye_open_r < blink_threshold:
            eye_open_r = 0.0
        mouth_open_scale = map_and_clamp(jaw_mouth_blend, 0.0, 1.0, 1.0, 1.06)
        eye_open_l *= mouth_open_scale
        eye_open_r *= mouth_open_scale
        eye_diff_abs = abs(eye_open_l - eye_open_r)
        if (eye_open_r > 0.001 and eye_open_l > 0.001) or not (eye_diff_abs > 0.1):
            eye_open_avg = average(eye_open_l, eye_open_r)
            eye_bind_val_right = eye_open_l - eye_open_avg
            eye_bind_val_left = eye_open_r - eye_open_avg
            self.eye_bind_queue_right.enqueue(eye_bind_val_right * 0.75)
            self.eye_bind_queue_left.enqueue(eye_bind_val_left * 0.75)
        else:
            self.eye_bind_queue_right.enqueue(0.0)
            self.eye_bind_queue_left.enqueue(0.0)
        eye_open_r += self.eye_bind_queue_right.average_exact
        eye_open_l += self.eye_bind_queue_left.average_exact

        if self.eye_blink_linking == 1 or (self.eye_blink_linking == 2 and head_rotation_state != 1):
            if self.eye_blink_linking == 1:
                blink_link_val = (eye_open_r + eye_open_l) / 2.0
                if blink_link_val <= 0.05:
                    eye_open_l = 0.0
                    eye_open_r = 0.0
                else:
                    eye_open_l = blink_link_val
                    eye_open_r = blink_link_val
            else:
                if head_rotation_state == 2:
                    eye_open_l = eye_open_r
                elif head_rotation_state == 3:
                    eye_open_r = eye_open_l

        self.eye_left_queue.enqueue(eye_open_l)
        self.eye_right_queue.enqueue(eye_open_r)
        eye_open_left = self.eye_left_queue.average_exact
        eye_open_right = self.eye_right_queue.average_exact

        self.smoothing_pos_x.enqueue(packet.tracker_pos[0])
        self.smoothing_pos_y.enqueue(packet.tracker_pos[1])
        self.smoothing_pos_z.enqueue(packet.tracker_pos[2])
        face_position_x = self.smoothing_pos_x.average_exact
        face_position_y = self.smoothing_pos_y.average_exact
        face_position_z = self.smoothing_pos_z.average_exact
        face_position_x *= 1.1
        face_position_y *= 1.1
        face_position_z *= 1.2
        face_position_x *= 1.0 - abs(map_and_clamp(average_exact, -90.0, 90.0, -0.45, 0.45))
        face_position_y *= 1.0 - abs(map_and_clamp(average_exact2, -90.0, 90.0, -0.2, 0.2))
        face_position_z *= 1.0 - abs(map_and_clamp(average_exact, -90.0, 90.0, -0.8, 0.8))
        face_position_z -= jaw_mouth_blend * -0.61 * 0.001
        face_position_z -= brow_mixed * -0.42 * 0.001
        face_position_z = clamp_between(face_position_z, -7.0, 9.0)

        return {
            "FacePositionX": face_position_x,
            "FacePositionY": face_position_y,
            "FacePositionZ": face_position_z,
            "FaceAngleX": face_angle_x,
            "FaceAngleY": face_angle_y,
            "FaceAngleZ": face_angle_z,
            "MouthSmile": mouth_smile,
            "MouthOpen": mouth_open,
            "Brows": brows,
            "EyeOpenLeft": eye_open_left,
            "EyeOpenRight": eye_open_right,
            "EyeLeftX": eye_left_x,
            "EyeLeftY": eye_left_y,
            "EyeRightX": eye_right_x,
            "EyeRightY": eye_right_y,
            "BrowLeftY": brow_left_y,
            "BrowRightY": brow_right_y,
            "MouthX": mouth_x,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bridge Maxine MXTracker UDP blendshapes to VTube Studio Plugin API."
    )
    parser.add_argument("--expression-app-path", help="Path to ExpressionApp executable.")
    parser.add_argument(
        "--expression-args",
        default="",
        help="Raw arguments passed to ExpressionApp. Use {camera_index} placeholder if needed.",
    )
    parser.add_argument("--camera-index", type=int, default=None, help="Camera index value.")
    parser.add_argument(
        "--no-spawn-expression-app",
        action="store_true",
        help="Do not launch ExpressionApp; only listen for incoming MX UDP packets.",
    )
    parser.add_argument("--mx-udp-bind", default="127.0.0.1", help="Local bind IP for MX UDP packets.")
    parser.add_argument("--mx-udp-port", type=int, default=9140, help="Local UDP port for MX packets.")
    parser.add_argument("--fps", type=float, default=30.0, help="Maximum injection framerate.")
    parser.add_argument("--print-raw", action="store_true", help="Print incoming MX UDP payload snippets.")
    parser.add_argument(
        "--print-expression-app-log",
        action="store_true",
        help="Print ExpressionApp stdout/stderr lines.",
    )

    parser.add_argument("--vts-host", default="127.0.0.1", help="VTube Studio host.")
    parser.add_argument("--vts-port", type=int, default=8001, help="VTube Studio WebSocket port.")
    parser.add_argument("--plugin-name", default="Maxine Blendshape Bridge", help="Plugin name shown in VTS.")
    parser.add_argument("--plugin-developer", default="Local Bridge", help="Plugin developer shown in VTS.")
    parser.add_argument("--token-file", default=None, help="Token cache JSON file (default: .vts_token.json next to script).")
    parser.add_argument(
        "--wait-inject-response",
        action="store_true",
        help="Wait for API response on every InjectParameterDataRequest (higher latency, useful for debugging).",
    )
    parser.add_argument(
        "--output-mode",
        choices=["default", "custom", "both"],
        default="default",
        help="Inject VTS default parameters, custom MX parameters, or both.",
    )
    parser.add_argument(
        "--param-prefix",
        default="MX",
        help="Prefix for created VTS custom parameter IDs (alphanumeric).",
    )
    parser.add_argument(
        "--skip-create-params",
        action="store_true",
        help="Skip creating custom parameters in VTS.",
    )
    parser.add_argument(
        "--facefound-threshold",
        type=float,
        default=0.0,
        help="Optional extra gate: if max blendshape <= threshold, send faceFound=false.",
    )
    parser.add_argument("--face-angle-x-mult", type=float, default=1.0, help="Multiplier for FaceAngleX.")
    parser.add_argument("--face-angle-y-mult", type=float, default=1.0, help="Multiplier for FaceAngleY.")
    parser.add_argument("--face-angle-z-mult", type=float, default=1.0, help="Multiplier for FaceAngleZ.")
    parser.add_argument("--face-pos-x-mult", type=float, default=1.0, help="Multiplier for FacePositionX.")
    parser.add_argument("--face-pos-y-mult", type=float, default=1.0, help="Multiplier for FacePositionY.")
    parser.add_argument("--face-pos-z-mult", type=float, default=1.0, help="Multiplier for FacePositionZ.")
    parser.add_argument("--eye-x-mult", type=float, default=1.0, help="Multiplier for EyeLeftX/EyeRightX.")
    parser.add_argument("--eye-y-mult", type=float, default=1.0, help="Multiplier for EyeLeftY/EyeRightY.")
    parser.add_argument("--mouth-x-mult", type=float, default=1.0, help="Multiplier for MouthX.")
    return parser.parse_args()


def build_command(path: str, args_template: str, camera_index: Optional[int]) -> List[str]:
    if not path:
        raise ValueError("Missing ExpressionApp path.")
    rendered = args_template.strip()
    if "{camera_index}" in rendered:
        rendered = rendered.replace("{camera_index}", "" if camera_index is None else str(camera_index))
    args = shlex.split(rendered, posix=False) if rendered else []
    return [path, *args]


def parse_mx_udp_payload(payload: bytes) -> Optional[MXPacket]:
    text = payload.decode("utf-8", errors="ignore").replace("\x00", "").strip()
    if not text:
        return None
    if "{" in text and "}" in text:
        text = text[text.find("{") : text.rfind("}") + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("exp"), list):
        return None

    mx_vals: Dict[str, float] = {}
    exp = data["exp"]
    for i, name in enumerate(MAXINE_EXPR_INDEX_NAMES):
        if i >= len(exp):
            break
        mx_vals[name] = safe_float(exp[i], 0.0)

    frame = {name: 0.0 for name in ARKIT_BLENDSHAPES}
    frame.update(
        {
            "browDownLeft": mx_vals.get("browDown_L", 0.0),
            "browDownRight": mx_vals.get("browDown_R", 0.0),
            "browInnerUp": max(mx_vals.get("browInnerUp_L", 0.0), mx_vals.get("browInnerUp_R", 0.0)),
            "browOuterUpLeft": mx_vals.get("browOuterUp_L", 0.0),
            "browOuterUpRight": mx_vals.get("browOuterUp_R", 0.0),
            "cheekPuff": max(mx_vals.get("cheekPuff_L", 0.0), mx_vals.get("cheekPuff_R", 0.0)),
            "cheekSquintLeft": mx_vals.get("cheekSquint_L", 0.0),
            "cheekSquintRight": mx_vals.get("cheekSquint_R", 0.0),
            "eyeBlinkLeft": mx_vals.get("eyeBlink_L", 0.0),
            "eyeBlinkRight": mx_vals.get("eyeBlink_R", 0.0),
            "eyeLookDownLeft": mx_vals.get("eyeLookDown_L", 0.0),
            "eyeLookDownRight": mx_vals.get("eyeLookDown_R", 0.0),
            "eyeLookInLeft": mx_vals.get("eyeLookIn_L", 0.0),
            "eyeLookInRight": mx_vals.get("eyeLookIn_R", 0.0),
            "eyeLookOutLeft": mx_vals.get("eyeLookOut_L", 0.0),
            "eyeLookOutRight": mx_vals.get("eyeLookOut_R", 0.0),
            "eyeLookUpLeft": mx_vals.get("eyeLookUp_L", 0.0),
            "eyeLookUpRight": mx_vals.get("eyeLookUp_R", 0.0),
            "eyeSquintLeft": mx_vals.get("eyeSquint_L", 0.0),
            "eyeSquintRight": mx_vals.get("eyeSquint_R", 0.0),
            "eyeWideLeft": mx_vals.get("eyeWide_L", 0.0),
            "eyeWideRight": mx_vals.get("eyeWide_R", 0.0),
            "jawForward": mx_vals.get("jawForward", 0.0),
            "jawLeft": mx_vals.get("jawLeft", 0.0),
            "jawOpen": mx_vals.get("jawOpen", 0.0),
            "jawRight": mx_vals.get("jawRight", 0.0),
            "mouthClose": mx_vals.get("mouthClose", 0.0),
            "mouthDimpleLeft": mx_vals.get("mouthDimple_L", 0.0),
            "mouthDimpleRight": mx_vals.get("mouthDimple_R", 0.0),
            "mouthFrownLeft": mx_vals.get("mouthFrown_L", 0.0),
            "mouthFrownRight": mx_vals.get("mouthFrown_R", 0.0),
            "mouthFunnel": mx_vals.get("mouthFunnel", 0.0),
            "mouthLeft": mx_vals.get("mouthLeft", 0.0),
            "mouthLowerDownLeft": mx_vals.get("mouthLowerDown_L", 0.0),
            "mouthLowerDownRight": mx_vals.get("mouthLowerDown_R", 0.0),
            "mouthPressLeft": mx_vals.get("mouthPress_L", 0.0),
            "mouthPressRight": mx_vals.get("mouthPress_R", 0.0),
            "mouthPucker": mx_vals.get("mouthPucker", 0.0),
            "mouthRight": mx_vals.get("mouthRight", 0.0),
            "mouthRollLower": mx_vals.get("mouthRollLower", 0.0),
            "mouthRollUpper": mx_vals.get("mouthRollUpper", 0.0),
            "mouthShrugLower": mx_vals.get("mouthShrugLower", 0.0),
            "mouthShrugUpper": mx_vals.get("mouthShrugUpper", 0.0),
            "mouthSmileLeft": mx_vals.get("mouthSmile_L", 0.0),
            "mouthSmileRight": mx_vals.get("mouthSmile_R", 0.0),
            "mouthStretchLeft": mx_vals.get("mouthStretch_L", 0.0),
            "mouthStretchRight": mx_vals.get("mouthStretch_R", 0.0),
            "mouthUpperUpLeft": mx_vals.get("mouthUpperUp_L", 0.0),
            "mouthUpperUpRight": mx_vals.get("mouthUpperUp_R", 0.0),
            "noseSneerLeft": mx_vals.get("noseSneer_L", 0.0),
            "noseSneerRight": mx_vals.get("noseSneer_R", 0.0),
            "tongueOut": 0.0,
        }
    )
    for key in frame:
        frame[key] = clamp01(frame[key])

    cnf_raw = safe_float(data.get("cnf", 0.0), 0.0)
    cnf_norm = clamp01(cnf_raw / 43.0)
    face_count_raw = safe_float(data.get("faceCount", data.get("num", 0.0)), 0.0)
    meta = {
        "num": face_count_raw,
        "faceCount": face_count_raw,
        "cnf": cnf_raw,
        "cnf_norm": cnf_norm,
        "fps": safe_float(data.get("fps", 0.0), 0.0),
        "face_found_mx": 1.0 if (face_count_raw > 0.0 and cnf_norm >= (1.0 / 3.0)) else 0.0,
    }

    raw_quat = (0.0, 0.0, 0.0, 1.0)
    rot = data.get("rot")
    if isinstance(rot, list) and len(rot) >= 4:
        raw_quat = (
            safe_float(rot[0], 0.0),
            safe_float(rot[1], 0.0),
            -safe_float(rot[2], 0.0),
            -safe_float(rot[3], 1.0),
        )

    tracker_pos = (0.0, 0.0, 0.0)
    pos = data.get("pos")
    if isinstance(pos, list) and len(pos) >= 3:
        tracker_pos = (
            safe_float(pos[0], 0.0) / 2.0,
            (-1.0 + safe_float(pos[1], 0.0)) / 2.0,
            (-(38.0 + safe_float(pos[2], 0.0))) / 3.0,
        )

    landmarks: List[float] = []
    pts = data.get("pts")
    if isinstance(pts, list) and len(pts) == 254:
        landmarks = [safe_float(v, 0.0) for v in pts[2:]]

    corrected = correct_osf_quaternion(quaternion_normalize(raw_quat))
    euler = quaternion_to_euler_vts(corrected)
    pose = {
        "headPitch": float(euler[0]),
        "headYaw": float(-euler[1]),
        "headRoll": float((-euler[2]) - 90.0),
    }
    position = {"posX": float(tracker_pos[0]), "posY": float(tracker_pos[1]), "posZ": float(tracker_pos[2])}
    return MXPacket(frame, mx_vals, meta, raw_quat, tracker_pos, landmarks, pose, position)


def _sanitize_prefix(prefix: str) -> str:
    p = re.sub(r"[^A-Za-z0-9]", "", prefix)
    if not p:
        p = "MX"
    return p


def build_parameter_ids(prefix: str) -> Dict[str, str]:
    p = _sanitize_prefix(prefix)
    out: Dict[str, str] = {}
    used = set()
    for name in ARKIT_BLENDSHAPES:
        clean = re.sub(r"[^A-Za-z0-9]", "", name)
        pid = (p + clean[:1].upper() + clean[1:])[:32]
        if len(pid) < 4:
            pid = (pid + "Param")[:4]
        base = pid
        suffix = 1
        while pid in used:
            pid = f"{base[:30]}{suffix:02d}"[:32]
            suffix += 1
        used.add(pid)
        out[name] = pid
    return out


def build_default_vts_values(
    converter: MaxineVTSV3Converter,
    packet: MXPacket,
    args: argparse.Namespace,
) -> Dict[str, float]:
    out = converter.process(packet)
    out["FaceAngleX"] *= float(args.face_angle_x_mult)
    out["FaceAngleY"] *= float(args.face_angle_y_mult)
    out["FaceAngleZ"] *= float(args.face_angle_z_mult)
    out["FacePositionX"] *= float(args.face_pos_x_mult)
    out["FacePositionY"] *= float(args.face_pos_y_mult)
    out["FacePositionZ"] *= float(args.face_pos_z_mult)
    out["EyeLeftX"] *= float(args.eye_x_mult)
    out["EyeRightX"] *= float(args.eye_x_mult)
    out["EyeLeftY"] *= float(args.eye_y_mult)
    out["EyeRightY"] *= float(args.eye_y_mult)
    out["MouthX"] *= float(args.mouth_x_mult)
    return out


def clamp_default_fallback(param_id: str, value: float) -> float:
    if param_id in VTS_DEFAULT_01_IDS:
        return clamp01(value)
    if param_id in VTS_DEFAULT_SIGNED_IDS:
        return clamp(value, -1.0, 1.0)
    return value


class VTSClient:
    def __init__(
        self,
        host: str,
        port: int,
        plugin_name: str,
        plugin_developer: str,
        token_file: Path,
        wait_inject_response: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.plugin_name = plugin_name
        self.plugin_developer = plugin_developer
        self.token_file = token_file
        self.wait_inject_response = wait_inject_response
        self.ws: Optional[websocket.WebSocket] = None
        self.key = f"{host}:{port}:{plugin_name}:{plugin_developer}"
        self._inject_sent = 0

    @staticmethod
    def _is_would_block_error(exc: BaseException) -> bool:
        if isinstance(exc, OSError):
            if getattr(exc, "winerror", None) == 10035:
                return True
            if getattr(exc, "errno", None) in (errno.EWOULDBLOCK, errno.EAGAIN):
                return True
        return False

    def connect(self) -> None:
        url = f"ws://{self.host}:{self.port}"
        self.ws = websocket.create_connection(url, timeout=6)

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None

    def _load_tokens(self) -> dict:
        if not self.token_file.exists():
            return {}
        try:
            return json.loads(self.token_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_token(self, token: str) -> None:
        tokens = self._load_tokens()
        tokens[self.key] = token
        self.token_file.write_text(json.dumps(tokens, indent=2), encoding="utf-8")

    def _get_cached_token(self) -> Optional[str]:
        return self._load_tokens().get(self.key)

    def _request(self, message_type: str, data: Optional[dict] = None) -> dict:
        if self.ws is None:
            raise VTSAPIError("WebSocket not connected.")
        request_id = str(uuid4())
        req = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": request_id,
            "messageType": message_type,
        }
        if data is not None:
            req["data"] = data
        self.ws.send(json.dumps(req))

        while True:
            raw = self.ws.recv()
            msg = json.loads(raw)
            if msg.get("requestID") != request_id:
                continue
            if msg.get("messageType") == "APIError":
                err_data = msg.get("data", {})
                raise VTSAPIError(
                    f"APIError id={err_data.get('errorID')} message={err_data.get('message')}"
                )
            return msg

    def _send_no_wait(self, message_type: str, data: Optional[dict] = None) -> None:
        if self.ws is None:
            raise VTSAPIError("WebSocket not connected.")
        req = {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": str(uuid4()),
            "messageType": message_type,
        }
        if data is not None:
            req["data"] = data
        self.ws.send(json.dumps(req))

    def _drain_messages(self, max_messages: int = 64) -> None:
        if self.ws is None:
            return
        if getattr(self.ws, "sock", None) is None:
            return
        try:
            previous_timeout = self.ws.sock.gettimeout()
        except Exception:
            previous_timeout = 6.0
        try:
            self.ws.settimeout(0.0)
            for _ in range(max_messages):
                try:
                    raw = self.ws.recv()
                except websocket.WebSocketTimeoutException:
                    break
                except TimeoutError:
                    break
                except OSError as exc:
                    if self._is_would_block_error(exc):
                        break
                    raise
                if not raw:
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("messageType") == "APIError":
                    err_data = msg.get("data", {})
                    print(
                        f"VTS async APIError id={err_data.get('errorID')} message={err_data.get('message')}",
                        file=sys.stderr,
                    )
        finally:
            try:
                self.ws.settimeout(previous_timeout if previous_timeout is not None else 6.0)
            except Exception:
                pass

    def authenticate(self) -> None:
        token = self._get_cached_token()
        if token:
            try:
                rsp = self._request(
                    "AuthenticationRequest",
                    {
                        "pluginName": self.plugin_name,
                        "pluginDeveloper": self.plugin_developer,
                        "authenticationToken": token,
                    },
                )
                if rsp.get("data", {}).get("authenticated"):
                    return
            except VTSAPIError:
                pass

        token_rsp = self._request(
            "AuthenticationTokenRequest",
            {"pluginName": self.plugin_name, "pluginDeveloper": self.plugin_developer},
        )
        token = token_rsp.get("data", {}).get("authenticationToken")
        if not token:
            raise VTSAPIError("AuthenticationTokenRequest succeeded but no token returned.")
        self._save_token(token)
        auth_rsp = self._request(
            "AuthenticationRequest",
            {
                "pluginName": self.plugin_name,
                "pluginDeveloper": self.plugin_developer,
                "authenticationToken": token,
            },
        )
        if not auth_rsp.get("data", {}).get("authenticated"):
            raise VTSAPIError(f"Authentication failed: {auth_rsp.get('data', {}).get('reason')}")

    def create_parameter(self, parameter_name: str, explanation: str, min_value: float = 0.0, max_value: float = 1.0, default_value: float = 0.0) -> None:
        self._request(
            "ParameterCreationRequest",
            {
                "parameterName": parameter_name,
                "explanation": explanation,
                "min": float(min_value),
                "max": float(max_value),
                "defaultValue": float(default_value),
            },
        )

    def get_input_parameter_ranges(self) -> Dict[str, Tuple[float, float]]:
        rsp = self._request("InputParameterListRequest")
        data = rsp.get("data", {})
        out: Dict[str, Tuple[float, float]] = {}

        for bucket in ("defaultParameters", "customParameters"):
            items = data.get(bucket, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                param_id = item.get("name")
                if not isinstance(param_id, str) or not param_id:
                    continue
                min_value = safe_float(item.get("min"), float("-inf"))
                max_value = safe_float(item.get("max"), float("inf"))
                if min_value > max_value:
                    min_value, max_value = max_value, min_value
                out[param_id] = (min_value, max_value)

        return out

    def inject_parameters(self, values: List[dict], face_found: bool) -> None:
        payload = {"faceFound": bool(face_found), "mode": "set", "parameterValues": values}
        if self.wait_inject_response:
            self._request("InjectParameterDataRequest", payload)
            return
        try:
            self._send_no_wait("InjectParameterDataRequest", payload)
        except OSError as exc:
            if self._is_would_block_error(exc):
                # Temporary socket backpressure; try draining once and drop this frame.
                self._drain_messages(256)
                return
            raise
        self._inject_sent += 1
        if self._inject_sent % 8 == 0:
            self._drain_messages(128)


def main() -> int:
    args = parse_args()
    converter = MaxineVTSV3Converter()

    proc = None
    expr_log_tail: deque[str] = deque(maxlen=80)
    expr_log_thread: Optional[threading.Thread] = None
    if not args.no_spawn_expression_app:
        cmd = build_command(args.expression_app_path or "", args.expression_args, args.camera_index)
        expr_cwd = str(Path(cmd[0]).resolve().parent)
        print(f"Starting ExpressionApp in {expr_cwd}: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                cwd=expr_cwd,
            )
            if proc.stdout is not None:
                def _pump_expression_app_output() -> None:
                    assert proc is not None and proc.stdout is not None
                    for line in proc.stdout:
                        clean = line.rstrip("\r\n")
                        if clean:
                            expr_log_tail.append(clean)
                            if args.print_expression_app_log:
                                print(f"[ExpressionApp] {clean}")

                expr_log_thread = threading.Thread(
                    target=_pump_expression_app_output,
                    name="ExpressionAppLogPump",
                    daemon=True,
                )
                expr_log_thread.start()
        except OSError as exc:
            print(f"Failed to start ExpressionApp: {exc}", file=sys.stderr)
            return 1
    else:
        print("Not starting ExpressionApp (no-spawn mode).")

    use_default = args.output_mode in {"default", "both"}
    use_custom = args.output_mode in {"custom", "both"}

    param_ids = build_parameter_ids(args.param_prefix) if use_custom else {}
    pose_param_ids = (
        {
            "headYaw": f"{_sanitize_prefix(args.param_prefix)}HeadYaw",
            "headPitch": f"{_sanitize_prefix(args.param_prefix)}HeadPitch",
            "headRoll": f"{_sanitize_prefix(args.param_prefix)}HeadRoll",
        }
        if use_custom
        else {}
    )
    vts = VTSClient(
        host=args.vts_host,
        port=args.vts_port,
        plugin_name=args.plugin_name,
        plugin_developer=args.plugin_developer,
        token_file=Path(args.token_file).resolve() if args.token_file else SCRIPT_DIR / ".vts_token.json",
        wait_inject_response=bool(args.wait_inject_response),
    )

    print(f"Connecting to VTube Studio API at ws://{args.vts_host}:{args.vts_port}")
    try:
        vts.connect()
        vts.authenticate()
        print("VTube Studio API authentication succeeded.")
    except Exception as exc:
        print(f"VTube Studio API connection/auth failed: {exc}", file=sys.stderr)
        if proc is not None and proc.poll() is None:
            proc.terminate()
        return 1

    print(f"Output mode: {args.output_mode}")
    print(f"Inject mode: {'sync-wait' if args.wait_inject_response else 'async-fast'}")

    input_param_ranges: Dict[str, Tuple[float, float]] = {}
    try:
        input_param_ranges = vts.get_input_parameter_ranges()
        print(f"Fetched {len(input_param_ranges)} parameter ranges from VTS.")
    except Exception as exc:
        print(f"Warning: InputParameterListRequest failed ({exc}). Using fallback clamping.")

    if use_custom and not args.skip_create_params:
        created = 0
        for name in ARKIT_BLENDSHAPES:
            pid = param_ids[name]
            try:
                vts.create_parameter(pid, f"Maxine blendshape {name}")
                created += 1
            except VTSAPIError:
                # Parameter might already exist or belong to another plugin; continue.
                pass
        for pose_name, pid in pose_param_ids.items():
            try:
                vts.create_parameter(pid, f"Maxine pose {pose_name}", min_value=-90.0, max_value=90.0, default_value=0.0)
                created += 1
            except VTSAPIError:
                pass
        print(f"Parameter creation attempted. Created/updated: {created}/{len(ARKIT_BLENDSHAPES) + 3}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((args.mx_udp_bind, int(args.mx_udp_port)))
    except OSError as exc:
        print(f"Failed to bind MX UDP socket {args.mx_udp_bind}:{args.mx_udp_port}: {exc}", file=sys.stderr)
        vts.close()
        if proc is not None and proc.poll() is None:
            proc.terminate()
        return 1
    sock.setblocking(False)

    print(f"Listening for Maxine UDP on {args.mx_udp_bind}:{args.mx_udp_port}")
    frame_interval = 0.0 if args.fps <= 0 else 1.0 / args.fps
    next_send = time.monotonic()
    sent = 0
    last_log = time.monotonic()
    recv_since_log = 0
    sent_since_log = 0
    latest_packet: Optional[MXPacket] = None

    try:
        while True:
            if proc is not None and proc.poll() is not None:
                print(f"ExpressionApp exited with code {proc.returncode}.")
                if expr_log_tail:
                    print("ExpressionApp last log lines:")
                    for line in list(expr_log_tail)[-20:]:
                        print(f"[ExpressionApp] {line}")
                break
            while True:
                try:
                    payload, src = sock.recvfrom(65535)
                except BlockingIOError:
                    break
                except OSError as exc:
                    if VTSClient._is_would_block_error(exc):
                        break
                    raise

                parsed = parse_mx_udp_payload(payload)
                if parsed is None:
                    continue
                latest_packet = parsed
                recv_since_log += 1

                if args.print_raw:
                    snip = payload.decode("utf-8", errors="ignore").replace("\x00", "").strip().replace("\n", " ")
                    print(f"[MX-UDP {src[0]}:{src[1]}] {snip[:220]}")

            now = time.monotonic()
            sends_this_tick = 0
            while latest_packet is not None and (frame_interval <= 0.0 or now >= next_send) and sends_this_tick < 2:
                packet = latest_packet
                frame = packet.frame
                meta = packet.meta

                max_val = max(frame.values()) if frame else 0.0
                face_found = (meta.get("face_found_mx", 0.0) > 0.5) and (max_val > args.facefound_threshold)
                values: List[dict] = []
                default_values = build_default_vts_values(converter, packet, args)

                if use_default:
                    for param_id in VTS_DEFAULT_PARAMETER_IDS:
                        value = float(default_values.get(param_id, 0.0))
                        bounds = input_param_ranges.get(param_id)
                        if bounds is not None:
                            value = clamp(value, bounds[0], bounds[1])
                        else:
                            value = clamp_default_fallback(param_id, value)
                        values.append({"id": param_id, "value": value})

                if use_custom:
                    for name in ARKIT_BLENDSHAPES:
                        pid = param_ids[name]
                        value = float(frame.get(name, 0.0))
                        bounds = input_param_ranges.get(pid)
                        if bounds is not None:
                            value = clamp(value, bounds[0], bounds[1])
                        values.append({"id": pid, "value": value})

                    for pose_name in ("headYaw", "headPitch", "headRoll"):
                        pid = pose_param_ids[pose_name]
                        if pose_name == "headYaw":
                            value = float(default_values.get("FaceAngleY", packet.pose.get("headYaw", 0.0)))
                        elif pose_name == "headPitch":
                            value = float(default_values.get("FaceAngleX", packet.pose.get("headPitch", 0.0)))
                        else:
                            value = float(default_values.get("FaceAngleZ", packet.pose.get("headRoll", 0.0)))
                        bounds = input_param_ranges.get(pid)
                        if bounds is not None:
                            value = clamp(value, bounds[0], bounds[1])
                        values.append({"id": pid, "value": value})

                if values:
                    try:
                        vts.inject_parameters(values, face_found)
                    except Exception as exc:
                        if VTSClient._is_would_block_error(exc):
                            pass
                        else:
                            print(f"Inject failed ({exc}), attempting reconnect...")
                            try:
                                vts.close()
                                time.sleep(0.2)
                                vts.connect()
                                vts.authenticate()
                                try:
                                    input_param_ranges = vts.get_input_parameter_ranges()
                                except Exception:
                                    pass
                            except Exception as reconnect_exc:
                                print(f"Reconnect failed: {reconnect_exc}", file=sys.stderr)
                                latest_packet = None
                                break
                    else:
                        sent += 1
                        sent_since_log += 1

                sends_this_tick += 1
                if frame_interval > 0.0:
                    next_send += frame_interval
                else:
                    break

            if frame_interval > 0.0 and now - next_send > 1.0:
                next_send = now + frame_interval

            if now - last_log > 2.0:
                elapsed = max(now - last_log, 1e-6)
                in_fps = recv_since_log / elapsed
                out_fps = sent_since_log / elapsed
                last_log = now
                recv_since_log = 0
                sent_since_log = 0
                if latest_packet is not None:
                    frame = latest_packet.frame
                    meta = latest_packet.meta
                    max_val = max(frame.values()) if frame else 0.0
                    default_values = build_default_vts_values(converter, latest_packet, args)
                    face_found = (meta.get("face_found_mx", 0.0) > 0.5) and (max_val > args.facefound_threshold)
                    default_log = ""
                    if use_default:
                        default_log = (
                            f" mouth={default_values.get('MouthOpen', 0.0):.3f}"
                            f" smile={default_values.get('MouthSmile', 0.0):.3f}"
                            f" yaw={default_values.get('FaceAngleY', 0.0):.2f}"
                        )
                    print(
                        f"Sent={sent} inFPS={in_fps:.1f} outFPS={out_fps:.1f} faceFound={int(face_found)} "
                        f"faceCount={int(meta.get('faceCount', meta.get('num', 0.0)))} cnf={meta.get('cnf_norm', 0.0):.3f} maxExp={max_val:.3f}{default_log}"
                    )
                else:
                    print(f"Sent={sent} inFPS={in_fps:.1f} outFPS={out_fps:.1f} waiting_for_packets=1")

            time.sleep(0.001)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        sock.close()
        vts.close()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        if expr_log_thread is not None:
            expr_log_thread.join(timeout=0.5)
        print(f"Bridge stopped. Injected {sent} frames.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
