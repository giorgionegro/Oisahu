# Maxine Conversion Notes

## 1) Incoming payload expected by tracker

From the tracker payload (note: the original payload field name `num` is referred to below as `faceCount` for clarity):

- `exp[53]`: processed expression coefficients used for tracking conversion.
- `rex[53]`: raw/pre-transfer expression coefficients (used for finetuning graph/debug only).
- `rot[4]`: rotation quaternion from tracker.
- `pos[3]`: tracker position.
- `pts[254]`: `[camWidth, camHeight, 252 landmark values]`.
- `num` (referred to below as `faceCount`): face count.
- `cnf`: confidence.
- `fps`, `cam`, `shw`, `cal`, `scl`, `xpt`.

## 2) First-stage transforms

When JSON is received by the bridge/tracker integration:

- `currentRawAngleFromTracker = (rot[0], rot[1], -rot[2], -rot[3])`
- `currentFacePosition = (pos[0]/2, (-1 + pos[1])/2, -(38 + pos[2])/3)`
- `confidence = clamp01(cnf / 43)`
- Face considered found only if:
  - `faceCount > 0` and
  - `confidence >= 0.333333`

Blendshape arrays:

- `currentBlendshapes = exp` (must be length 53)
- `rawBlendshapes = rex` (if present, length 53)

Landmarks:

- If `pts` length is 254, the first two entries (camera width/height) are removed and 252 landmark values are kept for distance computations.

Ports used by typical tracker integrations:

- Receive tracker data: `UDP 9140 + instanceID`
- Send commands to tracker: `127.0.0.1:(9160 + cameraID)`

Commands commonly supported by the tracker application include:

- `show_preview`, `hide_preview`, `show_config_dialog`, `calibrate`, `set_calibration ...`, `send_debug_info`, `quit`.

## 3) Expression transfer function (finetuning model)

The finetuning UI and sample code both use this transfer model per blendshape:

- `y = 1 - pow(max(1 - max(x - offset, 0) * scale, 0), exponent)`
- Optional global boost blend:
  - `y = boost * y + (1 - boost) * x`

This is the same model implemented in AR-SDK sample code for expression weight normalization.

## 4) Interpolation and quaternion correction

Before converter math, tracking systems typically interpolate from previous to current packet:

- Quaternion: slerp
- Position: lerp
- Blendshapes: lerp
- Landmarks: lerp

A standard quaternion correction is applied before Euler conversion. The correction sequence used by compatible converters is:

1. Reorder/sign flip: `(-raw.y, -raw.x, raw.z, raw.w)`
2. Multiply by a Z rotation offset (equivalent to Euler(0,0,-90))
3. Mirror xyz signs and inverse the quaternion

Euler conversion then follows the custom math used by the converter.

## 5) converter math


Sensitivity defaults are typically 0.5.

### Brows

- `browL = browInnerUp_L * 1.3 - browDown_L`
- `browR = browInnerUp_R * 1.3 - browDown_R`
- Mixed + anti-asymmetry correction, then scale `* 3.04`, then queue smoothing.
- `BrowLeftY/BrowRightY/Brows` are mapped `[-1,1] -> [0,1]` with brow sensitivity map `[0,1] -> [0.2,1.8]`.

### MouthX

- `MouthX = (mouthLeft - mouthRight) * 1.6`

### MouthSmile

- `smileBase = (2 - (mouthFrown_L + mouthFrown_R + mouthPucker) + (mouthSmile_R + mouthSmile_L + (mouthDimple_L + mouthDimple_R)/2)) / 4`
- `smileBase *= 0.99`
- `MouthSmile = smileBase * map(MouthSmileSensitivity, 0..1 -> 0.9..1.1) * 1.37`

### Face angles

Using corrected/interpolated quaternion Euler (`x,y,z`) and smoothing queues:

- `X = avgX * 0.95`
- `Y = avg(-y) + blinkFix + smileFix + smileBase`
- `Z = (avg(-z - 90) * 0.63) + mouthXFix`

Where:

- `blinkFix` queue uses average blink `* -3.78`
- `smileFix` queue uses `smileBase * 13.3`
- `mouthXFix` queue uses `MouthX * 1.6`

### MouthOpen

- Blendshape branch:
  - `a = 1.3*jawOpen - mouthClose + (mouthLowerDown_L + mouthLowerDown_R)/5`
  - `a *= 1.1`
  - `blendMouth = a * map(MouthOpenSensitivity, 0..1 -> 0.4..1.6) + map(MouthOpenSensitivity, 0..0.5 -> -0.1..-0.001)`
- Landmark branch:
  - `ratio = MouthDistance / MouthTopBottomAllDistance`
  - `ratio -= mouthOpenCalibration`
  - `ratio -= 0.035`
  - `ratio = mapClamp(ratio, 0..0.65 -> 0..1)`
  - `landmarkMouth = ratio * map(MouthOpenSensitivity, 0..1 -> 0.5..1.5)`
  - queue-smoothed
- Final:
  - `MouthOpen = 0.481 * landmarkMouth + 0.519 * blendMouth`

### Eye gaze (XY)

- `EyeLeftX = (eyeLookOut_L - eyeLookIn_L) * 1.92 * -1`
- `EyeRightX = (eyeLookOut_R - eyeLookIn_R) * 1.92`
- `EyeLeftY = (eyeLookUp_L - eyeLookDown_L) * 3.1`
- `EyeRightY = (eyeLookUp_R - eyeLookDown_R) * 3.1`
- Small queue smoothing is applied.

### EyeOpen L/R

Base:

- `L = 0.5 + (-0.9*eyeBlink_L + 1.1*eyeWide_L) - 0.125 + 0.06`
- `R = 0.5 + (-0.9*eyeBlink_R + 1.1*eyeWide_R) - 0.125 + 0.06`

Then multiple corrections:

- Smile-dependent scaling and lift
- EyeOpen sensitivity offset/scale
- Asymmetry blending by `MouthX`
- Additional landmark influence (`EyeLeft_Open`, `EyeRight_Open`)
- Brow coupling subtraction
- Blink threshold clamp:
  - threshold = map(BlinkSensitivity, 0..1 -> 0.001..0.06)
- Mouth-open scaling (`map(a, 0..1 -> 1..1.06)`) 
- Eye bind queues
- Optional eye blink linking (Never / Always / OnlyWhenRotated)
- Final queue smoothing for EyeOpenLeft/EyeOpenRight

### Face position XYZ

From smoothed transformed tracker position:

- Multiply:
  - `X *= 1.1`
  - `Y *= 1.1`
  - `Z *= 1.2`
- Angle-dependent damping:
  - `X *= 1 - abs(map(FaceAngleX, -90..90 -> -0.45..0.45))`
  - `Y *= 1 - abs(map(FaceAngleY, -90..90 -> -0.2..0.2))`
  - `Z *= 1 - abs(map(FaceAngleX, -90..90 -> -0.8..0.8))`
- Tiny expression-dependent z nudges:
  - `Z -= a * -0.61 * 0.001`
  - `Z -= browAvg * -0.42 * 0.001`
- Clamp:
  - `Z = clamp(Z, -7, 9)`

## 6) Blendshape index order expected for `exp[53]`

Typical order for the expression array (indices 0..52):

`browDown_L, browDown_R, browInnerUp_L, browInnerUp_R, browOuterUp_L, browOuterUp_R, cheekPuff_L, cheekPuff_R, cheekSquint_L, cheekSquint_R, eyeBlink_L, eyeBlink_R, eyeLookDown_L, eyeLookDown_R, eyeLookIn_L, eyeLookIn_R, eyeLookOut_L, eyeLookOut_R, eyeLookUp_L, eyeLookUp_R, eyeSquint_L, eyeSquint_R, eyeWide_L, eyeWide_R, jawForward, jawLeft, jawOpen, jawRight, mouthClose, mouthDimple_L, mouthDimple_R, mouthFrown_L, mouthFrown_R, mouthFunnel, mouthLeft, mouthLowerDown_L, mouthLowerDown_R, mouthPress_L, mouthPress_R, mouthPucker, mouthRight, mouthRollLower, mouthRollUpper, mouthShrugLower, mouthShrugUpper, mouthSmile_L, mouthSmile_R, mouthStretch_L, mouthStretch_R, mouthUpperUp_L, mouthUpperUp_R, noseSneer_L, noseSneer_R`
