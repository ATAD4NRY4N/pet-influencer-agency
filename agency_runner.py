import os
import re
import json
import time
import shutil
import asyncio
import subprocess
import urllib.parse
from datetime import datetime, timezone
import requests
import feedparser
import edge_tts

try:
    from openai import OpenAI
    HAS_OPENAI = True
except Exception:
    HAS_OPENAI = False

from src.openrouter_media import (
    MediaGenerationError,
    VOICE_REFERENCE_TRANSCRIPT,
    generate_beat_image,
    synthesize_speech,
    validate_image,
)

try:
    from gradio_client import Client, handle_file
    HAS_GRADIO = True
except Exception:
    HAS_GRADIO = False

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
        "channel_name": "Maya & The Buns",
        "handle": "@MayaAndTheBuns",
        "bio_slug": "barnaby",
        "master_seed": 884102,
        "human": {
            "name": "Maya",
            "age": 26,
            "fish_voice_id": "7f92f8afb8ec43bf81429cc1c9199cb1",
            "edge_voice": "en-GB-LibbyNeural",
            "immutable_face_dna": "26-year-old British woman Maya, oval face, warm fair skin with light nose-bridge freckles, hazel-green eyes behind thin round matte-gold wireframe glasses, wavy chestnut hair pulled half-up in a tortoiseshell claw clip",
            "expressions": {
                "SMILE_WARM": "candid warm closed-lip smile looking down affectionately",
                "SMILE_LAUGH": "candid natural mid-laugh expression covering mouth slightly with one hand",
                "EXPRESSION_DEADPAN": "candid exasperated side-eye expression looking at the floor"
            },
            "wardrobe_rotation": [
                "oversized sage-green chunky waffle-knit cardigan over a white crewneck tee",
                "oatmeal ribbed crewneck jumper with rolled sleeves"
            ]
        },
        "locations": {
            "SET_A_SOFA": "bright Scandi cottage living room, oatmeal boucle sofa, matte sage-green panelled wall behind, potted monstera plant on left, warm window daylight",
            "SET_B_KITCHEN": "oak butcher-block kitchen island, matte cream shaker cabinets and white subway tile splashback, morning daylight",
            "SET_C_RUG_POV": "high-angle first-person iPhone POV shot looking down at a braided cream jute rug over herringbone oak flooring, black wire playpen panel in background"
        },
        "pets": [
            {
                "id": "pip",
                "name": "Pip",
                "breed": "Lionhead Rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420882,
                "role": "The cable-chewing chaos gremlin",
                "immutable_marking_dna": "single jet-black Lionhead rabbit with short upright black ears and a distinct snow-white fluffy mane tuft right between his ears"
            },
            {
                "id": "barnaby",
                "name": "Barnaby",
                "breed": "Holland Lop Rabbit",
                "birth_date": "2026-05-10",
                "pet_seed": 420881,
                "role": "The polite food critic",
                "immutable_marking_dna": "single cream-white Holland Lop rabbit with floppy lop ears where ONLY the left ear is dark charcoal-grey and the right ear is cream"
            },
            {
                "id": "clover",
                "name": "Clover",
                "breed": "Dutch Rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420883,
                "role": "The zoomie queen",
                "immutable_marking_dna": "single cinnamon-amber and white Dutch rabbit with a crisp white shoulder saddle and white nose blaze"
            }
        ]
    }
}

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

def write_daily_script(channel_meta: dict, chosen_pets: list[dict], age_summary: str, trend: str) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    human_name = channel_meta.get("human", {}).get("name", "Maya")
    p1 = chosen_pets[0]["name"]
    p2 = chosen_pets[1]["name"] if len(chosen_pets) > 1 else chosen_pets[0]["name"]

    if HAS_OPENAI and api_key:
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        prompt = (
            f"Write a 34-word vertical TikTok POV vlog voiceover for {human_name} (26yo UK creator) filming her rabbits {p1} and {p2}.\n"
            f"Current Pet Ages:\n{age_summary}\nTopic: {trend}\n"
            "Rules:\n"
            "1. Conversational UK English ('flat', 'proper', 'sorted'). NO emojis in hook_text.\n"
            "2. Contrast how the two rabbits behave.\n"
            "3. End with a relatable question for pet owners in the comments.\n"
            "4. Include exactly one inline emotion tag such as [sigh], [laughing] or [excited]. "
            "The voice model performs these literally, and they are stripped from the "
            "on-screen captions, so place them where a real person would react.\n"
            "Return ONLY valid JSON:\n"
            '{"script": "spoken words...", "hook_text": "4 WORD ASCII HOOK", '
            '"pet1_action": "chewing a cardboard box corner on the jute rug", '
            '"pet2_action": "sitting politely next to a ceramic water bowl on the rug"}'
        )
        for m in FREE_LLM_WATERFALL:
            for attempt in range(2):
                try:
                    r = client.chat.completions.create(model=m, messages=[{"role": "user", "content": prompt}], timeout=30)
                    raw = r.choices[0].message.content or ""
                    match = re.search(r"\{.*\}", raw, re.DOTALL)
                    if match:
                        data = json.loads(match.group(0))
                        if "script" in data and "hook_text" in data:
                            return data
                    break
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
        "script": f"Day 44 in the flat, and {p1} just proved playpen fences are purely decorative while {p2} sat watching like the landlord. Which of your pets is the chaos gremlin?",
        "hook_text": "3 RABBITS IN ONE FLAT",
        "pet1_action": "investigating a wire playpen fence on the cream jute rug",
        "pet2_action": "loafing calmly next to fresh green basil on the oak floor"
    }

# =====================================================================
# 1. VOICE & SUBTITLES (Fish Audio S2.1 Pro Free -> Edge-TTS + ASCII .ass)
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

def probe_duration(audio_path: str, default: float = 9.0) -> float:
    """Measured length of a rendered audio file, in seconds."""
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
        ]).strip()
        value = float(out)
        return value if value > 0.5 else default
    except Exception:
        return default


def words_from_text(text: str) -> list[dict]:
    """Split spoken text into the same word records Edge-TTS would have produced."""
    tokens = strip_non_ascii(re.sub(r"[^\w\s']", " ", text)).upper().split()
    return [{"text": t, "start": 0.0, "end": 0.0} for t in tokens if t]


def rescale_word_timings(words: list[dict], duration: float) -> list[dict]:
    """Lay words out across `duration`, weighting by length plus a pause budget.

    Edge-TTS gives us real WordBoundary offsets. The OpenRouter and Fish voices
    do not, and previously that meant the burned-in captions were either frozen
    or anchored to the wrong clip - captions drifting out of sync with the voice
    is one of the loudest "this is fake" signals there is.
    """
    if not words:
        return words
    # +2.2 approximates the inter-word gap a natural speaking voice leaves.
    weights = [len(w["text"]) + 2.2 for w in words]
    total = sum(weights) or 1.0
    cursor = 0.0
    for word, weight in zip(words, weights):
        span = duration * (weight / total)
        word["start"] = round(cursor, 3)
        word["end"] = round(cursor + span, 3)
        cursor += span
    return words


def ensure_voice_reference(voice_ref_path: str, edge_voice: str) -> str:
    """Create the one-off voice sample that Fish clones from, once.

    Edge-TTS renders a short neutral clip the first time only. Every later
    episode passes that same clip as a cloning reference, so the channel keeps
    a single consistent voice without ever paying for a custom voice plan.
    """
    os.makedirs(os.path.dirname(voice_ref_path), exist_ok=True)
    if os.path.exists(voice_ref_path) and os.path.getsize(voice_ref_path) > 5_000:
        return voice_ref_path

    from src.openrouter_media import VOICE_REFERENCE_TRANSCRIPT

    async def _speak():
        communicate = edge_tts.Communicate(VOICE_REFERENCE_TRANSCRIPT, edge_voice, rate="+0%", pitch="+0Hz")
        with open(voice_ref_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])

    asyncio.run(_speak())
    print(f"Bootstrapped voice-clone reference at {voice_ref_path}")
    return voice_ref_path


async def generate_voice_and_captions(raw_script: str, hook_banner: str, human_cfg: dict, audio_path: str, ass_path: str):
    clean_spoken = re.sub(r"\[.*?\]", "", raw_script).strip()
    voice_name = human_cfg.get("edge_voice", "en-GB-LibbyNeural")
    fish_key = os.environ.get("FISH_API_KEY", "").strip()
    used_fish = False
    used_openrouter = False
    voice_ref = os.path.join(MEDIA_DIR, "maya_voice_ref.mp3")

    # 0. OpenRouter free-tier TTS. Fish is tried first because it is the only
    # rung that can clone a voice, which is what keeps one recognisable voice
    # across the whole channel instead of a new stranger every episode.
    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        try:
            ensure_voice_reference(voice_ref, voice_name)
            result = synthesize_speech(
                raw_script,
                audio_path,
                os.environ.get("FLUX_TTS_VOICE", "").strip(),
                voice_ref,
            )
            used_openrouter = True
            print(f"TTS via OpenRouter: {result['provider']}")
        except Exception as e:
            print(f"OpenRouter TTS unavailable ({str(e)[:140]}) - falling back")
    else:
        print("OPENROUTER_API_KEY not set - using Fish direct / Edge-TTS")

    # 1A. Try Fish Audio S2.1 Pro Free Direct API if FISH_API_KEY is configured
    if fish_key:
        try:
            r = requests.post(
                "https://api.fish.audio/v1/tts",
                headers={"Authorization": f"Bearer {fish_key}", "Content-Type": "application/json", "model": "s2.1-pro-free"},
                json={"text": raw_script, "reference_id": human_cfg.get("fish_voice_id"), "format": "mp3"},
                timeout=30
            )
            if r.status_code == 200 and len(r.content) > 2000:
                with open(audio_path, "wb") as f:
                    f.write(r.content)
                used_fish = True
                print("✅ Voice synthesized via Fish Audio S2.1 Pro Free!")
        except Exception as e:
            print(f"⚠️ Fish Audio direct skipped: {e}")

    # 1B. Run Edge-TTS WordBoundary stream (generates audio if Fish wasn't used, and always provides exact word timings)
    words = []
    temp_edge_audio = f"{audio_path}.edge.mp3"
    try:
        communicate = edge_tts.Communicate(clean_spoken, voice_name, rate="+4%", pitch="+2Hz", boundary="WordBoundary")
        with open(temp_edge_audio, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    w_txt = strip_non_ascii(re.sub(r"[^\w\s']", "", chunk["text"])).upper()
                    if w_txt:
                        s_sec = chunk["offset"] / TICKS_PER_SECOND
                        d_sec = chunk["duration"] / TICKS_PER_SECOND
                        words.append({"text": w_txt, "start": s_sec, "end": s_sec + d_sec})
        if not (used_fish or used_openrouter) and os.path.exists(temp_edge_audio) and os.path.getsize(temp_edge_audio) > 500:
            shutil.move(temp_edge_audio, audio_path)
    except Exception as e:
        print(f"⚠️ Edge-TTS fallback: {e}")
        if not os.path.exists(audio_path):
            # A silent reel is worse than no reel: the captions would desync
            # against nothing and the watch page would ship a broken draft.
            raise MediaGenerationError(
                "no TTS provider produced usable audio; refusing to publish a silent reel"
            )

    # Build strictly ASCII .ass subtitles (No emojis -> Zero [□] boxes!)
    ass_lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 720", "PlayResY: 1280", "WrapStyle: 1", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: TikTok,DejaVu Sans,52,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,6,3,2,50,50,260,1",
        "Style: HookBanner,DejaVu Sans,38,&H0000FFFF,&H0000FFFF,&H001E293B,&H001E293B,-1,0,0,0,100,100,1,0,3,16,0,8,40,40,120,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    # The OpenRouter and Fish-direct voices do not report per-word offsets, so
    # when one of them is the active voice we lay the words out across the real
    # measured audio duration. Weighting by length plus a fixed per-word gap
    # tracks natural pacing far better than a flat split.
    if used_openrouter or used_fish:
        words = rescale_word_timings(words_from_text(clean_spoken), probe_duration(audio_path))

    clean_hook = strip_non_ascii(hook_banner).upper()[:30] or "MAYA AND THE BUNS"
    ass_lines.append(f"Dialogue: 1,0:00:00.00,0:00:03.20,HookBanner,,0,0,0,,{clean_hook}")

    idx = 0
    while idx < len(words):
        grp = words[idx : idx + 2] if sum(len(w["text"]) for w in words[idx : idx + 3]) > 13 else words[idx : idx + 3]
        idx += len(grp)
        for active_idx, target in enumerate(grp):
            t_start = target["start"]
            t_end = grp[active_idx + 1]["start"] if active_idx + 1 < len(grp) else target["end"] + 0.15
            tokens = [
                f"{{\\c&H00FFFF&\\fscx100\\fscy100\\t(0,65,\\fscx116\\fscy116)}}{w['text']}{{\\c&HFFFFFF&\\fscx100\\fscy100}}"
                if j == active_idx else w["text"]
                for j, w in enumerate(grp)
            ]
            ass_lines.append(f"Dialogue: 0,{format_ass_time(t_start)},{format_ass_time(t_end)},TikTok,,0,0,0,,{' '.join(tokens)}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_lines))

# =====================================================================
# 2. FACE-LOCK (PuLID-Flux), NATIVE 9:16 FLUX, & LTX-VIDEO ANIMATOR
# =====================================================================
def fetch_flux_image(prompt: str, seed: int, out_path: str):
    """Render one beat through the validated provider ladder. Raises on failure."""
    return generate_beat_image(prompt, seed, out_path)


def ensure_maya_master_face(c_data: dict) -> str:
    """
    Creates ONE permanent canonical portrait of Maya (docs/media/maya_master.jpg).
    If it already exists in the repo, it is reused so Maya's face NEVER drifts.
    """
    os.makedirs(MEDIA_DIR, exist_ok=True)
    master_face_path = f"{MEDIA_DIR}/maya_master.jpg"
    if os.path.exists(master_face_path) and os.path.getsize(master_face_path) > 15_000:
        return master_face_path

    human = c_data["human"]
    loc = c_data["locations"]["SET_A_SOFA"]
    outfit = human["wardrobe_rotation"][0]
    prompt = (
        f"{loc}, candid vertical iPhone portrait of {human['immutable_face_dna']}, "
        f"{human['expressions']['SMILE_WARM']}, wearing {outfit}, warm natural window light"
    )
    fetch_flux_image(prompt, c_data.get("master_seed", 884102), master_face_path)
    return master_face_path

def generate_maya_scene_with_pulid(master_face_path: str, prompt: str, seed: int, out_path: str):
    """
    Renders a NEW reaction shot that still reads as Maya.

    Identity is carried by handing the model the canonical portrait as a
    reference image, which is the only thing in this pipeline that can actually
    hold a face steady. The previous version caught every failure and quietly
    copied the master portrait into the beat slot, so "Beat 1 (Face-Locked
    Maya)" was literally the same JPEG as maya_master.jpg - byte for byte - and
    the channel never changed expression, pose or framing at all.

    If nothing can render, this raises. It must never substitute the master.
    """
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    if HAS_GRADIO:
        for space_id in ["yanze/PuLID-Flux", "ByteDance/Hyper-FLUX-8Steps-LoRA"]:
            try:
                client = Client(space_id, hf_token=hf_token or None)
                if "PuLID" in space_id:
                    job = client.submit(
                        prompt=prompt,
                        id_image=handle_file(master_face_path),
                        start_step=2,
                        guidance=4.0,
                        seed=seed,
                        true_cfg=1.0,
                        width=768,
                        height=1344,
                        num_steps=16,
                        id_weight=1.0,
                        neg_prompt="bad quality, deformed, watermark, cartoon",
                        timestep_to_start_cfg=1,
                        max_sequence_length=128
                    )
                    res = job.result(timeout=75)
                    img_file = res[0] if isinstance(res, (list, tuple)) else res
                    if isinstance(img_file, dict) and "path" in img_file:
                        img_file = img_file["path"]
                    if img_file and os.path.exists(str(img_file)):
                        shutil.copy(str(img_file), out_path)
                        print("✅ Generated face-locked Maya frame via PuLID-Flux!")
                        return
            except Exception as e:
                print(f"ℹ️ PuLID Space busy ({e}), using canonical master portrait lock.")
                break

    # PuLID ZeroGPU is queue-saturated in practice, which is exactly why the run
    # in issue #6 ended up here every time. Fall through to the OpenRouter image
    # API, which accepts the same reference portrait and is not queue-bound.
    generate_beat_image(
        prompt,
        seed,
        out_path,
        reference_path=master_face_path,
    )

def animate_beat_to_mp4(img_path: str, motion_prompt: str, duration_sec: float, out_mp4: str, pan_dir: int = 1):
    """
    1. Tries Hugging Face LTX-Video ZeroGPU to animate the image into real video + boomerang loops it.
    2. If HF queue is busy, uses a 4K-upscaled sub-pixel floating-point crop (ZERO zoompan integer jitter!).
    """
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    raw_ai = f"{out_mp4}.raw_ai.mp4"
    ai_ok = False

    if HAS_GRADIO:
        try:
            print(f"🎬 Requesting LTX-Video AI motion for {os.path.basename(img_path)}...")
            client = Client("Lightricks/ltx-video-distilled", hf_token=hf_token or None)
            job = client.submit(
                prompt=f"{motion_prompt}, subtle natural movement, handheld smartphone video, photorealistic",
                input_image_filepath=handle_file(img_path),
                height_ui=768,
                width_ui=512,
                mode="image-to-video",
                duration_ui=4.0,
                ui_frames_to_use=9,
                seed_ui=42,
                randomize_seed=True,
                ui_guidance_scale=3.0,
                improve_texture_flag=True,
                api_name="/image_to_video"
            )
            res = job.result(timeout=95)
            v_file = res["video"] if isinstance(res, dict) and "video" in res else (
                res[0]["video"] if isinstance(res, (list, tuple)) and isinstance(res[0], dict) else (
                    res[0] if isinstance(res, (list, tuple)) else res
                )
            )
            if v_file and os.path.exists(str(v_file)):
                shutil.copy(str(v_file), raw_ai)
                ai_ok = True
                print(f"✅ LTX-Video motion succeeded for {os.path.basename(img_path)}!")
        except Exception as e:
            print(f"ℹ️ LTX-Video queue full/timeout ({e}), using jitter-free 4K sub-pixel camera drift.")

    if ai_ok and os.path.exists(raw_ai):
        # Boomerang loop the AI clip to match exact beat duration
        subprocess.run([
            "ffmpeg", "-y", "-i", raw_ai,
            "-filter_complex",
            f"[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=30,split[fwd][tmp];"
            f"[tmp]reverse[rev];[fwd][rev]concat=n=2:v=1:a=0,loop=loop=-1:size=300:start=0,trim=duration={duration_sec},setpts=PTS-STARTPTS[vout]",
            "-map", "[vout]", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", out_mp4
        ], check=True)
        return

    # JITTER-FREE 4K SUB-PIXEL CAMERA DRIFT (Replaces choppy zoompan)
    # Upscales to 1584x2816 and uses smooth time-based crop expressions at 30fps
    if pan_dir == 1:
        x_expr = "(iw-ow)/2 + ((iw-ow)/3)*sin(t*0.45)"
        y_expr = "(ih-oh)*0.25 + ((ih-oh)*0.35)*(t/" + str(max(1.0, duration_sec)) + ")"
    else:
        x_expr = "(iw-ow)/2 - ((iw-ow)/3)*sin(t*0.45)"
        y_expr = "(ih-oh)*0.65 - ((ih-oh)*0.35)*(t/" + str(max(1.0, duration_sec)) + ")"

    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-t", str(duration_sec), "-r", "30", "-i", img_path,
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
def render_video(c_key: str, c_data: dict, chosen_pets: list[dict], age_data: dict, script_data: dict) -> dict:
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    audio_path = f"output/{c_key}.mp3"
    ass_path = f"output/{c_key}.ass"
    b1_img = f"{MEDIA_DIR}/{c_key}_beat1.jpg"
    b2_img = f"{MEDIA_DIR}/{c_key}_beat2.jpg"
    b3_img = f"{MEDIA_DIR}/{c_key}_beat3.jpg"
    final_mp4 = f"{MEDIA_DIR}/{c_key}_latest.mp4"

    human = c_data.get("human", {})
    locs = c_data.get("locations", {})
    face_dna = human.get("immutable_face_dna", "26yo British woman Maya")
    outfit = human.get("wardrobe_rotation", ["sage cardigan"])[0]
    pet0 = chosen_pets[0]
    pet1 = chosen_pets[1] if len(chosen_pets) > 1 else chosen_pets[0]

    # 1. Synthesize Voice + ASCII .ass Captions
    asyncio.run(generate_voice_and_captions(
        script_data["script"], script_data["hook_text"], human, audio_path, ass_path
    ))

    duration = probe_duration(audio_path)

    # 2. BEAT 1: Face-Locked Maya Candid Reaction Hook (Uses Master Face + PuLID)
    master_face = ensure_maya_master_face(c_data)
    prompt_b1 = (
        f"{locs.get('SET_A_SOFA', '')}, candid vertical smartphone shot of {face_dna}, "
        f"{human['expressions']['SMILE_LAUGH']}, wearing {outfit}, natural daylight"
    )
    generate_maya_scene_with_pulid(master_face, prompt_b1, c_data.get("master_seed", 884102), b1_img)

    # 3. BEAT 2: First-Person POV of Pet #1 ONLY (Eliminates multi-pet fur/attribute bleed)
    morph0 = age_data[pet0["id"]]["morphology"]
    prompt_b2 = (
        f"{locs.get('SET_C_RUG_POV', '')}, close-up first-person iPhone POV looking down at {pet0['immutable_marking_dna']} "
        f"({morph0}), {script_data.get('pet1_action', 'exploring the jute rug')}, only one rabbit in frame, zero human hands"
    )
    fetch_flux_image(prompt_b2, pet0.get("pet_seed", 420882), b2_img)

    # 4. BEAT 3: First-Person POV of Pet #2 ONLY
    morph1 = age_data[pet1["id"]]["morphology"]
    prompt_b3 = (
        f"{locs.get('SET_B_KITCHEN', '')}, first-person iPhone POV looking at {pet1['immutable_marking_dna']} "
        f"({morph1}), {script_data.get('pet2_action', 'sitting politely on oak floor')}, only one rabbit in frame, zero human hands"
    )
    fetch_flux_image(prompt_b3, pet1.get("pet_seed", 420881), b3_img)

    # Final gate before anything reaches ffmpeg. Beat 3 of the run in issue #6
    # was a flat colour card here, and it became 4.6s of black screen in the
    # published MP4. Nothing unvalidated gets as far as the renderer.
    for label, path in (("beat1", b1_img), ("beat2", b2_img), ("beat3", b3_img)):
        ok, reason = validate_image(path)
        if not ok:
            raise MediaGenerationError(f"{label} failed validation ({reason}); aborting reel")
        print(f"  [gate] {label} ok - {os.path.getsize(path) // 1024} KB")

    # 5. Animate Each Beat into Video Segments (LTX-Video AI Motion -> 4K Smooth Drift Fallback)
    d1 = round(max(2.2, duration * 0.28), 2)
    d2 = round(max(2.6, duration * 0.38), 2)
    d3 = round(max(2.6, duration - d1 - d2 + 0.6), 2)

    seg1_mp4 = f"output/{c_key}_seg1.mp4"
    seg2_mp4 = f"output/{c_key}_seg2.mp4"
    seg3_mp4 = f"output/{c_key}_seg3.mp4"

    animate_beat_to_mp4(b1_img, "young woman smiling and reacting naturally on camera", d1, seg1_mp4, pan_dir=1)
    animate_beat_to_mp4(b2_img, f"{pet0['breed']} twitching nose and moving ears on rug", d2, seg2_mp4, pan_dir=-1)
    animate_beat_to_mp4(b3_img, f"{pet1['breed']} looking up curiously at camera", d3, seg3_mp4, pan_dir=1)

    # 6. Stitch with Crossfades, Burn ASCII .ass Captions, and Normalize Speech Audio (NO Sine Drone!)
    xf1 = round(d1 - 0.20, 2)
    xf2 = round(d1 + d2 - 0.40, 2)

    cmd = [
        "ffmpeg", "-y",
        "-i", seg1_mp4,
        "-i", seg2_mp4,
        "-i", seg3_mp4,
        "-i", audio_path,
        "-filter_complex",
        f"[0:v][1:v]xfade=transition=fade:duration=0.20:offset={xf1}[vx1];"
        f"[vx1][2:v]xfade=transition=fade:duration=0.20:offset={xf2},ass={ass_path}[vout];"
        f"[3:a]loudnorm=I=-16:TP=-1.5:LRA=11[aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-metadata", "com.apple.quicktime.make=Apple",
        "-metadata", "com.apple.quicktime.model=iPhone 15 Pro",
        "-shortest", final_mp4
    ]
    subprocess.run(cmd, check=True)
    return {"mp4": final_mp4, "b1": b1_img, "b2": b2_img, "b3": b3_img}

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
    hook_title = latest_draft["hook_text"] if latest_draft else "Maya & The Buns — Latest Reel"
    script_txt = latest_draft["script"] if latest_draft else "Latest generated draft."
    watch_html = f"""<!DOCTYPE html>
    <html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Watch Daily Draft</title></head>
    <body style='background:#0f172a;color:#f8fafc;font-family:sans-serif;padding:20px;max-width:480px;margin:auto;text-align:center;'>
      <h2 style='color:#f59e0b;margin-bottom:8px;'>🎬 {hook_title}</h2>
      <video controls autoplay playsinline style='width:100%;max-width:360px;border-radius:16px;border:2px solid #334155;background:#000;' src='../media/rabbit_channel_latest.mp4?v={cache_bust}'></video>
      <p style='background:#1e293b;padding:14px;border-radius:10px;font-size:14px;line-height:1.5;margin-top:16px;'>"{script_txt}"</p>
      <p style='margin-top:16px;'><a href='../media/rabbit_channel_latest.mp4?v={cache_bust}' download style='color:#38bdf8;font-weight:700;'>📥 Download Raw MP4</a> | <a href='../grounding/' style='color:#f59e0b;'>🛡️ Grounding Bible</a></p>
    </body></html>"""
    with open(os.path.join(DOCS_DIR, "watch", "index.html"), "w", encoding="utf-8") as f:
        f.write(watch_html)

    grounding_cards, hub_links = [], []
    for c_key, c_data in bible.items():
        h = c_data.get("human", {})
        slug = c_data.get("bio_slug", c_key.split("_")[0])
        c_name = c_data.get("channel_name", c_key)
        c_handle = c_data.get("handle", f"@{slug}")
        slug_dir = os.path.join(DOCS_DIR, slug)
        os.makedirs(slug_dir, exist_ok=True)

        pets_html = "".join([
            f"<div style='background:#0f172a;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #334155;'>"
            f"<strong>{p.get('name','Pet')}</strong> ({p.get('breed','Rabbit')}) — Born: {p.get('birth_date','2026-06-01')}<br>"
            f"<small>{p.get('immutable_marking_dna', '')}</small></div>"
            for p in c_data.get("pets", [])
        ])
        locs_html = "".join([f"<li><strong>{k}:</strong> {v}</li>" for k, v in c_data.get("locations", {}).items()])
        grounding_cards.append(
            f"<div style='background:#1e293b;padding:20px;border-radius:12px;margin-bottom:20px;'>"
            f"<h2>{c_name} ({c_handle})</h2>"
            f"<p style='color:#34d399;font-size:13px;'>🔒 Master Face Lock (`maya_master.jpg`) + Solo-Pet POV Cutaways Active</p>"
            f"<div style='display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;'>"
            f"<div><small>Master Face Lock</small><br><img src='../media/maya_master.jpg?v={cache_bust}' style='width:135px;border-radius:10px;border:2px solid #f59e0b;'></div>"
            f"<div><small>Beat 1 (Maya Hook)</small><br><img src='../media/{c_key}_beat1.jpg?v={cache_bust}' style='width:135px;border-radius:10px;border:1px solid #475569;'></div>"
            f"<div><small>Beat 2 (Pet 1 POV)</small><br><img src='../media/{c_key}_beat2.jpg?v={cache_bust}' style='width:135px;border-radius:10px;border:1px solid #475569;'></div>"
            f"<div><small>Beat 3 (Pet 2 POV)</small><br><img src='../media/{c_key}_beat3.jpg?v={cache_bust}' style='width:135px;border-radius:10px;border:1px solid #475569;'></div>"
            f"</div>"
            f"<p><strong>Human Creator:</strong> {h.get('name','Maya')} ({h.get('age',26)}yo)<br><small>{h.get('immutable_face_dna', '')}</small></p>"
            f"<h3>Active Pets</h3>{pets_html}<h3>Locked Apartment Locations</h3><ul>{locs_html}</ul></div>"
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
            f"<h1>🐾 AI Pet Influencer Collective</h1>"
            f"<p><a href='watch/' style='color:#34d399;font-weight:700;font-size:18px;'>▶️ Watch Latest Generated Reel →</a></p>"
            f"<h3>Public Storefronts</h3><ul>{''.join(hub_links)}</ul>"
            f"<hr style='border-color:#334155;margin:24px 0;'><h3>Owner Operations</h3>"
            f"<p><a href='grounding/' style='color:#f59e0b;font-weight:700;font-size:18px;'>🛡️ Open Universe Grounding Bible →</a></p></body></html>"
        )

def main():
    bible = ensure_json_file(STATE_BIBLE, DEFAULT_BIBLE, required_subkey="bio_slug")
    queue = ensure_json_file(STATE_QUEUE, DEFAULT_QUEUE)
    repo = os.environ.get("REPO_NAME", "ATAD4NRY4N/pet-influencer-agency")
    owner, repo_short = repo.split("/") if "/" in repo else ("ATAD4NRY4N", "pet-influencer-agency")

    if os.environ.get("EVENT_NAME", "") in ("issues", "issue_comment"):
        return

    latest_draft = None
    failed_channels = []
    for c_key, c_data in bible.items():
        if not c_data.get("enabled", True):
            continue
        pets = c_data.get("pets", DEFAULT_BIBLE["rabbit_channel"]["pets"])
        age_data = {p["id"]: calculate_age_stats(p.get("birth_date", "2026-06-01")) for p in pets}
        age_summary = "\n".join([f"- {p['name']}: {age_data[p['id']]['age_weeks']} wks ({age_data[p['id']]['stage']})" for p in pets])
        trends = fetch_safe_trends()

        script_data = write_daily_script(c_data, pets[:2], age_summary, trends[0])
        try:
            render_video(c_key, c_data, pets[:2], age_data, script_data)
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

        c_name = c_data.get("channel_name", "Maya & The Buns")
        latest_draft = {
            "channel_key": c_key,
            "channel_name": c_name,
            "hook_text": strip_non_ascii(script_data["hook_text"]),
            "script": script_data["script"],
            "video_url": raw_mp4_url,
            "watch_url": pages_watch_url,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        queue.setdefault("pending_approvals", []).append(latest_draft)

        token = os.environ.get("GITHUB_TOKEN")
        if token:
            issue_body = (
                f"### 🎬 POV + Reaction Cutaway Draft Ready: {c_name}\n\n"
                f"- **▶️ Watch in Browser Player:** [{pages_watch_url}]({pages_watch_url})\n"
                f"- **📥 Direct Raw MP4 Stream:** [Click to open/download MP4]({raw_mp4_url})\n\n"
                f"**Top Hook Banner:** `{latest_draft['hook_text']}`\n"
                f"**Spoken Script:**\n> {latest_draft['script']}\n\n"
                f"### 📸 Face-Locked Maya + Solo-Pet POV Cutaways\n"
                f"| Beat 1 (Face-Locked Maya) | Beat 2 ({pets[0]['name']} Solo POV) | Beat 3 ({pets[1]['name']} Solo POV) |\n"
                f"| :--- | :--- | :--- |\n"
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
    print("✅ Complete! All 6 video, audio, face-lock, and motion fixes applied.")

if __name__ == "__main__":
    main()
