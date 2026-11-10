# Frame Player

Frame Player plays website streams and direct video on [Steam Frame](https://partner.steamgames.com/doc/steamhardware/steamframe): YouTube, Twitch, anything else [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports, plus HLS, DASH, and MP4 URLs. It is built for the headset's Snapdragon 8 Gen 3, keeps a short history and a favorites list, and shows which decoder is actually running.

The window is a normal Linux app. On Steam Frame it runs in desktop mode (a flat panel in the headset) and can be added to Steam as a non-Steam game. Controls are large enough for the controller's laser pointer, and a gamepad template drives play, seek, volume, and 360° look.

## Why libmpv

Decoding and presentation stay in [mpv](https://mpv.io/) / libmpv. The Python UI only resolves URLs, picks a quality, and draws controls.

- libmpv already speaks HLS, DASH, MP4, and separate audio+video URLs, including live streams.
- Its `hwdec` option is a real fallback list. If Iris rejects a stream, playback continues on the CPU without a second player.
- `v4l2m2m-copy` is the FFmpeg path that works with Qualcomm's stateful V4L2 decoder.
- The same code runs on x86_64 for development and on aarch64 SteamOS.

A browser would hide the decoder. A from-scratch demuxer would redo yt-dlp and FFmpeg. libmpv is the smallest stack that can both resolve a YouTube URL and name the hardware decoder on screen.

## Hardware decode on Steam Frame

Steam Frame is a Snapdragon 8 Gen 3 (SM8650, Adreno 750) running SteamOS. The video engine is Qualcomm Iris, exposed as a **stateful V4L2 mem2mem** decoder. On current SteamOS builds that node is `/dev/video-dec0` (often also `/dev/video22`) and the driver name is `qcom-iris-decoder`.

What that node actually advertises, checked against Frame reports (kernel 6.18, Mesa Turnip):

| Path | Usable on Steam Frame? |
| --- | --- |
| V4L2 stateful M2M (`v4l2m2m-copy`) | Yes. This is the path Frame Player requests first. H.264, HEVC, and VP9, up to 8192×8192. |
| V4L2 zero-copy (`v4l2m2m`) | No. Upstream FFmpeg does not export Iris frames as DRM-PRIME, so mpv cannot import them into the window. |
| Vulkan Video | No. Adreno 750 / Turnip does not expose `VK_KHR_video_decode_queue`. |
| VA-API | Not shipped. An experimental third-party Iris VA-API driver exists; Frame Player will use VA-API only when `vainfo` succeeds (typical on x86 PCs). |
| NVDEC | PC NVIDIA only. |

Valve's current Iris fork does **not** list AV1, even though the VPU firmware contains an AV1 decoder and mainline Iris gained it in Linux 7.0. AV1, including a lot of YouTube 8K, is software decode until SteamOS exposes it.

`v4l2m2m-copy` still is hardware decode. Iris decodes the bitstream. FFmpeg then copies the NV12 frame into the OpenGL window. That copy is the price of the upstream decoder. It is much cheaper than decoding 4K or 8K HEVC on the CPU, and it is the path that works inside an embedded libmpv window. At 8K the copy is roughly 50 MB per frame, so 8K60 is heavy on memory bandwidth. If it stutters, drop to 4K in the quality list.

On a PC with no Iris node, the plan is VA-API, then NVDEC, then Vulkan, using copy-back modes first so the embedded window can show the frames. If none of those exist, mpv is asked for `auto-copy` and then software.

Set `FRAMEPLAYER_HWDEC=no` before launch to force the CPU. Set `FRAMEPLAYER_HWDEC=v4l2m2m-copy` to pin Iris. The on-screen badge reads `Hardware · v4l2m2m-copy` or `Software fallback`. Stats also show the codec, decoded resolution, fps, and dropped frames.

## 8K, 3D, and VR

The quality ceiling defaults to **8K** (4320p). Auto prefers a hardware-decodable codec at the highest resolution that fits: HEVC, then H.264, then VP9. AV1 is offered in the list with a software badge and is not chosen automatically on Steam Frame. Frames wider or taller than 8192 pixels are marked as over the Iris limit and will not be selected as hardware.

3D and VR files are normal frames with a packing convention. An 8K side-by-side video is one picture with a left eye and a right eye. A 360° video is one equirectangular picture. Iris decodes the whole picture. The player then:

- crops to the left, right, top, or bottom eye for flat 3D (side-by-side or over-under)
- projects 360° and 180° video on the GPU and lets you look around

Auto-detect uses spherical/stereo metadata, then words in the title (`360`, `VR180`, `SBS`, `over-under`), then only extreme aspect ratios. A normal 16:9 film is not treated as 360°. The Eye and VR menus override the guess.

The desktop window is one flat panel, so both of your eyes see the same image. Frame Player cannot submit a different picture to each eye of the headset. OpenXR binocular stereo is a different kind of app. What you get here is the full decoded frame, one eye at full panel size, and mouse, touch, or right-stick look for 360° and 180°.

## Install

YouTube needs a JavaScript runtime. Deno is preferred. Node 22 or newer also works. yt-dlp is installed with its default extras, including the EJS challenge scripts. If `deno` is on `PATH`, it is used automatically. Node is enabled as well when it is installed.

### For your user

This installs Frame Player into `~/.local/opt/frameplayer`, adds a desktop entry, and puts `frameplayer` and `frameplayer-uninstall` on `~/.local/bin`. The script creates `~/.local` when that directory is not there yet.

```sh
./scripts/install.sh
frameplayer
```

### Flatpak (preferred on SteamOS)

SteamOS does not let you write the root filesystem. Flatpak installs into your user. The build script also installs `frameplayer-uninstall` into `~/.local/bin`.

```sh
flatpak install -y flathub org.freedesktop.Platform//26.08 org.freedesktop.Sdk//26.08
./scripts/build-flatpak.sh
flatpak run app.frameplayer.Player
```

The manifest builds mpv 0.42 with libmpv, installs Deno 2.9.8 (YouTube), and installs the Python app. It is not pinned to one CPU architecture: build it on the Frame for aarch64, or on a PC for x86_64. The sandbox asks for `--device=all` because the Iris node is `/dev/video-dec0` and Steam Input is `/dev/input/js*`. `--device=dri` alone does not expose either.

Confirm the bundled FFmpeg can see the V4L2 decoders:

```sh
flatpak run --command=ffmpeg app.frameplayer.Player -hide_banner -decoders | grep v4l2m2m
```

You want `h264_v4l2m2m`, `hevc_v4l2m2m`, and `vp9_v4l2m2m`.

### Checkout, for development

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/frameplayer
```

### Uninstall

Install leaves `frameplayer-uninstall` next to `frameplayer`. Run that when you want the app gone:

```sh
frameplayer-uninstall
```

The same script is `./scripts/uninstall.sh` in a checkout. It removes the user install (`~/.local/opt/frameplayer`), the desktop entry, the icon, and the Flatpak `app.frameplayer.Player` if that is installed. Favorites, history, and settings stay. Delete those too with:

```sh
frameplayer-uninstall --purge
```

A system-wide Flatpak that this user cannot remove prints the `sudo flatpak uninstall` command instead of failing the rest of the cleanup. A non-Steam shortcut added inside Steam is removed from the Steam library by hand.

## Add it to Steam

1. Install the Flatpak (or the local checkout).
2. In Steam: **Games → Add a Non-Steam Game to My Library → Browse**.
3. Pick the executable:
   - user install: `~/.local/bin/frameplayer`
   - Flatpak user install: `~/.local/share/flatpak/exports/bin/app.frameplayer.Player`
   - Flatpak system install: `/var/lib/flatpak/exports/bin/app.frameplayer.Player`
4. In **Properties**, name it Frame Player. Enable Steam Input and use a gamepad template.
5. Launch it from the library. On Steam Frame it opens as a flat window in the dashboard, the same way other desktop apps do. From Desktop mode, the controller aims a laser at the buttons; face buttons and sticks still work as below.

A local checkout can be added the same way. Point Steam at `scripts/run.sh`.

## Controls

| Input | Action |
| --- | --- |
| Paste and play | Read the clipboard and open it |
| Click the picture | Pause, or bring the controls back |
| Double-click or Start or F | Full screen |
| A or Space | Play or pause. A activates a focused button while controls are open |
| B or Esc | Leave full screen, or hide the controls |
| D-pad | Move between controls while they are open. Seek and volume while they are hidden |
| Left stick | Seek and volume |
| Right stick | Look around in 360° and 180° video |
| Right stick click | Reset the view |
| LB / RB | Activate the focused control, or seek 10 seconds |
| LT / RT or Shift+arrows | Seek 30 seconds |
| X or I | Stats |
| Y | Favorite |
| Select | Library |

Drag the picture to look around in VR video. The FOV slider is in the VR row.

## Verify hardware decoding on the headset

```sh
./scripts/verify-hwdec.sh
```

You want a device whose name contains `iris` or `qcom-iris-decoder`, and output formats `H264`, `HEVC`, and `VP90`. Then play an H.264 or HEVC file (not AV1). The badge should say `Hardware · v4l2m2m-copy` and the stats block should name `/dev/video-dec0`.

While it plays, the log at `~/.local/share/frameplayer/mpv.log` (inside Flatpak: `~/.var/app/app.frameplayer.Player/data/frameplayer/mpv.log`) should mention hardware decoding. `Software fallback` together with an AV1 line is expected on today's SteamOS driver. `Software fallback` on H.264 means the node was missing, the sandbox could not see `/dev/video*`, or the decoder rejected the stream.

```sh
# codec list from the driver
v4l2-ctl -d /dev/video-dec0 --list-formats-out
# player plan as JSON
flatpak run --command=frameplayer app.frameplayer.Player --probe
```

## Limitations

- DRM catalogs do not work. Netflix, Disney+, Prime Video, Max, Hulu, Apple TV, Paramount+, and Peacock use Widevine. yt-dlp cannot produce a clear stream, and Frame Player refuses those hosts up front.
- AV1 is software decode on current Steam Frame kernels.
- Frames larger than 8192 pixels on a side exceed Iris and fall back to software. Some "12K" VR masters are in that bucket.
- 8K is decoded as one frame, then copied into the window. 8K60 can stutter even when Iris is keeping up.
- The window cannot show a different image to each eye. 3D is one eye at a time, or the packed frame.
- Cubemap VR is shown packed. Equirectangular 360° and 180° are projected.
- YouTube needs Deno or Node, and some networks get HTTP 403 on the media host unless you are signed in with cookies. Frame Player does not ship a cookie importer.
- Live streams with no DVR window cannot be seeked.

## Troubleshooting

**Badge stays on Software fallback for H.264.** Run `./scripts/verify-hwdec.sh`. If there is no Iris node, the kernel or firmware is missing and no player can hardware-decode. If the node exists but Flatpak cannot see it, reinstall so `--device=all` is granted, or allow device access in Flatseal.

**The app crashes as soon as a hardware decoder is selected.** Some older FFmpeg builds segfault inside `v4l2m2m-copy` on Venus. Quit, start once with `FRAMEPLAYER_HWDEC=no`, and update mpv / the Flatpak runtime. The in-app "Hardware decoding" checkbox in the library panel does the same thing for the next launch.

**YouTube says "Sign in to confirm you're not a bot" or the stream 403s.** Install Deno 2.3 or newer so yt-dlp can solve the player challenge. A datacenter or VPN address is often blocked even after that. Try another network. Twitch VODs and direct HLS URLs do not need the YouTube challenge.

**No sound.** The player tries PipeWire, then PulseAudio, then ALSA. Steam Frame uses PipeWire. A missing audio server still leaves the picture up; the log names the audio error.

**360° view is black outside the picture.** That is the back of a 180° video, or the view has been yawed past the recorded hemisphere. Press Reset view.

**Stats say the frame is over 8192.** Pick a lower quality chip. The 8K chip is the highest one whose long side is still inside Iris.

**Flatpak FFmpeg has no v4l2m2m decoders.** The Freedesktop 26.08 runtime's FFmpeg is expected to include them. If `ffmpeg -decoders` does not list `h264_v4l2m2m`, the sandbox cannot use Iris. File an issue against the runtime or rebuild FFmpeg with `--enable-v4l2-m2m` and point mpv at it.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests
```

Headless playback, useful on a machine without a headset:

```sh
.venv/bin/python -m frameplayer --probe
.venv/bin/python -m frameplayer --play-test /path/to/video.mp4 --seconds 3
.venv/bin/python -m frameplayer --play-test "https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_fmp4/master.m3u8" --seconds 4
```

`tests/test_playback.py` builds a side-by-side clip and a 7680×4320 clip with ffmpeg and checks that libmpv decodes them. `tests/test_gl.py` opens the window under a display, checks that the left eye of a red/blue frame is red, and checks that the center of a 360° frame samples the front of the picture.

## What was verified without a Steam Frame

This tree was exercised on x86_64 Ubuntu with mpv 0.37 and FFmpeg's `h264_v4l2m2m` / `hevc_v4l2m2m` / `vp9_v4l2m2m` decoders compiled in. There is no Iris node and no VA-API device here, so hardware decode cannot succeed. The fallback path was tested by pinning `v4l2m2m-copy` and confirming the stats report `Software fallback` while the picture still advances.

Verified here:

- Unit tests for the Iris plan (`v4l2m2m-copy` first, H.264/HEVC/VP9, AV1 and over-8192 excluded), HLS quality ranking, stereo crops, and 360°/180° projection math.
- A generated **7680×4320** H.264 file decoded at 7680×4320.
- A side-by-side color frame: the left-eye crop is red, the right half is blue and not shown.
- A 360° frame: the GPU shader's forward view lands on the green band at the front of the equirectangular picture.
- Apple's public HLS sample (`img_bipbop_adv_example_fmp4`) played for 4 seconds at 1920×1080, H.264, 60 fps, with the software fallback reported because this machine has no decoder device.
- yt-dlp resolved the public YouTube video "Me at the zoo". The media URLs then returned **HTTP 403** from this network (YouTube's bot / datacenter block, including on the HLS segments). Playback of that title was not completed here. On a normal connection the same resolve path is what the window plays.

Not verified, because there is no headset in this environment:

- Iris actually decoding H.264, HEVC, or VP9 on `/dev/video-dec0`.
- 8K60 thermal and memory-bandwidth behavior of `v4l2m2m-copy`.
- Steam Input's virtual gamepad on the Frame. The mapping is the standard Linux joystick layout and is covered by unit tests, not by a physical controller.
- Flatpak-builder against `org.freedesktop.Platform//26.08`. The manifest is in the tree; it was not built in this environment.
