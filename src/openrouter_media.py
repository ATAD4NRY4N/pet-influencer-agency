"""
Free-first media providers for the agency.

Everything goes through OpenRouter's unified API so the whole pipeline needs a
single credential (OPENROUTER_API_KEY) instead of a scattering of vendor keys.

Two rules this module exists to enforce:

1. A generated image is either VALIDATED or it never reaches the renderer.
   The previous pipeline silently substituted a flat `#1e293b` ffmpeg colour
   card when an image provider failed, and that placeholder was then stitched
   into the finished reel as 4.6s of black screen. We now raise instead.

2. Text-to-image is free, but *identity-consistent* text-to-image is not.
   A text prompt alone cannot hold a human face stable between episodes, so
   the ladder has an optional rung that accepts a reference portrait.
"""

import base64
import os
import random
import time

import requests

import src.billing as billing

try:  # Pillow is used purely to validate what a provider handed back.
    from PIL import Image, ImageStat
except ImportError:  # pragma: no cover - validation degrades to byte checks
    Image = None
    ImageStat = None

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Spoken once by the bootstrap voice clip so the cloner knows what it is hearing.
VOICE_REFERENCE_TRANSCRIPT = (
    "Right, let's get straight to it. The little ones are already causing chaos "
    "and honestly I could not ask for a better morning."
)
HTTP_TIMEOUT = 90

# An image smaller than this is almost certainly an error card or a 1x1 stub.
MIN_IMAGE_BYTES = 20_000
# Standard deviation of the luma channel. Flat colour cards land near 0-3,
# real photographs sit comfortably above 15 even in low light.
MIN_LUMA_STDDEV = 12.0
TARGET_ASPECT = 9 / 16
ASPECT_TOLERANCE = 0.14

# Ordered image ladder. Each rung is (key, model_id).
#   pollinations  - $0 via OpenRouter's free bridge, text-only, BUT cannot lock
#                   a face, so Maya drifts between episodes.
#   gemini        - ~$0.00003/image, accepts a reference portrait, so Maya's
#                   face, glasses and hair survive across episodes. PAID.
#
# The order below is the *preference* order. src.billing.py then removes any
# rung the current billing policy refuses, so under the default free-only
# policy this collapses to pollinations regardless of the order.
IMAGE_LADDER = {
    "seedream": "bytedance-seed/seedream-5-0-lite",
    "gemini31": "google/gemini-3.1-flash-image",
    "flux_pro": "black-forest-labs/flux.2-pro",
    "gemini_lite": "google/gemini-3.1-flash-lite-image",
    "gemini": "google/gemini-2.5-flash-image",
    # Design-tooling models, kept reachable but never preferred. Neither can
    # hold a character: ming-image-0.1-design-layer is image-to-image and
    # *splits a flattened design into RGBA layers* (it needs an input image we
    # do not have), and ming-image-0.1-design accepts zero reference images
    # (`input_references: 0-0`), so it cannot possibly keep Barnaby's ear the
    # same grey across episodes. Verified against OpenRouter's images
    # catalogue at /api/v1/images/models, which is a different list from the
    # chat catalogue at /api/v1/models.
    "ming": "inclusionai/ming-image-0.1-design",
    "ming_layer": "inclusionai/ming-image-0.1-design-layer",
    "pollinations": "pollinations/flux",
}

# Reference-image support, from the same catalogue. `refs` is the advertised
# input_references range; the number that matters for character consistency is
# the minimum, because a model that refuses a reference cannot be given one.
#   seedream 0-14 | gemini31 0-14 | flux_pro 0-8 | gemini_lite 0-14
#   gemini 0-3    | ming 0-0 (useless) | ming_layer 1-1 (needs a design file)
REFERENCE_CAPABLE = {"seedream", "gemini31", "flux_pro", "gemini_lite", "gemini"}

# Resolution is not uniform across the ladder - seedream offers 2K/4K, gemini31
# offers up to 4K, flux_pro and gemini advertise none - so it is sent only when
# the operator asks for it, and the payload walk below drops it automatically
# for any model that rejects it rather than failing the beat.
DEFAULT_IMAGE_RESOLUTION = os.environ.get("IMAGE_RESOLUTION", "2K")

# The 2K/4K reference-capable models lead because the whole point of moving off
# Pollinations is image quality. `gemini` is the only rung confirmed end to end
# by a real run, so it stays in the chain as a known-good fallback, and
# pollinations is last so a run can still finish at zero cost.
DEFAULT_IMAGE_ORDER = "seedream,gemini31,flux_pro,gemini_lite,gemini,pollinations"

# Ordered TTS ladder. Fish is first because it is the only rung that supports
# stateless voice cloning, which is what keeps one recognisable voice across the
# whole channel instead of a new stranger every episode.
TTS_LADDER = [
    {"key": "fish_openrouter", "model": "fish-audio/s2.1-pro-free:free", "clone": True},
    {"key": "deepgram_openrouter", "model": "deepgram/flux-tts:free", "clone": False},
]


class MediaGenerationError(RuntimeError):
    """Raised when no provider could produce a usable asset."""


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------
def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise MediaGenerationError("OPENROUTER_API_KEY is not set")
    return key


def _openrouter_post(path: str, payload: dict, *, raw: bool, attempts: int = 3) -> requests.Response:
    """POST to OpenRouter with exponential backoff on rate limits.

    Free-tier models are aggressively rate limited, and a bare retry loop that
    gives up on the first 429 is the usual reason a daily run ships nothing.
    """
    url = f"{OPENROUTER_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }
    last_error = None

    for attempt in range(attempts):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
        except requests.RequestException as exc:  # network hiccup / timeout
            last_error = exc
            time.sleep(2 * (attempt + 1))
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = MediaGenerationError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if (retry_after or "").isdigit() else 2 ** (attempt + 1) + random.random()
            time.sleep(min(wait, 30))
            continue

        if not resp.ok:
            # 400 usually means "this rung does not accept that parameter" -
            # surface it so the caller can drop the optional field and retry.
            raise MediaGenerationError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        if raw:
            if len(resp.content) < 2_000:
                raise MediaGenerationError("provider returned an empty audio stream")
            return resp
        return resp

    raise MediaGenerationError(f"OpenRouter {path} failed after {attempts} attempts: {last_error}")


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
def validate_image(path: str) -> tuple[bool, str]:
    """Reject anything that would look broken on screen.

    Catches the three failure modes that actually shipped: a flat placeholder
    colour card, a provider error stub, and a wrong-orientation render that
    would get pillarboxed into black bars.
    """
    if not os.path.exists(path):
        return False, "file does not exist"
    if os.path.getsize(path) < MIN_IMAGE_BYTES:
        return False, f"file too small ({os.path.getsize(path)} bytes)"

    if Image is None:
        return True, "ok (Pillow unavailable, byte check only)"

    try:
        with Image.open(path) as im:
            im.load()
            width, height = im.size
            if width < 384 or height < 384:
                return False, f"resolution too low ({width}x{height})"
            aspect = width / height
            if abs(aspect - TARGET_ASPECT) > ASPECT_TOLERANCE:
                return False, f"not 9:16 (got {width}x{height}, aspect {aspect:.3f})"
            luma_stddev = ImageStat.Stat(im.convert("L")).stddev[0]
            if luma_stddev < MIN_LUMA_STDDEV:
                return False, f"image is flat (luma stddev {luma_stddev:.1f} < {MIN_LUMA_STDDEV})"
    except Exception as exc:
        return False, f"unreadable image: {exc}"

    return True, "ok"


def _write_data_url_image(reference_path: str) -> str:
    with open(reference_path, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("ascii")
    ext = os.path.splitext(reference_path)[1].lstrip(".").lower() or "jpeg"
    if ext == "jpg":
        ext = "jpeg"
    return f"data:image/{ext};base64,{encoded}"


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------
def _accepts_references(model: str) -> bool:
    """True when this rung can actually be given a reference image."""
    return model in {IMAGE_LADDER[k] for k in REFERENCE_CAPABLE if k in IMAGE_LADDER}


def _generate_via_openrouter(prompt: str, seed: int, out_path: str, reference_path: str | None, model: str) -> None:
    """Render one beat through OpenRouter's Images API using `model`.

    The model is passed in by the caller, which has already run it through
    billing.filter_ladder. This function used to re-derive the choice itself
    with `allowed[0][1]`, which silently pinned every attempt to the first
    permitted rung no matter which rung the caller's loop was on - so with a
    multi-model order, rung 2 and beyond were unreachable. The first model to
    fail took the whole episode with it instead of falling through to the next.
    """
    base = {"model": model, "prompt": prompt}
    if DEFAULT_IMAGE_RESOLUTION:
        base = {**base, "resolution": DEFAULT_IMAGE_RESOLUTION}

    # Not every image model advertises aspect_ratio, resolution or n in
    # supported_parameters, and OpenRouter rejects an unsupported combination
    # with a 400 rather than quietly ignoring it. So we walk a ladder of
    # progressively plainer payloads and keep the first one the model actually
    # accepts, rather than assuming.
    #
    # The reference portrait is the first thing to go: it is the biggest quality
    # win, but a model that cannot take references should still render the beat.
    candidates = []
    # Only send a reference to a rung that advertises it. A model whose
    # input_references minimum is 0 (ming-image-0.1-design) will reject the
    # payload with a 400, so the walk below would recover - but only after a
    # wasted round trip on every beat of every episode.
    if reference_path and os.path.exists(reference_path) and _accepts_references(model):
        candidates.append({**base, "aspect_ratio": "9:16", "seed": seed, "n": 1,
                           "input_references": [
                               {"type": "image_url", "image_url": {"url": _write_data_url_image(reference_path)}}
                           ]})
    elif reference_path and os.path.exists(reference_path):
        print(f"i: {model} cannot take a reference image; rendering from text only")
    candidates.append({**base, "aspect_ratio": "9:16", "seed": seed, "n": 1})
    candidates.append({**base, "aspect_ratio": "9:16"})
    candidates.append(dict(base))

    last_error = None
    for payload in candidates:
        try:
            resp = _openrouter_post("/images", payload, raw=False, attempts=2)
        except MediaGenerationError as exc:
            if "HTTP 400" in str(exc):
                # Capability gap, not an outage. Try the next plainer payload.
                last_error = exc
                extra = sorted(set(payload) - set(base))
                print(f"i: {model} rejected a payload carrying {extra or ['defaults only']}; retrying plainer")
                continue
            raise
        _save_image_response(resp, out_path, model, reference_path)
        return

    raise MediaGenerationError(f"{model} rejected every payload shape: {last_error}")


def _save_image_response(resp: requests.Response, out_path: str, model: str, reference_path: str | None) -> None:
    try:
        body = resp.json()
    except ValueError as exc:
        raise MediaGenerationError(f"{model} returned non-JSON: {resp.text[:200]}") from exc

    entries = body.get("data") or []
    if not entries:
        raise MediaGenerationError(f"{model} returned no image data: {json.dumps(body)[:200]}")

    entry = entries[0]
    if entry.get("b64_json"):
        blob = base64.b64decode(entry["b64_json"])
    elif entry.get("url"):
        downloaded = requests.get(entry["url"], timeout=HTTP_TIMEOUT)
        if not downloaded.ok:
            raise MediaGenerationError(f"{model} image URL fetch failed: HTTP {downloaded.status_code}")
        blob = downloaded.content
    else:
        raise MediaGenerationError(f"{model} response had neither b64_json nor url")

    with open(out_path, "wb") as fh:
        fh.write(blob)

    ok, reason = validate_image(out_path)
    if not ok:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise MediaGenerationError(f"{model} produced an unusable image: {reason}")


def _generate_via_pollinations(prompt: str, seed: int, out_path: str) -> None:
    import urllib.parse

    url = (
        "https://image.pollinations.ai/prompt/"
        f"{urllib.parse.quote(prompt[:900])}?width=768&height=1344&seed={seed}&model=flux&nologo=true"
    )
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=HTTP_TIMEOUT)
    if not resp.ok:
        raise MediaGenerationError(f"pollinations HTTP {resp.status_code}")

    with open(out_path, "wb") as fh:
        fh.write(resp.content)

    ok, reason = validate_image(out_path)
    if not ok:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise MediaGenerationError(f"pollinations produced an unusable image: {reason}")


def generate_beat_image(
    prompt: str,
    seed: int,
    out_path: str,
    reference_path: str | None = None,
    log=print,
) -> str:
    """Render one 9:16 beat, or raise.

    There is deliberately no placeholder branch. A caller that gets a
    MediaGenerationError should abandon the reel rather than publish a black
    rectangle - a failed run is recoverable, a published broken reel is not.
    """
    order = [k.strip() for k in os.environ.get("IMAGE_PROVIDER_ORDER", DEFAULT_IMAGE_ORDER).split(",") if k.strip()]
    errors = []

    # A paid rung is dropped by billing.filter_ladder rather than attempted,
    # so the free entries after it still get their turn. This matters most for
    # "gemini": it is first in the preference order, so without the filter the
    # run would spend money before ever considering the free option.
    order = [k for k, _ in billing.filter_ladder(order, IMAGE_LADDER, "image")]
    if not order:
        raise MediaGenerationError(
            "no permitted image provider (billing policy refuses every rung; "
            "set OPENROUTER_FREE_ONLY=0 to allow paid models)"
        )

    for attempt in range(1, 3):  # one retry with a fresh seed: providers flake
        for key in order:
            if key == "pollinations":
                try:
                    _generate_via_pollinations(prompt, seed + attempt, out_path)
                    log(f"  [image] pollinations/flux ok (seed {seed + attempt})")
                    return out_path
                except MediaGenerationError as exc:
                    errors.append(f"pollinations: {exc}")
            elif key in IMAGE_LADDER:
                try:
                    _generate_via_openrouter(prompt, seed + attempt, out_path, reference_path, IMAGE_LADDER[key])
                    log(f"  [image] {IMAGE_LADDER[key]} ok (seed {seed + attempt}, ref={'yes' if reference_path else 'no'})")
                    return out_path
                except MediaGenerationError as exc:
                    errors.append(f"{IMAGE_LADDER[key]}: {exc}")
            else:
                errors.append(f"unknown provider key: {key}")

    raise MediaGenerationError(
        "every image provider failed for this beat:\n    " + "\n    ".join(errors)
    )


# --------------------------------------------------------------------------
# text to speech
# --------------------------------------------------------------------------
def _fish_emotion_tags(text: str) -> list[str]:
    import re

    return re.findall(r"\[([^\]]{1,24})\]", text)


def _strip_emotion_tags(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]{1,24}\]", "", text)).strip()


def synthesize_speech(
    text: str,
    out_path: str,
    voice: str,
    voice_reference_path: str | None = None,
    log=print,
) -> dict:
    """Speak `text` to `out_path` (mp3).

    Returns a dict describing how it was produced so the caption builder knows
    whether real word timings are available:

        {"provider": str, "has_word_boundaries": bool, "text": str}
    """
    order = [r["key"] for r in TTS_LADDER]
    if os.environ.get("TTS_PROVIDER"):
        wanted = os.environ["TTS_PROVIDER"].strip()
        order = [k for k in order if k == wanted] or [wanted]

    # Both current TTS rungs are ":free", so this changes nothing today. It is
    # here so that editing TTS_LADDER to add a paid voice fails closed rather
    # than quietly billing the channel every episode.
    permitted = [k for k, _ in billing.filter_ladder(
        order, {r["key"]: r["model"] for r in TTS_LADDER}, "tts"
    )]
    for key in order:
        if key not in permitted:
            log(f"  [tts] skipping {key} - refused by the free-only billing policy")
    order = permitted
    if not order:
        raise MediaGenerationError("every TTS rung is refused by the billing policy")

    errors = []
    for rung in TTS_LADDER:
        if rung["key"] not in order:
            continue
        try:
            # Fish understands [sigh]/[laughing] inline and they are part of the
            # performance. Deepgram does not - it would read them out loud, so
            # they get stripped before it ever sees the text.
            spoken = text if rung["key"] == "fish_openrouter" else _strip_emotion_tags(text)

            payload = {
                "model": rung["model"],
                "input": spoken,
                "response_format": "mp3",
            }
            if voice:
                payload["voice"] = voice

            if rung["clone"] and voice_reference_path and os.path.exists(voice_reference_path):
                with open(voice_reference_path, "rb") as fh:
                    encoded = base64.b64encode(fh.read()).decode("ascii")
                payload["input_references"] = [
                    {"type": "input_audio", "input_audio": {"data": f"data:audio/mpeg;base64,{encoded}"}},
                    {"type": "text", "text": VOICE_REFERENCE_TRANSCRIPT},
                ]

            try:
                resp = _openrouter_post("/audio/speech", payload, raw=True)
            except MediaGenerationError as exc:
                # "voice" is provider-specific: an unknown voice id is a 400, and
                # retrying without it lets a provider-side default take over.
                if voice and "HTTP 400" in str(exc):
                    payload.pop("voice", None)
                    resp = _openrouter_post("/audio/speech", payload, raw=True)
                    voice = ""
                else:
                    raise

            with open(out_path, "wb") as fh:
                fh.write(resp.content)

            log(f"  [tts] {rung['model']} ok ({len(resp.content) // 1024} KB, clone={'yes' if payload.get('input_references') else 'no'})")
            return {
                "provider": rung["model"],
                "has_word_boundaries": False,
                "text": spoken,
                "emotion_tags": _fish_emotion_tags(text) if rung["key"] == "fish_openrouter" else [],
            }
        except MediaGenerationError as exc:
            errors.append(f"{rung['model']}: {exc}")

    raise MediaGenerationError("every TTS provider failed:\n    " + "\n    ".join(errors))
