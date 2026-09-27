import os
import re
import json
import time
import shutil
import subprocess
import urllib.parse
from datetime import datetime, timezone
import requests
import feedparser

try:
    from openai import OpenAI
    HAS_OPENAI = True
except Exception:
    HAS_OPENAI = False

from src.openrouter_media import (
    MediaGenerationError,
    generate_beat_image,
    validate_image,
)
from src.motion import MOTION_THRESHOLD, MotionError, generate_i2v_clip, measure_motion
from src.pet_music import MOODS as MUSIC_MOODS, mood_for_action, write_music_wav
import src.billing as billing

STATE_DIR = "state"
DOCS_DIR = "docs"
MEDIA_DIR = f"{DOCS_DIR}/media"
STATE_BIBLE = f"{STATE_DIR}/character_bible.json"
STATE_CATALOG = f"{STATE_DIR}/catalog.json"
STATE_WEIGHTS = f"{STATE_DIR}/strategy_weights.json"
STATE_QUEUE = f"{STATE_DIR}/queue.json"
TICKS_PER_SECOND = 10_000_000

DEFAULT_BIBLE = {
    "rabbit_channel": {
        "enabled": True,
        "channel_name": "The Buns",
        "handle": "@TheBunsDaily",
        "bio_slug": "barnaby",
        "master_seed": 884102,
        "cast_policy": "ANIMALS_ONLY",
        "cast_policy_note": "No human, no face, no hands, no owner appears in any image or video. Maya is the (unseen) owner and is never rendered. Every beat is rabbits only.",
        "music_policy": "Light-hearted instrumental background music generated per episode. No voiceover, no narration, no on-screen speech captions.",
        "locations": {
            "SET_RUG_POV": "high-angle first-person iPhone POV looking down at a braided cream jute rug over herringbone oak flooring, black wire playpen panel in background, warm window daylight",
            "SET_KITCHEN": "oak butcher-block kitchen island, matte cream shaker cabinets and white subway tile splashback, soft morning daylight, no people",
            "SET_LIVING": "bright Scandi cottage living room, oatmeal boucle sofa, matte sage-green panelled wall behind, potted monstera plant, warm window daylight, no people"
        },
        "pets": [
            {
                "id": "barnaby",
                "name": "Barnaby",
                "breed": "Holland Lop Rabbit",
                "species": "rabbit",
                "birth_date": "2026-05-10",
                "pet_seed": 420881,
                "role": "The polite food critic",
                "immutable_marking_dna": "one single cream-white Holland Lop rabbit with long floppy lop ears, where ONLY the left ear is dark charcoal-grey and the right ear is cream white, pink nose, dark round eyes, dense soft plush fur",
                "grounder": "exactly one rabbit, full body and face clearly visible, short upright fluffy coat, same charcoal-grey left ear in every shot"
            },
            {
                "id": "pip",
                "name": "Pip",
                "breed": "Lionhead Rabbit",
                "species": "rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420882,
                "role": "The cable-chewing chaos gremlin",
                "immutable_marking_dna": "one single jet-black Lionhead rabbit with short upright black ears, a distinct snow-white fluffy mane tuft right between his ears, and a black nose, dark round eyes",
                "grounder": "exactly one rabbit, full body and face clearly visible, the white mane tuft between the ears visible in every shot"
            },
            {
                "id": "clover",
                "name": "Clover",
                "breed": "Dutch Rabbit",
                "species": "rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420883,
                "role": "The zoomie queen",
                "immutable_marking_dna": "one single cinnamon-amber and white Dutch rabbit with a crisp white shoulder saddle, a white nose blaze running up the face, white front paws, and dark round eyes",
                "grounder": "exactly one rabbit, full body and face clearly visible, the white shoulder saddle and nose blaze visible in every shot"
            }
        ]
    }
}

# The channel is animals-only. This is a hard requirement, not a preference:
# issue #8 shipped a human/rabbit hybrid creature because a single word in a
# prompt was enough to summon a person back into the frame. Rather than trust
# every future prompt to be written carefully, every prompt that reaches an
# image or video model is checked against this list first, and a hit aborts the
# reel. Failing the whole episode is the correct outcome - a reel that quietly
# grew a person in it is exactly the failure we are preventing.
NO_HUMAN_TERMS = (
    "human", "humans", "person", "persons", "people", "woman", "women",
    "man", "men", "girl", "girls", "boy", "boys", "lady", "ladies",
    "gentleman", "owner", "owners", "maya", "face dna", "portrait of",
    "hand", "hands", "arm", "arms", "finger", "fingers", "child", "children",
    "crowd", "family", "kid", "kids", "selfie", "vlogger", "creator",
)

# Terms match only on word boundaries, and specifically NOT after a hyphen or
# another word character. A plain substring search is useless here, and so is a
# loose suffix:
#   "first-person iPhone POV" contains "person"   -> blocked by the lookbehind
#   "warm window daylight"    contains "arm"      -> blocked by the lookbehind
#   "snow-white fluffy mane"  contains "man"      -> blocked by the trailing \b
# All three appear in legitimate animal-only prompts, and Pip is a Lionhead
# whose defining feature is a "mane tuft", so a guard that fires on those
# rejects every episode. The guard has to be sharp enough that it never cries
# wolf, because a guard that cries wolf gets switched off.
_NO_HUMAN_RE = re.compile(
    r"(?<![-\w])(" + "|".join(re.escape(t) for t in NO_HUMAN_TERMS) + r")\b"
)


class CastPolicyError(RuntimeError):
    """A prompt or render asked for a human in an animals-only channel."""


def assert_animal_only(text: str, where: str) -> str:
    """Reject any prompt naming a person, so a reel never grows a face."""
    low = (text or "").lower()
    hits = []
    for m in _NO_HUMAN_RE.finditer(low):
        # "no people" / "zero human hands" are negations - they are the whole
        # point of putting them in the prompt, so they are not a hit.
        prefix = low[max(0, m.start() - 5):m.start()].strip()
        if prefix.endswith(("no", "zero", "without", "nobody", "not", "never")):
            continue
        hits.append(m.group(0))
    if hits:
        raise CastPolicyError(
            f"{where} prompt names a human ({', '.join(sorted(set(hits)))}). "
            f"This channel is animals-only; the reel was not rendered."
        )
    return text


DEFAULT_CATALOG = {
    "rabbit_channel": [
        {
            "id": "cord-protector-tubing",
            "title": "Heavy-Duty Split Wire Loom Cord Protectors (10ft)",
            "price": "£8.99",
            "badge": "Pip's Life Saver",
            "affiliate_url": "https://www.amazon.co.uk/dp/B00EXAMPLE?tag=yourtag-21",
            "keywords": ["cord", "cable", "chew"]
        },
        {
            "id": "hay-feeder-pro",
            "title": "Anti-Mess Wooden Hay Rack & Feeding Station",
            "price": "£24.99",
            "badge": "Barnaby's Pick",
            "affiliate_url": "https://www.amazon.co.uk/dp/B00EXAMPLE3?tag=yourtag-21",
            "keywords": ["hay", "feeder", "litter", "rack", "mess"]
        }
    ]
}

DEFAULT_WEIGHTS = {"rabbit_channel": {"maturity_phase": "PHASE_1_INCUBATION"}}
DEFAULT_QUEUE = {"pending_approvals": [], "published_history": [], "used_trends": []}

def strip_non_ascii(text: str) -> str:
    """Removes emojis and non-ASCII glyphs so libass/DejaVu Sans never draws broken [□] boxes."""
    cleaned = text.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[{}\\]", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()

def ensure_json_file(path: str, default_data: dict, required_subkey: str = None) -> dict:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = None
    if os.path.exists(path) and os.path.getsize(path) > 5:
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                if required_subkey:
                    first_val = next(iter(loaded.values()), {})
                    if isinstance(first_val, dict) and required_subkey in first_val:
                        data = loaded
                else:
                    data = loaded
        except Exception:
            data = None
    if data is None:
        data = default_data
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return data

def calculate_age_stats(birth_date_str: str) -> dict:
    try:
        birth_dt = datetime.strptime(birth_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_days = max(1, (datetime.now(timezone.utc) - birth_dt).days)
    except Exception:
        age_days = 75
    if age_days < 90:
        stage, morph = "baby_kit", "tiny palm-sized 10-week-old baby kit with oversized ears and fluffy baby fur"
    elif age_days < 180:
        stage, morph = "adolescent", "juvenile half-grown 4-month-old rabbit with lanky body proportions"
    else:
        stage, morph = "prime_adult", "full-grown adult rabbit with dense glossy coat"
    return {"age_days": age_days, "age_weeks": round(age_days / 7.0, 1), "stage": stage, "morphology": morph}

def fetch_safe_trends() -> list[str]:
    try:
        resp = requests.get("https://news.google.com/rss/search?q=house+rabbit+OR+pet+bunny+tips", timeout=10)
        feed = feedparser.parse(resp.text)
        titles = [e.title for e in feed.entries[:5] if e.title]
        if titles:
            return titles
    except Exception:
        pass
    return [
        "Why rabbits flip cheap plastic bowls and how to stop it",
        "How to bunny-proof laptop and phone cords in 5 minutes"
    ]

FREE_LLM_WATERFALL = [
    "openrouter/free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
]

def write_daily_episode_plan(channel_meta: dict, chosen_pets: list[dict], age_summary: str, trend: str) -> dict:
    """Plan one silent, animals-only episode.

    There is no voiceover and no spoken script any more. What an LLM contributes
    is the plan the visuals and the music follow: which pet is on screen, what
    it is doing, and a short hook for the on-screen text. The words are kept to a
    caption, never spoken, because a narration track is what the channel used to
    build its identity around and the whole point of the change is that the
    animals are the content.

    Every action is checked against the animals-only cast policy before it is
    returned, so a model that volunteers a person loses the whole reel rather
    than quietly reintroducing one.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    by_id = {p["id"]: p for p in chosen_pets}
    p1 = chosen_pets[0]
    p2 = chosen_pets[1] if len(chosen_pets) > 1 else chosen_pets[0]

    if HAS_OPENAI and api_key:
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        roster = "\n".join(
            f"- {p['name']} (id={p['id']}): {p['breed']}. Locked look: {p['immutable_marking_dna']}"
            for p in chosen_pets
        )
        prompt = (
            "Plan one 9-second vertical silent video for a pet-only Instagram/TikTok "
            "account. There is NO voiceover, NO narration and NO spoken audio - only "
            f"upbeat background music and on-screen text.\n\n"
            f"Cast (do not invent animals outside this list):\n{roster}\n\n"
            f"Current ages:\n{age_summary}\n\nLoose inspiration (do not mention on screen): {trend}\n\n"
            "Rules:\n"
            "1. Return exactly 3 beats. Each beat names one pet_id from the cast list above.\n"
            "2. Two beats may use the SAME pet_id (the same animal appearing more than "
            "once in one video is fine and encouraged).\n"
            "3. Each action is a short physical behaviour of that animal only: chewing, "
            "nuzzling, loafing, zoomies, digging, grooming, flopping over. Never a human "
            "action and never a human in shot.\n"
            "4. hook_text is 2-4 ASCII WORDS, no emoji, that would work as a text overlay.\n"
            "5. music_mood for each beat is one of: playful, curious, calm, sleepy.\n"
            "Return ONLY valid JSON, no prose:\n"
            '{"hook_text": "BUNNY BUDGET CHECK", "beats": ['
            '{"pet_id": "barnaby", "action": "nudging a full hay rack with his nose", '
            '"motion": "slowly chewing, ears bobbing gently", "music_mood": "calm"}, '
            '{"pet_id": "pip", "action": "full zoomies across the jute rug", '
            '"motion": "bounding and skidding, ears flying", "music_mood": "playful"}, '
            '{"pet_id": "pip", "action": "flopping over mid-run for a dramatic nap", '
            '"motion": "slowly sinking into a flat loaf, eyes closing", "music_mood": "sleepy"}]}'
        )
        for m in FREE_LLM_WATERFALL:
            for attempt in range(2):
                try:
                    r = client.chat.completions.create(model=m, messages=[{"role": "user", "content": prompt}], timeout=30)
                    raw = r.choices[0].message.content or ""
                    match = re.search(r"\{.*\}", raw, re.DOTALL)
                    if not match:
                        break
                    data = json.loads(match.group(0))
                    beats = data.get("beats") or []
                    if len(beats) != 3 or not data.get("hook_text"):
                        break
                    # Every beat must name a real cast member, or we cannot
                    # ground the animal at all.
                    if any(b.get("pet_id") not in by_id for b in beats):
                        print(f"Model {m} returned a pet_id outside the cast - discarding")
                        break
                    for b in beats:
                        assert_animal_only(f"{b.get('action','')} {b.get('motion','')}", f"{m} beat")
                    return data
                except CastPolicyError:
                    raise
                except Exception as e:
                    # Free models are rate limited hard and 429 is routine, not
                    # fatal - back off and give the next model a turn.
                    text = str(e)
                    if "429" in text and attempt == 0:
                        time.sleep(4)
                        continue
                    print(f"Model {m} skipped: {text[:120]}")
                    break

    return {
        "hook_text": "BUNNY BUDGET CHECK",
        "beats": [
            {"pet_id": p1["id"], "action": "nudging a full hay rack with his nose",
             "motion": "slowly chewing, ears bobbing gently", "music_mood": "calm"},
            {"pet_id": p2["id"], "action": "full zoomies across the jute rug",
             "motion": "bounding and skidding, ears flying", "music_mood": "playful"},
            {"pet_id": p2["id"], "action": "flopping over mid-run for a dramatic nap",
             "motion": "slowly sinking into a flat loaf, eyes closing", "music_mood": "sleepy"},
        ],
    }

# =====================================================================
# 1. SILENT REELS: on-screen text only. There is no voiceover and no narration,
#    so there is no voice model, no cloning reference and no speech captions.
# =====================================================================
def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

# =====================================================================
# 2. ANIMAL-ONLY 9:16 STILLS & LTX-VIDEO ANIMATOR
#    The cast is entirely animals, so there is no face lock to keep. What holds
#    a character steady instead is the pet's own marking DNA plus its fixed seed.
# =====================================================================
def fetch_flux_image(prompt: str, seed: int, out_path: str):
    """Render one beat through the validated provider ladder. Raises on failure."""
    return generate_beat_image(prompt, seed, out_path)


def animate_beat_to_mp4(img_path: str, motion_prompt: str, duration_sec: float, out_mp4: str, pan_dir: int = 1) -> str:
    """
    Turn a beat still into a genuinely moving clip.

    Returns the provider that actually animated it, or "still-pan" when every
    image-to-video provider failed. The caller is expected to surface that, so
    a run where the characters stayed dead is never presented as a success.
    """
    raw_ai = f"{out_mp4}.raw_ai.mp4"
    prompt = f"{motion_prompt}, subtle natural movement, handheld smartphone video, photorealistic"

    try:
        clip, provider = generate_i2v_clip(img_path, prompt, raw_ai, duration_sec)
    except MotionError as exc:
        print(f"DEGRADED {os.path.basename(img_path)}: no live motion, using camera drift")
        print(f"  {str(exc)[:400]}")
        _camera_drift(img_path, duration_sec, out_mp4, pan_dir)
        return "still-pan"

    # Generators emit a few seconds; cover the whole beat with a forward+reverse
    # loop so the beat never visibly restarts mid-shot.
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", clip,
        "-filter_complex",
        f"[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=30,split[fwd][tmp];"
        f"[tmp]reverse[rev];[fwd][rev]concat=n=2:v=1:a=0,trim=duration={duration_sec},setpts=PTS-STARTPTS[vout]",
        "-map", "[vout]", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", out_mp4
    ], check=True)
    return provider


def _camera_drift(img_path: str, duration_sec: float, out_mp4: str, pan_dir: int):
    """Last-resort motion: pan across the still.

    This is NOT a character coming alive - measured motion is ~1.8 against a
    4.0 threshold for real animation. It exists so a provider outage degrades
    the reel instead of failing it, and it is always reported as degraded.
    """
    if pan_dir == 1:
        x_expr = "(iw-ow)/2 + ((iw-ow)/3)*sin(t*0.45)"
        y_expr = "(ih-oh)*0.25 + ((ih-oh)*0.35)*(t/" + str(max(1.0, duration_sec)) + ")"
    else:
        x_expr = "(iw-ow)/2 - ((iw-ow)/3)*sin(t*0.45)"
        y_expr = "(ih-oh)*0.65 - ((ih-oh)*0.35)*(t/" + str(max(1.0, duration_sec)) + ")"

    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", str(duration_sec), "-r", "30", "-i", img_path,
        "-vf", (
            f"scale=1584:2816:force_original_aspect_ratio=increase,crop=1584:2816,"
            f"crop=w=1360:h=2418:x='{x_expr}':y='{y_expr}',"
            f"scale=720:1280:flags=lanczos,setsar=1,fps=30"
        ),
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", out_mp4
    ], check=True)

# =====================================================================
# 3. MASTER VIDEO COMPILER (POV + Reaction Cutaway Pacing)
# =====================================================================
def render_video(c_key: str, c_data: dict, chosen_pets: list[dict], age_data: dict, plan: dict) -> dict:
    """Render one silent, animals-only episode.

    Structure is unchanged from the voiced version - three beats, crossfaded -
    but every beat is now an animal, and the soundtrack is generated music rather
    than a voiceover. Because there is no narration to fit, the beat lengths are
    fixed rather than derived from an audio duration, which also stops one slow
    beat from swallowing the whole reel.
    """
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    b1_img = f"{MEDIA_DIR}/{c_key}_beat1.jpg"
    b2_img = f"{MEDIA_DIR}/{c_key}_beat2.jpg"
    b3_img = f"{MEDIA_DIR}/{c_key}_beat3.jpg"
    final_mp4 = f"{MEDIA_DIR}/{c_key}_latest.mp4"

    locs = c_data.get("locations", {})
    by_id = {p["id"]: p for p in chosen_pets}
    beats = plan["beats"]
    loc_keys = ["SET_RUG_POV", "SET_KITCHEN", "SET_LIVING"]
    beat_imgs = [b1_img, b2_img, b3_img]

    # 1. One animal-only still per beat, each anchored to a locked pet so the
    #    same rabbit comes back in the next episode looking like itself.
    for i, beat in enumerate(beats):
        pet = by_id[beat["pet_id"]]
        morph = age_data[pet["id"]]["morphology"]
        loc = locs.get(loc_keys[i % len(loc_keys)], "")
        prompt = (
            f"{loc}, candid vertical iPhone photo of {pet['immutable_marking_dna']} "
            f"({morph}), {beat.get('action', 'sitting calmly on the floor')}, "
            f"{pet.get('grounder', 'exactly one rabbit')}, "
            f"photorealistic, natural daylight, animals only, nobody else in the room"
        )
        assert_animal_only(prompt, f"beat{i + 1} image")
        seed = pet.get("pet_seed", 420000) + i * 7
        fetch_flux_image(prompt, seed, beat_imgs[i])
        print(f"  [cast] beat{i + 1}: {pet['name']} ({pet['breed']}) seed {seed}")

    # Final gate before anything reaches ffmpeg. Beat 3 of the run in issue #6
    # was a flat colour card here, and it became 4.6s of black screen in the
    # published MP4. Nothing unvalidated gets as far as the renderer.
    for label, path in (("beat1", b1_img), ("beat2", b2_img), ("beat3", b3_img)):
        ok, reason = validate_image(path)
        if not ok:
            raise MediaGenerationError(f"{label} failed validation ({reason}); aborting reel")
        print(f"  [gate] {label} ok - {os.path.getsize(path) // 1024} KB")

    # 2. Animate each beat. The motion prompt describes the animal only.
    d1 = d2 = d3 = 3.0
    segs = [f"output/{c_key}_seg{n}.mp4" for n in (1, 2, 3)]
    animators = {}
    for i, beat in enumerate(beats):
        pet = by_id[beat["pet_id"]]
        motion_prompt = f"{pet['breed']} {beat.get('motion', 'moving naturally')}"
        assert_animal_only(motion_prompt, f"beat{i + 1} motion")
        animators[f"beat{i+1}"] = animate_beat_to_mp4(
            beat_imgs[i], motion_prompt, [d1, d2, d3][i], segs[i], pan_dir=1 if i % 2 == 0 else -1
        )
    live_beats = [b for b, p in animators.items() if p != "still-pan"]
    if not live_beats:
        raise MediaGenerationError(
            "no beat could be animated - every image-to-video provider failed; "
            "refusing to publish a motionless reel"
        )
    print(f"  [motion] live beats: {', '.join(live_beats)}")

    # 3. Soundtrack. One continuous track whose mood is set by the reel's most
    #    energetic beat, faded in and out so it never starts or stops abruptly.
    total_duration = round(d1 + d2 + d3 - 0.4, 2)
    moods = [b.get("music_mood") for b in beats if b.get("music_mood")]
    # One continuous track, so pick the mood that suits the reel as a whole
    # rather than cutting between them. A reel built around zoomies and
    # bouncing wants upbeat music; a reel of rabbits asleep in a box wants a
    # lullaby. Ranking by energy, not first-wins, so the result is stable.
    energy = {"calm": 0, "curious": 1, "playful": 2, "sleepy": -1}
    lead_mood = max(moods, key=lambda m: energy.get(m, 1)) if moods else "curious"
    music_wav = f"output/{c_key}_music.wav"
    # Vary by day so consecutive episodes are not the same track note for note,
    # while a rerun on the same day stays byte-identical.
    seed = c_data.get("master_seed", 884102) + int(time.time()) // 86400
    write_music_wav(lead_mood, total_duration, seed, music_wav)
    print(f"  [music] mood '{lead_mood}' -> {music_wav} ({total_duration}s)")

    # 4. Stitch with crossfades, burn the hook text, and lay the music under it.
    hook = strip_non_ascii(plan.get("hook_text", ""))
    ass_path = write_hook_overlay(hook, total_duration)

    xf1 = round(d1 - 0.20, 2)
    xf2 = round(d1 + d2 - 0.40, 2)

    cmd = [
        "ffmpeg", "-y",
        "-i", segs[0], "-i", segs[1], "-i", segs[2], "-i", music_wav,
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=fade:duration=0.20:offset={xf1}[vx1];"
        f"[vx1][2:v]xfade=transition=fade:duration=0.20:offset={xf2},ass={ass_path}[vout];"
        f"[3:a]afade=t=in:st=0:d=0.6,afade=t=out:st={max(0.0, total_duration - 1.0):.2f}:d=1.0,"
        f"loudnorm=I=-14:TP=-1.5:LRA=11[aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-metadata", "com.apple.quicktime.make=Apple",
        "-metadata", "com.apple.quicktime.model=iPhone 15 Pro",
        "-shortest", final_mp4
    ]
    subprocess.run(cmd, check=True)

    final_motion = measure_motion(final_mp4)
    print(f"  [motion] final reel motion score: {final_motion:.2f} (live >= {MOTION_THRESHOLD})")

    return {
        "mp4": final_mp4,
        "b1": b1_img,
        "b2": b2_img,
        "b3": b3_img,
        "animators": animators,
        "live_beats": live_beats,
        "motion_score": final_motion,
        "hook_text": hook,
        "music_mood": lead_mood,
        "cast": [
            {"id": b["pet_id"], "name": by_id[b["pet_id"]]["name"],
             "breed": by_id[b["pet_id"]]["breed"], "action": b.get("action", "")}
            for b in beats
        ],
    }


def write_hook_overlay(hook: str, duration_sec: float) -> str:
    """A single short on-screen caption for the whole reel.

    Speech captions are gone - there is no speech. What remains is the one line
    of text these accounts put over the footage, which is how the viewer knows
    what they are looking at before the scroll takes them away.
    """
    path = "output/hook.ass"
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 720\nPlayResY: 1280\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,DejaVu Sans,54,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,"
        "0,0,0,0,100,100,0,0,1,4,2,2,60,60,230,1\n\n"
    )
    body = (
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
        f"Dialogue: 0,{format_ass_time(0.4)},{format_ass_time(max(0.6, duration_sec - 0.4))},"
        f"Default,,0,0,0,,{hook}\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + body)
    return path

# =====================================================================
# 4. STOREFRONT, GROUNDING DASHBOARD, & MAIN RUNNER
# =====================================================================
def build_all_storefronts_and_grounding(latest_draft: dict = None):
    bible = ensure_json_file(STATE_BIBLE, DEFAULT_BIBLE, required_subkey="bio_slug")
    catalog = ensure_json_file(STATE_CATALOG, DEFAULT_CATALOG)
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DOCS_DIR, "grounding"), exist_ok=True)
    os.makedirs(os.path.join(DOCS_DIR, "watch"), exist_ok=True)

    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w") as f:
        f.write("")

    cache_bust = int(time.time())
    hook_title = latest_draft["hook_text"] if latest_draft else "The Buns - Latest Reel"
    mood = latest_draft.get("music_mood", "") if latest_draft else ""
    cast_txt = " · ".join(
        f"{c['name']} ({c['breed'].replace(' Rabbit','')})" for c in (latest_draft.get("cast") or [])
    ) if latest_draft else ""
    watch_html = f"""<!DOCTYPE html>
    <html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Watch Daily Draft</title></head>
    <body style='background:#0f172a;color:#f8fafc;font-family:sans-serif;padding:20px;max-width:480px;margin:auto;text-align:center;'>
      <h2 style='color:#f59e0b;margin-bottom:8px;'>🐾 {hook_title}</h2>
      <video controls autoplay playsinline style='width:100%;max-width:360px;border-radius:16px;border:2px solid #334155;background:#000;' src='../media/rabbit_channel_latest.mp4?v={cache_bust}'></video>
      <p style='background:#1e293b;padding:14px;border-radius:10px;font-size:14px;line-height:1.5;margin-top:16px;'>
        <strong>Animals only.</strong> No owner on camera, no voiceover.<br>
        <span style='color:#34d399;font-size:12px;'>🎵 {mood} · {cast_txt}</span>
      </p>
      <p style='margin-top:16px;'><a href='../media/rabbit_channel_latest.mp4?v={cache_bust}' download style='color:#38bdf8;font-weight:700;'>📥 Download Raw MP4</a> | <a href='../grounding/' style='color:#f59e0b;'>🛡️ Grounding Bible</a></p>
    </body></html>"""
    with open(os.path.join(DOCS_DIR, "watch", "index.html"), "w", encoding="utf-8") as f:
        f.write(watch_html)

    grounding_cards, hub_links = [], []
    for c_key, c_data in bible.items():
        slug = c_data.get("bio_slug", c_key.split("_")[0])
        c_name = c_data.get("channel_name", c_key)
        c_handle = c_data.get("handle", f"@{slug}")
        slug_dir = os.path.join(DOCS_DIR, slug)
        os.makedirs(slug_dir, exist_ok=True)

        pets_html = "".join([
            f"<div style='background:#0f172a;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #334155;'>"
            f"<strong>{p.get('name','Pet')}</strong> ({p.get('breed','Rabbit')}) — Born: {p.get('birth_date','2026-06-01')}"
            f" · <span style='color:#94a3b8;font-size:11px;'>seed {p.get('pet_seed','-')}</span><br>"
            f"<small>{p.get('immutable_marking_dna', '')}</small></div>"
            for p in c_data.get("pets", [])
        ])
        locs_html = "".join([f"<li><strong>{k}:</strong> {v}</li>" for k, v in c_data.get("locations", {}).items()])
        grounding_cards.append(
            f"<div style='background:#1e293b;padding:20px;border-radius:12px;margin-bottom:20px;'>"
            f"<h2>{c_name} ({c_handle})</h2>"
            f"<p style='color:#34d399;font-size:13px;'>🐾 ANIMALS-ONLY CAST — no human, no owner, no voiceover. "
            f"Music: {c_data.get('music_policy','')}</p>"
            f"<div style='display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;'>"
            f"<div><small>Beat 1</small><br><img src='../media/{c_key}_beat1.jpg?v={cache_bust}' style='width:135px;border-radius:10px;border:1px solid #475569;'></div>"
            f"<div><small>Beat 2</small><br><img src='../media/{c_key}_beat2.jpg?v={cache_bust}' style='width:135px;border-radius:10px;border:1px solid #475569;'></div>"
            f"<div><small>Beat 3</small><br><img src='../media/{c_key}_beat3.jpg?v={cache_bust}' style='width:135px;border-radius:10px;border:1px solid #475569;'></div>"
            f"</div>"
            f"<h3>Grounded Animals</h3>{pets_html}<h3>Locked Locations</h3><ul>{locs_html}</ul></div>"
        )

        prod_html = "".join([
            f"<div style='background:#1e293b;padding:16px;border-radius:12px;margin-bottom:12px;border:1px solid #334155;'>"
            f"<span style='background:#f59e0b;color:#000;font-size:10px;font-weight:800;padding:2px 6px;border-radius:4px;'>{p.get('badge','Verified Pick')}</span>"
            f"<h3 style='margin:8px 0 4px;font-size:16px;'>{p.get('title','Pet Essential')}</h3>"
            f"<p style='color:#34d399;font-weight:700;margin:4px 0;'>{p.get('price','£12.99')}</p>"
            f"<a href='{p.get('affiliate_url','#')}' target='_blank' style='display:inline-block;margin-top:6px;background:#3b82f6;color:#fff;text-decoration:none;padding:6px 12px;border-radius:6px;font-size:12px;font-weight:600;'>View Deal →</a></div>"
            for p in catalog.get(c_key, [])
        ])
        with open(os.path.join(slug_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(
                f"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{c_name}</title></head>"
                f"<body style='background:#0f172a;color:#f8fafc;font-family:sans-serif;padding:24px;max-width:440px;margin:auto;'>"
                f"<h1 style='text-align:center;'>{c_name}</h1>{prod_html}</body></html>"
            )
        hub_links.append(f"<li><a href='{slug}/' style='color:#38bdf8;font-size:18px;'>{c_name} Storefront →</a></li>")

    with open(os.path.join(DOCS_DIR, "grounding", "index.html"), "w", encoding="utf-8") as f:
        f.write(
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Universe Grounding Bible</title></head>"
            f"<body style='background:#0f172a;color:#f8fafc;font-family:sans-serif;padding:24px;max-width:860px;margin:auto;'>"
            f"<h1 style='color:#f59e0b;'>🐾 Universe Grounding & Cast Bible</h1>"
            f"<p><a href='../watch/' style='color:#34d399;font-weight:700;'>▶️ Watch Latest Daily Video</a> | <a href='../' style='color:#38bdf8;'>← Agency Hub</a></p>"
            f"{''.join(grounding_cards)}</body></html>"
        )

    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>AI Pet Influencer Hub</title></head>"
            f"<body style='background:#0f172a;color:#f8fafc;font-family:sans-serif;padding:32px;max-width:600px;margin:auto;line-height:1.6;'>"
            f"<h1>🐾 Pet Influencer Collective</h1>"
            f"<p><a href='watch/' style='color:#34d399;font-weight:700;font-size:18px;'>▶️ Watch Latest Generated Reel →</a></p>"
            f"<h3>Public Storefronts</h3><ul>{''.join(hub_links)}</ul>"
            f"<hr style='border-color:#334155;margin:24px 0;'><h3>Owner Operations</h3>"
            f"<p><a href='grounding/' style='color:#f59e0b;font-weight:700;font-size:18px;'>🛡️ Open Universe Grounding Bible →</a></p></body></html>"
        )

def announce_billing_policy():
    """State the spend policy once, before anything can cost anything.

    Silence here is how a $0 run quietly becomes a paid one, so the banner
    names what is allowed and, crucially, what has been switched off.
    """
    if not billing.free_only():
        print("[billing] WARNING: OPENROUTER_FREE_ONLY=0 - PAID OpenRouter models are permitted.")
        print("[billing] Paid rungs are active and WILL spend credit.")
        return

    print("[billing] FREE-ONLY: no paid OpenRouter model may be called.")
    import src.frame_diffusion as fd
    import src.openrouter_media as om

    paid_images = [m for m in om.IMAGE_LADDER.values() if not billing.is_free(m)]
    paid_frames = [m for m in fd.FRAME_LADDER.values() if not billing.is_free(m)]
    print(f"[billing]   image rungs refused : {', '.join(paid_images) or 'none'}")
    if paid_frames:
        print(f"[billing]   FRAME DIFFUSION OFF : {', '.join(paid_frames)}")
        print("[billing]   -> beats fall back to the still-camera-pan unless a free")
        print("[billing]      image-to-video rung (HF Spaces) succeeds. Expect a")
        print("[billing]      motionless reel and a 'DEGRADED' notice; this is the")
        print("[billing]      cost of running at $0, not a bug.")
    free_tts = [r["model"] for r in om.TTS_LADDER if billing.is_free(r["model"])]
    print(f"[billing]   TTS (already free)  : {', '.join(free_tts) or 'none'} (unused - reels are silent)")


def main():
    bible = ensure_json_file(STATE_BIBLE, DEFAULT_BIBLE, required_subkey="bio_slug")
    queue = ensure_json_file(STATE_QUEUE, DEFAULT_QUEUE)
    repo = os.environ.get("REPO_NAME", "ATAD4NRY4N/pet-influencer-agency")
    owner, repo_short = repo.split("/") if "/" in repo else ("ATAD4NRY4N", "pet-influencer-agency")

    if os.environ.get("EVENT_NAME", "") in ("issues", "issue_comment"):
        return

    announce_billing_policy()

    latest_draft = None
    failed_channels = []
    for c_key, c_data in bible.items():
        if not c_data.get("enabled", True):
            continue
        pets = c_data.get("pets", DEFAULT_BIBLE["rabbit_channel"]["pets"])
        age_data = {p["id"]: calculate_age_stats(p.get("birth_date", "2026-06-01")) for p in pets}
        age_summary = "\n".join([f"- {p['name']}: {age_data[p['id']]['age_weeks']} wks ({age_data[p['id']]['stage']})" for p in pets])
        trends = fetch_safe_trends()

        script_data = write_daily_episode_plan(c_data, pets, age_summary, trends[0])
        try:
            render_result = render_video(c_key, c_data, pets, age_data, script_data)
        except MediaGenerationError as e:
            # Do not queue a draft, do not open an issue, and above all do not
            # leave a half-rendered MP4 sitting in docs/media where the watch
            # page and the previous good reel would both pick it up.
            print(f"ABORT {c_key}: {e}")
            failed_channels.append({"channel": c_key, "error": str(e)})
            continue

        pages_watch_url = f"https://{owner.lower()}.github.io/{repo_short}/watch/"
        raw_mp4_url = f"https://raw.githubusercontent.com/{repo}/main/docs/media/{c_key}_latest.mp4"
        raw_b1_url = f"https://raw.githubusercontent.com/{repo}/main/docs/media/{c_key}_beat1.jpg"
        raw_b2_url = f"https://raw.githubusercontent.com/{repo}/main/docs/media/{c_key}_beat2.jpg"
        raw_b3_url = f"https://raw.githubusercontent.com/{repo}/main/docs/media/{c_key}_beat3.jpg"

        c_name = c_data.get("channel_name", "The Buns")
        latest_draft = {
            "channel_key": c_key,
            "channel_name": c_name,
            "hook_text": render_result.get("hook_text", ""),
            "music_mood": render_result.get("music_mood", ""),
            "cast": render_result.get("cast", []),
            "video_url": raw_mp4_url,
            "watch_url": pages_watch_url,
            "live_beats": render_result.get("live_beats", []),
            "motion_score": render_result.get("motion_score", 0.0),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        queue.setdefault("pending_approvals", []).append(latest_draft)

        token = os.environ.get("GITHUB_TOKEN")
        if token:
            live = render_result.get("live_beats", [])
            dead = [b for b in ("beat1", "beat2", "beat3") if b not in live]
            animators = render_result.get("animators", {})
            how = ", ".join(f"{b}={p}" for b, p in animators.items())
            motion_line = (
                f"**Character motion:** {len(live)}/3 beats genuinely animated - "
                f"measured motion score `{render_result.get('motion_score', 0.0):.1f}` "
                f"(live threshold `{MOTION_THRESHOLD}`)\n"
                f"**Per beat:** `{how}`\n"
                + (f"**Degraded:** {', '.join(dead)} fell back to still-camera-pan "
                   f"(NOT live motion)\n" if dead else "")
                + "\n"
            )
            cast_rows = "\n".join(
                f"| {n} | {c['breed']} | {c['action']} |"
                for n, c in enumerate(latest_draft.get("cast", []), 1)
            )
            issue_body = (
                f"### 🎬 Silent Pet Draft Ready: {c_name}\n\n"
                f"- **▶️ Watch in Browser Player:** [{pages_watch_url}]({pages_watch_url})\n"
                f"- **📥 Direct Raw MP4 Stream:** [Click to open/download MP4]({raw_mp4_url})\n\n"
                f"**Animals only - no owner on camera, no voiceover.**\n"
                f"**On-screen text:** `{latest_draft['hook_text']}`\n"
                f"**Music:** `{latest_draft.get('music_mood','')}` (generated instrumental, no speech)\n"
                f"{motion_line}"
                f"### 🐾 Grounded cast\n"
                f"| Beat | Animal | Doing |\n| :--- | :--- | :--- |\n{cast_rows}\n\n"
                f"### 📸 Beat stills\n"
                f"| Beat 1 | Beat 2 | Beat 3 |\n| :--- | :--- | :--- |\n"
                f"| ![Beat 1]({raw_b1_url}) | ![Beat 2]({raw_b2_url}) | ![Beat 3]({raw_b3_url}) |\n\n"
                f"---\n"
                f"**Mobile Actions:** Apply label `approve` or comment `/publish`."
            )
            requests.post(
                f"https://api.github.com/repos/{repo}/issues",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
                json={"title": f"🐾 [Draft] {c_name}: {latest_draft['hook_text']}", "body": issue_body}
            )

    if failed_channels:
        print("FAILED CHANNELS: " + json.dumps(failed_channels, indent=2))
    build_all_storefronts_and_grounding(latest_draft)
    with open(STATE_QUEUE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2)
    print("✅ Complete! Silent animals-only reel rendered.")

if __name__ == "__main__":
    main()
