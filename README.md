# Oisahu

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

Oisahu is a  bridge for Nvidia tracking in  VTube Studio. Streams over LAN (or from a VM) via Plugin API, RTX quality tracking on a remote machine (e.g. Linux).

## Use case

VTube Studio on Linux cannot run NVIDIA's `ExpressionApp.exe` (Windows/RTX only).  
This tool runs on your **Windows machine** (where the RTX GPU and webcam are), captures face tracking data, and sends it over the network to **VTube Studio on another machine** via the VTube Studio Plugin API WebSocket.

```
Windows (RTX GPU + webcam)              Linux machine / Linux VM
──────────────────────────              ─────────────────────────
ExpressionApp.exe  (NVIDIA Maxine)
        ↓ UDP 127.0.0.1:9140
maxine_vts_api_bridge.py
        ↓ WebSocket ws://192.168.x.x:8001
                                ──────→ VTube Studio Plugin API
                                        face parameters injected
                                        directly into the model
```


## Requirements

- Windows PC with NVIDIA RTX GPU
- VTube Studio installed via Steam (Windows or Linux)
- VTube Studio RTX Tracking DLC (provides `ExpressionApp.exe`)
- Python 3.10+

```bash
pip install -r requirements.txt
```

## Quick start

Copy `run.bat.example` to `run.bat`, edit the IP address and paths, then double-click it.

```bat
python maxine_vts_api_bridge.py ^
  --output-mode default ^
  --expression-app-path "D:\SteamLibrary\steamapps\common\VTube Studio\VTube Studio_Data\StreamingAssets\MXTracker\v2\ExpressionApp.exe" ^
  --expression-args "--show=False --landmarks=True --model_path=.\models --cam_res=1920x1080 --expr_mode=2 --camera=0 --camera_cap=0 --cam_fps=60 --fps_limit=60 --use_opencl=False --cam_api=0 --pose_mode=2 --face_model=models\face_model3.nvf --render_model=models\face_model3.nvf --filter=55" ^
  --mx-udp-bind 127.0.0.1 ^
  --mx-udp-port 9140 ^
  --vts-host 192.168.1.x ^
  --vts-port 8001 ^
  --fps 60
```

Replace `192.168.1.x` with your Linux machine's local IP.
Replace the `--expression-app-path` with the actual path to `ExpressionApp.exe` on your system (bundled with the VTube Studio RTX Tracking DLC). 

On first run, VTube Studio will show a **plugin permission popup** - approve it.

## VTube Studio setup (Linux side)

1. Open VTube Studio and load your model
2. Go to **Settings → General** and make sure the Plugin API is enabled (port 8001)
3. Make sure the Linux machine is on the same local network as the Windows machine
4. Run the bridge on Windows - approve the plugin popup in VTS
5. Your model's default parameters (`FaceAngleX/Y/Z`, `MouthOpen`, `EyeOpenLeft/Right`, etc.) will start moving

No special VTS configuration needed - the bridge injects the same built-in parameter IDs that VTS's own trackers use.

## ExpressionApp path

The `ExpressionApp.exe` is bundled with the **VTube Studio RTX Tracking DLC** on Steam:

```
<Steam>\steamapps\common\VTube Studio\VTube Studio_Data\StreamingAssets\MXTracker\v2\ExpressionApp.exe
```

## Key options

| Flag | Default | Description |
|---|---|---|
| `--vts-host` | `127.0.0.1` | IP of the machine running VTube Studio |
| `--vts-port` | `8001` | VTube Studio Plugin API WebSocket port |
| `--fps` | `30` | Tracking injection rate |
| `--output-mode` | `default` | `default` = built-in VTS params, `custom` = raw MX params, `both` |
| `--camera` | `0` | Webcam index |
| `--expression-app-path` | - | Full path to `ExpressionApp.exe` |
| `--no-spawn-expression-app` | - | Don't launch ExpressionApp (listen only) |
| `--print-raw` | - | Print incoming UDP packets for debugging |
| `--print-expression-app-log` | - | Print ExpressionApp stdout |

## Output modes

### `default` (recommended)
Injects VTube Studio's built-in parameter IDs directly - works with any model without setup:
`FacePositionX/Y/Z`, `FaceAngleX/Y/Z`, `MouthSmile`, `MouthOpen`, `Brows`, `EyeOpenLeft/Right`, `EyeLeftX/Y`, `EyeRightX/Y`, `BrowLeftY`, `BrowRightY`, `MouthX`

### `custom`
Creates and injects per-blendshape ARKit parameters with a prefix (default `MX`):
`MXEyeBlinkLeft`, `MXJawOpen`, etc. May be useful for advanced model rigging.

### `both`
Injects both sets simultaneously.

## How it works

1. Launches `ExpressionApp.exe` (NVIDIA Maxine RTX face tracker)
2. Reads the UDP JSON stream it outputs on `127.0.0.1:9140`  
   Each packet contains `exp[52]` (blendshape floats), `rot[4]` (quaternion), `pts[254]` (landmarks)
3. Converts raw Maxine indices → ARKit blendshape names
4. Applies a transfer function to produce natural-feeling parameter values
5. Injects parameters into VTube Studio via WebSocket Plugin API

See `MAXINE_VTS_REVERSE_NOTES.md` for full details on the reverse-engineered conversion.

## TODO / planned features

### ExpressionApp runtime commands
ExpressionApp accepts UDP commands on `127.0.0.1:(9160 + cameraID)`. None of these are currently wired up in the bridge:

| Command | Description |
|---|---|
| `{"cmd":"calibrate"}` | Trigger a new expression calibration (resets neutral pose) |
| `{"cmd":"show_preview"}` | Show the ExpressionApp face preview window |
| `{"cmd":"hide_preview"}` | Hide the face preview window |
| `{"cmd":"show_config_dialog"}` | Open the ExpressionApp config/settings UI |
| `{"cmd":"set_calibration <params>"}` | Load a specific calibration from saved coefficients |
| `{"cmd":"send_debug_info"}` | Request a debug info dump from ExpressionApp |
| `{"cmd":"quit"}` | Gracefully shut down ExpressionApp |

Planned: add `--calibrate` CLI flag and optional hotkey support to send calibration commands at runtime without restarting.

### Other planned improvements
- **Blendshape scaling/calibration curves**  per-shape min/max remapping (similar to DrBomb's snap/interpolation system), useful for eye blink snap-to-close and expression sensitivity tuning
- **Config file support**  save all CLI args to a JSON config so `run.bat` is not needed
- **Auto-reconnect**  automatically reconnect to VTube Studio if the WebSocket drops
- **Camera selector** interactive camera/cap picker like DrBomb, instead of requiring manual `--camera_cap` index lookup

### Why not just run ExpressionApp on Linux via Proton/Wine?
ExpressionApp is dependent on NVIDIA Maxine SDK, it is not plug and play on Linux. It may be possible to run it via Proton/Wine, but that would require way more effort and some edits on VTS .dlls to skip some Windows checks.

### Why not just relay the raw ExpressionApp UDP data and let VTS do the conversion?
This would need the same .dll edits to bypass th Windows check to get the tracker running, also a "fake" ExpressionApp to run on Linux. 
Additionally (from my testing) the camera indexes must be aligned in the real ExpressionApp and Vtube Studio, which would be hard to guarantee since the two apps run on different machines with different camera drivers. 
But it can be more convenient since you have the native calibration. I may try to understand if I can make the VTS and ExpressionApp to work even with different camera indexes (I already try and correct for the port number, but it didn't seem's to work)
