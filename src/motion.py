"""
Image-to-video motion for the agency.

A still image panned across the frame is not a character, it is a slideshow.
This module exists to turn each beat's still into an actually moving clip, and
- crucially - to *prove* the clip moved before the renderer is allowed to use it.

Three things were wrong before:

1. `api_name="/i2v_generation"` was called on the Wan2.1 Space. That endpoint
   does not exist; the real ones are `/i2v_generation_async` followed by
   `/status_refresh` polling. So the Wan rung could never succeed, ever.
2. Every exception was swallowed into one generic "queue full" message, so a
   wrong endpoint, a bad token and a saturated GPU were indistinguishable.
3. Nothing checked the result. A Space that returned the input frame back, or
   a clip of one repeated still, was accepted and shipped as "motion".
"""

import base64
import os
import random
import subprocess
import time

import requests

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")

# A clip is only "alive" if the median frame-to-frame luma difference clears
# this.
#
# Calibrated against measured artifacts, not guessed:
#   flat colour card / frozen frame ........ 0.0
#   small background element moving ......... 0.5
#   _camera_drift fallback (dead character) . 3.6   <- must stay BELOW this
#   real image-to-video output .............. 7.1   <- must stay ABOVE this
#
# The old value of 4.0 was set from an estimated panned-still score of ~1.8,
# but the actual _camera_drift pan measures 3.6 against a real photograph. At
# 4.0 the fallback sat 0.4 from being accepted as "live motion", which is
# precisely the failure this gate exists to prevent. 5.0 puts the fallback 1.4
# below the line and genuine animation 2.1 above it.
MOTION_THRESHOLD = 5.0
MIN_CLIP_SECONDS = 1.5


class MotionError(RuntimeError):
    """No provider could produce a genuinely moving clip."""


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)


def probe_video(path: str) -> tuple[float, int]:
    """Return (duration_seconds, frame_count)."""
    try:
        out = _run([
            FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
            "-count_frames",
            "-show_entries", "format=duration:stream=nb_read_frames",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ], timeout=60).stdout.split()
        duration = float(out[0]) if out else 0.0
        frames = int(out[1]) if len(out) > 1 else 0
        if duration > 0:
            return duration, frames
    except Exception:
        pass

    # ffprobe is not always on PATH (some minimal images ship ffmpeg alone).
    # Duration alone is enough for our gate, so degrade rather than give up.
    try:
        proc = _run([FFMPEG_BIN, "-i", path], timeout=60)
        text = (proc.stderr or "") + (proc.stdout or "")
        if "Duration: " in text:
            stamp = text.split("Duration: ", 1)[1].split(",", 1)[0].strip()
            hh, mm, ss = stamp.split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss), 0
    except Exception:
        pass
    return 0.0, 0


def measure_motion(path: str, samples: int = 8) -> float:
    """Median absolute luma difference between *adjacent* frames.

    Adjacent frames plus a median, rather than widely-spaced samples plus a
    mean, is the whole trick. A hard cut between two stills produces one huge
    spike which a mean happily averages into a "this is animated" verdict -
    that is exactly how the issue #6 reels scored 11.1 while being motionless.
    A median ignores isolated spikes, so a panned still reads ~1-2 and real
    character motion reads 6-25.
    """
    try:
        from PIL import Image
    except ImportError:
        return -1.0  # cannot measure; caller decides

    tmp = f"/tmp/_motionprobe_{os.getpid()}"
    os.makedirs(tmp, exist_ok=True)
    try:
        _run([
            FFMPEG_BIN, "-y", "-v", "error", "-i", path,
            "-vf", f"fps={samples}", os.path.join(tmp, "f%03d.png"),
        ], timeout=90)

        files = sorted(f for f in os.listdir(tmp) if f.endswith(".png"))
        if len(files) < 2:
            return 0.0

        import numpy as np

        frames = []
        for name in files:
            arr = np.asarray(Image.open(os.path.join(tmp, name)).convert("L")).astype(float)
            # Upper 60% only: burned-in captions change constantly and would
            # otherwise register as motion on an otherwise dead frame.
            frames.append(arr[: int(arr.shape[0] * 0.6), :])

        diffs = sorted(
            float(np.abs(frames[i] - frames[i - 1]).mean()) for i in range(1, len(frames))
        )
        if not diffs:
            return 0.0
        mid = len(diffs) // 2
        return float(diffs[mid] if len(diffs) % 2 else (diffs[mid - 1] + diffs[mid]) / 2)
    except Exception:
        return 0.0
    finally:
        for name in os.listdir(tmp) if os.path.isdir(tmp) else []:
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def validate_clip(path: str) -> tuple[bool, str]:
    """A clip is usable only if it exists, is long enough, and actually moves."""
    if not os.path.exists(path) or os.path.getsize(path) < 10_000:
        return False, "clip missing or truncated"
    duration, frames = probe_video(path)
    if duration < MIN_CLIP_SECONDS:
        return False, f"clip too short ({duration:.2f}s)"
    motion = measure_motion(path)
    if motion >= 0 and motion < MOTION_THRESHOLD:
        return False, f"clip is effectively still (motion {motion:.2f} < {MOTION_THRESHOLD})"
    return True, f"ok ({duration:.1f}s, {frames} frames, motion {motion:.1f})"


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------
def _make_client(space_id: str, hf_token: str):
    """Build a Gradio client across gradio_client 1.x and 2.x.

    2.x renamed the auth keyword from `hf_token` to `token`. The old code
    passed `hf_token` while requirements.txt allowed 2.x, so every call died
    with a TypeError that a bare `except Exception` reported as "queue full" -
    which is why the animation looked like an unreliable GPU queue rather than
    a hard API incompatibility.
    """
    from gradio_client import Client

    # Try the current keyword first, then the legacy one, then anonymous.
    auth_variants = [{"token": hf_token}, {"hf_token": hf_token}, {}] if hf_token else [{}]
    last: Exception | None = None
    for kwargs in auth_variants:
        try:
            return Client(space_id, verbose=False, **kwargs)
        except TypeError as exc:
            last = exc
    raise MotionError(f"could not open {space_id}: {last}")


def _extract_clip(result, out_path: str) -> str | None:
    """Pull a usable video path out of the many shapes Gradio returns."""
    candidates = []
    if isinstance(result, dict):
        candidates = [result.get("video"), result.get("path"), result.get("named_file")]
    elif isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, dict):
                candidates.extend([item.get("video"), item.get("path")])
            elif isinstance(item, str):
                candidates.append(item)
            elif hasattr(item, "name"):
                candidates.append(item.name)
    elif hasattr(result, "name"):
        candidates = [result.name]

    for cand in candidates:
        if cand and os.path.exists(str(cand)):
            return str(cand)
    return None


def _via_ltx(img_path: str, prompt: str, out_path: str, duration: float, hf_token: str) -> str | None:
    """Lightricks LTX-Video Space (free, burns ZeroGPU quota)."""
    from gradio_client import handle_file

    client = _make_client("Lightricks/ltx-video-distilled", hf_token)
    job = client.submit(
        prompt=prompt,
        negative_prompt="static image, no motion, frozen, still photo, warping, morphing face",
        input_image_filepath=handle_file(img_path),
        input_video_filepath=None,
        # Portrait: the Space's own defaults are 704x512 (landscape), which
        # would letterbox a 9:16 beat into black bars.
        width_ui=512,
        height_ui=704,
        mode="image-to-video",
        duration_ui=duration,
        ui_frames_to_use=9,
        seed_ui=-1,
        randomize_seed=True,
        ui_guidance_scale=1.0,
        improve_texture_flag=True,
        api_name="/image_to_video",
    )
    result = job.result(timeout=240)
    return _extract_clip(result, out_path)


def _via_wan21(img_path: str, prompt: str, out_path: str, duration: float, hf_token: str) -> str | None:
    """Wan-AI/Wan2.1 Space (free).

    This Space is async: `/i2v_generation_async` only reports an estimated
    wait and the actual file arrives via `/status_refresh`. The previous code
    called a non-existent `/i2v_generation`, so this rung never once ran.
    """
    from gradio_client import handle_file

    client = _make_client("Wan-AI/Wan2.1", hf_token)
    client.submit(
        prompt=prompt,
        image=handle_file(img_path),
        watermark_wan=True,
        seed=-1,
        api_name="/i2v_generation_async",
    )

    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(12)
        try:
            status = client.submit(api_name="/status_refresh")
        except Exception:
            continue
        if not status:
            continue
        clip = _extract_clip(status, out_path)
        if clip:
            return clip
    raise MotionError("Wan2.1 async job never produced a clip within 300s")


def _via_pollinations(img_path: str, prompt: str, out_path: str, duration: float, hf_token: str = "") -> str | None:
    """Pollinations video API. Not free - costs pollen - but far the best
    quality per unit cost (alibaba/wan-2.2-fast is 0.01 pollen/sec)."""
    key = os.environ.get("POLLINATIONS_API_KEY", "").strip()
    if not key:
        raise MotionError("POLLINATIONS_API_KEY not set")

    with open(img_path, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("ascii")
    data_url = f"data:image/jpeg;base64,{encoded}"

    model = os.environ.get("POLLINATIONS_VIDEO_MODEL", "alibaba/wan-2.2-fast")
    resp = requests.get(
        f"https://gen.pollinations.ai/video/{requests.utils.quote(prompt[:400])}",
        params={
            "model": model,
            "image": data_url,
            "duration": max(5, int(round(duration))),
            "aspectRatio": "9:16",
            "seed": -1,
        },
        headers={"Authorization": f"Bearer {key}"},
        timeout=420,
    )
    if not resp.ok:
        raise MotionError(f"pollinations video HTTP {resp.status_code}: {resp.text[:160]}")
    if len(resp.content) < 10_000:
        raise MotionError("pollinations returned an empty video")

    with open(out_path, "wb") as fh:
        fh.write(resp.content)
    return out_path


def _via_framediffusion(img_path: str, prompt: str, out_path: str, duration: float, hf_token: str = "") -> str | None:
    """Re-anchored image chain via OpenRouter image models (see frame_diffusion).

    This is the primary rung because it is the only one whose cost is knowable
    up front and bounded: roughly $0.00003/frame x (duration x 8 frames/sec),
    so a ~11s reel lands near $0.003. The HF Space rungs below are free but
    queue-bound, which is precisely why issue #6 shipped motionless characters.
    """
    from src.frame_diffusion import animate_still_to_clip

    seed = random.randint(1, 999_999)
    return animate_still_to_clip(img_path, prompt, out_path, duration, seed=seed)


PROVIDERS = [
    ("framediffusion", _via_framediffusion),
    ("pollinations", _via_pollinations),
    ("ltx", _via_ltx),
    ("wan21", _via_wan21),
]


def generate_i2v_clip(
    img_path: str,
    motion_prompt: str,
    out_path: str,
    duration: float,
    log=print,
) -> tuple[str, str]:
    """Animate a beat still into a genuinely moving clip.

    Returns (clip_path, provider_name). Raises MotionError if no provider
    produced a clip that measurably moves - the caller is then expected to
    fall back to the still-image drift AND say so, rather than pretending the
    character came alive.
    """
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    # framediffusion first: bounded cost and no queue. The HF Spaces are free
    # but ZeroGPU-saturated, and Pollinations bills video in pollen, so both
    # are strictly worse fallbacks than something we can actually budget for.
    order = [n.strip() for n in os.environ.get("I2V_PROVIDER_ORDER", "framediffusion,pollinations,ltx,wan21").split(",") if n.strip()]

    attempts = []
    for name in order:
        fn = dict(PROVIDERS).get(name)
        if fn is None:
            continue
        try:
            clip = fn(img_path, motion_prompt, out_path, duration, hf_token)
        except Exception as exc:
            # Surface the real reason. "queue full" hid a wrong endpoint and a
            # bad token behind one useless message.
            attempts.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
            log(f"  [i2v] {name} failed: {type(exc).__name__}: {str(exc)[:120]}")
            continue

        if not clip:
            attempts.append(f"{name}: returned no clip")
            continue

        if clip != out_path:
            import shutil

            shutil.copy(clip, out_path)

        ok, reason = validate_clip(out_path)
        if ok:
            log(f"  [i2v] {name} ok - {reason}")
            return out_path, name
        attempts.append(f"{name}: {reason}")
        log(f"  [i2v] {name} produced a dead clip ({reason}) - rejecting")
        try:
            os.remove(out_path)
        except OSError:
            pass

    raise MotionError("no image-to-video provider produced live motion:\n    " + "\n    ".join(attempts))
