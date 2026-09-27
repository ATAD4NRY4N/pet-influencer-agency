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
            "edge_voice": "en-GB-SoniaNeural",
            "immutable_face_dna": "26-year-old British woman Maya, oval face, warm fair skin with light nose-bridge freckles, hazel-green eyes behind thin round gold wireframe glasses, wavy chestnut hair pulled half-up in a tortoiseshell claw clip",
            "expressions": {
                "SMILE_WARM": "gentle closed-lip warm smile with a subtle left-cheek dimple",
                "SMILE_LAUGH": "natural open-mouth laughing smile, crinkled amused eyes",
                "EXPRESSION_DEADPAN": "deadpan unimpressed expression raising one eyebrow at the camera"
            },
            "wardrobe_rotation": [
                "oversized sage-green chunky waffle-knit cardigan over a white crewneck tee",
                "oatmeal ribbed crewneck jumper with rolled sleeves"
            ]
        },
        "locations": {
            "SET_A_SOFA": "inside a bright Scandi living room, sitting by an oatmeal boucle sofa, matte sage-green panelled wall behind, potted monstera plant on left, warm window daylight, herringbone oak floor",
            "SET_B_KITCHEN": "standing at an oak butcher-block kitchen island, matte cream shaker cabinets and white subway tile splashback behind her, morning daylight",
            "SET_C_RUG_POV": "high-angle first-person POV smartphone shot looking down at a braided cream jute rug over herringbone oak flooring, black wire playpen fence on top edge"
        },
        "pets": [
            {
                "id": "barnaby",
                "name": "Barnaby",
                "breed": "Holland Lop Rabbit",
                "birth_date": "2026-05-10",
                "pet_seed": 420881,
                "role": "The polite food critic",
                "immutable_marking_dna": "cream-white Holland Lop rabbit with floppy ears where ONLY the left ear is dark charcoal-grey and the right ear is cream"
            },
            {
                "id": "pip",
                "name": "Pip",
                "breed": "Lionhead Rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420882,
                "role": "The cable-chewing chaos gremlin",
                "immutable_marking_dna": "jet-black Lionhead rabbit with upright black ears and a distinct snow-white fluffy mane tuft right between his ears"
            },
            {
                "id": "clover",
                "name": "Clover",
                "breed": "Dutch Rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420883,
                "role": "The zoomie queen",
                "immutable_marking_dna": "cinnamon-amber and white Dutch rabbit with a crisp white shoulder saddle and white nose blaze"
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
        stage, morph = "baby_kit", "tiny palm-sized baby kit with oversized ears and downy fluff fur"
    elif age_days < 180:
        stage, morph = "adolescent", "juvenile half-grown adolescent rabbit with lanky body proportions"
    else:
        stage, morph = "prime_adult", "full-grown prime adult rabbit with dense glossy fur"
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

def write_daily_script(channel_meta: dict, chosen_pets: list[dict], age_summary: str, trend: str) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    human_name = channel_meta.get("human", {}).get("name", "Maya")
    pet_names = ", ".join([p.get("name", "Barnaby") for p in chosen_pets])

    if HAS_OPENAI and api_key:
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        prompt = (
            f"Write a 34-word vertical TikTok/Reel voiceover for {human_name} (26yo UK creator) with her rabbits ({pet_names}).\n"
            f"Current Pet Ages:\n{age_summary}\nTopic: {trend}\n"
            "Rules:\n"
            "1. Sound like a genuine UK pet owner ('flat', 'proper', 'sorted').\n"
            "2. End with a relatable question for pet owners in the comments that grammatically loops back into the first word.\n"
            "Return ONLY valid JSON:\n"
            '{"script": "spoken words...", "hook_text": "4-WORD UPPERCASE HOOK", '
            '"beat2_action": "two rabbits investigating a wooden hay rack on the braided jute rug"}'
        )
        for m in ["openrouter/free", "meta-llama/llama-3.3-70b-instruct:free", "google/gemma-3-27b-it:free"]:
            try:
                r = client.chat.completions.create(model=m, messages=[{"role": "user", "content": prompt}], timeout=25)
                raw = r.choices[0].message.content or ""
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    if "script" in data and "hook_text" in data:
                        return data
            except Exception as e:
                print(f"⚠️ Model {m} skipped: {e}")

    return {
        "script": "Day 42 with three house rabbits, and Pip just proved playpen fences are purely decorative. Barnaby didn't even blink. Which of your pets is the chaos gremlin?",
        "hook_text": "3 RABBITS VS 1 FLAT",
        "beat2_action": "rabbits foraging together on the living room braided jute rug"
    }

def ensure_warm_bgm(bgm_path: str):
    """Synthesizes a warm, subtle lo-fi chord bed so there is zero dead air behind speech."""
    if os.path.exists(bgm_path) and os.path.getsize(bgm_path) > 1000:
        return
    os.makedirs(os.path.dirname(bgm_path), exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "aevalsrc='0.04*sin(2*PI*220*t)+0.03*sin(2*PI*277.18*t)+0.03*sin(2*PI*329.63*t)':s=44100:d=25",
        "-af", "lowpass=f=800,afade=t=in:ss=0:d=1",
        "-c:a", "libmp3lame", "-b:a", "128k", bgm_path
    ], check=True)

def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

async def generate_voice_and_captions(raw_script: str, hook_banner: str, voice_name: str, audio_path: str, ass_path: str):
    clean_spoken = re.sub(r"\[.*?\]", "", raw_script).strip()
    words = []
    try:
        communicate = edge_tts.Communicate(clean_spoken, voice_name, rate="+6%", boundary="WordBoundary")
        with open(audio_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    w_txt = re.sub(r"[^\w\s']", "", chunk["text"]).strip().upper()
                    if w_txt:
                        s_sec = chunk["offset"] / TICKS_PER_SECOND
                        d_sec = chunk["duration"] / TICKS_PER_SECOND
                        words.append({"text": w_txt, "start": s_sec, "end": s_sec + d_sec})
    except Exception as e:
        print(f"⚠️ Edge-TTS fallback: {e}")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "7",
            "-c:a", "libmp3lame", audio_path
        ], check=True)

    ass_lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 720", "PlayResY: 1280", "WrapStyle: 1", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        # Lower-third dynamic word-pop style
        "Style: TikTok,DejaVu Sans,52,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,6,3,2,50,50,270,1",
        # Top Hook Pill Banner style (BorderStyle=3 draws an opaque rounded box)
        "Style: HookBanner,DejaVu Sans,40,&H0000FFFF,&H0000FFFF,&H001E293B,&H001E293B,-1,0,0,0,100,100,1,0,3,14,0,8,40,40,130,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    # Add Top Hook Banner for the first 3.2 seconds
    clean_hook = hook_banner.replace("{", "").replace("}", "").upper()[:32]
    ass_lines.append(f"Dialogue: 1,0:00:00.00,0:00:03.20,HookBanner,,0,0,0,,🐾 {clean_hook}")

    # Smart 2-word grouping so long words never clip off-screen when scaled to 115%
    idx = 0
    while idx < len(words):
        grp = words[idx : idx + 2] if sum(len(w["text"]) for w in words[idx : idx + 3]) > 14 else words[idx : idx + 3]
        idx += len(grp)
        for active_idx, target in enumerate(grp):
            t_start = target["start"]
            t_end = grp[active_idx + 1]["start"] if active_idx + 1 < len(grp) else target["end"] + 0.15
            tokens = [
                f"{{\\c&H00FFFF&\\fscx100\\fscy100\\t(0,70,\\fscx116\\fscy116)}}{w['text']}{{\\c&HFFFFFF&\\fscx100\\fscy100}}"
                if j == active_idx else w["text"]
                for j, w in enumerate(grp)
            ]
            ass_lines.append(f"Dialogue: 0,{format_ass_time(t_start)},{format_ass_time(t_end)},TikTok,,0,0,0,,{' '.join(tokens)}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_lines))

def fetch_valid_image(prompt: str, seed: int, out_path: str):
    """Requests native 9:16 (768x1344) vertical aspect ratio from HF FLUX.1-schnell."""
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    if hf_token:
        try:
            hf_url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
            payload = {
                "inputs": f"{prompt}, vertical 9:16 iPhone 15 UGC footage, natural indoor daylight, sharp focus",
                "parameters": {"width": 768, "height": 1344, "num_inference_steps": 4, "seed": seed}
            }
            r = requests.post(hf_url, headers={"Authorization": f"Bearer {hf_token}"}, json=payload, timeout=40)
            if r.status_code == 200 and (r.content.startswith(b"\xff\xd8") or r.content.startswith(b"\x89PNG")):
                with open(out_path, "wb") as f:
                    f.write(r.content)
                print(f"✅ Native 9:16 image via HF FLUX.1-schnell: {out_path}")
                return
        except Exception as e:
            print(f"⚠️ HF FLUX.1-schnell skipped: {e}")

    encoded = urllib.parse.quote(prompt[:320])
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=720&height=1280&seed={seed}&model=flux&nologo=true"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
        if resp.status_code == 200 and (resp.content.startswith(b"\xff\xd8") or resp.content.startswith(b"\x89PNG")):
            with open(out_path, "wb") as f:
                f.write(resp.content)
            return
    except Exception:
        pass

    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x1e293b:s=720x1280:d=1",
        "-frames:v", "1", out_path
    ], check=True)

def render_video(c_key: str, c_data: dict, chosen_pets: list[dict], age_data: dict, script_data: dict) -> dict:
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    audio_path = f"output/{c_key}.mp3"
    ass_path = f"output/{c_key}.ass"
    bgm_path = "output/bgm_warm.mp3"
    b1_img = f"{MEDIA_DIR}/{c_key}_beat1.jpg"
    b2_img = f"{MEDIA_DIR}/{c_key}_beat2.jpg"
    b3_img = f"{MEDIA_DIR}/{c_key}_beat3.jpg"
    final_mp4 = f"{MEDIA_DIR}/{c_key}_latest.mp4"

    ensure_warm_bgm(bgm_path)

    human = c_data.get("human", {})
    locs = c_data.get("locations", {})
    voice = human.get("edge_voice", "en-GB-SoniaNeural")
    face_dna = human.get("immutable_face_dna", "26yo British woman Maya")
    outfit = human.get("wardrobe_rotation", ["sage cardigan"])[0]
    pet0 = chosen_pets[0]
    pet1 = chosen_pets[1] if len(chosen_pets) > 1 else chosen_pets[0]

    asyncio.run(generate_voice_and_captions(
        script_data["script"], script_data["hook_text"], voice, audio_path, ass_path
    ))

    try:
        duration = float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", audio_path
        ]).strip())
    except Exception:
        duration = 9.0

    # 3 Distinct Visual Beats with Depth-of-Field Separation
    prompt_b1 = (
        f"{locs.get('SET_A_SOFA', '')}, vertical smartphone selfie of {face_dna}, wearing {outfit}, "
        f"{human['expressions']['SMILE_WARM']}, {pet0['immutable_marking_dna']} visible on rug in background"
    )
    prompt_b2 = (
        f"{locs.get('SET_C_RUG_POV', '')}, high-angle first-person POV looking down at {pet0['immutable_marking_dna']} "
        f"and {pet1['immutable_marking_dna']}, {script_data.get('beat2_action', 'exploring rug')}, zero human hands"
    )
    prompt_b3 = (
        f"{locs.get('SET_B_KITCHEN', '')}, medium portrait shot of {face_dna}, wearing {outfit}, "
        f"{human['expressions']['SMILE_LAUGH']}, holding a sprig of fresh green basil, {pet1['immutable_marking_dna']} on counter"
    )

    fetch_valid_image(prompt_b1, c_data.get("master_seed", 884102), b1_img)
    fetch_valid_image(prompt_b2, pet0.get("pet_seed", 420881), b2_img)
    fetch_valid_image(prompt_b3, c_data.get("master_seed", 884102) + 7, b3_img)

    # Proportional 3-Beat Timing + Smooth Handheld Zoom/Pan + Crossfades + Sidechain Audio Ducking
    seg = max(2.2, round((duration + 0.6) / 3.0, 2))
    frames = int(seg * 30)
    xf1 = round(seg - 0.25, 2)
    xf2 = round((seg * 2) - 0.50, 2)

    filter_complex = (
        f"[0:v]scale=820:1458:force_original_aspect_ratio=increase,crop=820:1458,"
        f"zoompan=z='min(zoom+0.0012,1.14)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x1280:fps=30,setsar=1[v0];"
        f"[1:v]scale=820:1458:force_original_aspect_ratio=increase,crop=820:1458,"
        f"zoompan=z='if(eq(on,1),1.14,max(1.0,zoom-0.0012))':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x1280:fps=30,setsar=1[v1];"
        f"[2:v]scale=820:1458:force_original_aspect_ratio=increase,crop=820:1458,"
        f"zoompan=z='min(zoom+0.0010,1.12)':d={frames+15}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x1280:fps=30,setsar=1[v2];"
        f"[v0][v1]xfade=transition=fade:duration=0.25:offset={xf1}[vx1];"
        f"[vx1][v2]xfade=transition=fade:duration=0.25:offset={xf2},ass={ass_path}[vout];"
        f"[3:a]asplit=2[vo][vo_sc];"
        f"[4:a]volume=0.22[bg];"
        f"[bg][vo_sc]sidechaincompress=threshold=0.015:ratio=5:attack=40:release=250[bg_ducked];"
        f"[vo][bg_ducked]amix=inputs=2:duration=first[aout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", b1_img,
        "-i", b2_img,
        "-i", b3_img,
        "-i", audio_path,
        "-i", bgm_path,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-metadata", "com.apple.quicktime.make=Apple",
        "-metadata", "com.apple.quicktime.model=iPhone 15 Pro",
        "-shortest", final_mp4
    ]
    subprocess.run(cmd, check=True)
    return {"mp4": final_mp4, "b1": b1_img, "b2": b2_img, "b3": b3_img}

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
            f"<div style='display:flex;gap:10px;flex-wrap:wrap;margin:12px 0;'>"
            f"<img src='../media/{c_key}_beat1.jpg?v={cache_bust}' style='width:150px;border-radius:10px;border:1px solid #475569;' alt='Beat 1'>"
            f"<img src='../media/{c_key}_beat2.jpg?v={cache_bust}' style='width:150px;border-radius:10px;border:1px solid #475569;' alt='Beat 2'>"
            f"<img src='../media/{c_key}_beat3.jpg?v={cache_bust}' style='width:150px;border-radius:10px;border:1px solid #475569;' alt='Beat 3'>"
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
    for c_key, c_data in bible.items():
        if not c_data.get("enabled", True):
            continue
        pets = c_data.get("pets", DEFAULT_BIBLE["rabbit_channel"]["pets"])
        age_data = {p["id"]: calculate_age_stats(p.get("birth_date", "2026-06-01")) for p in pets}
        age_summary = "\n".join([f"- {p['name']}: {age_data[p['id']]['age_weeks']} wks ({age_data[p['id']]['stage']})" for p in pets])
        trends = fetch_safe_trends()

        script_data = write_daily_script(c_data, pets[:2], age_summary, trends[0])
        render_video(c_key, c_data, pets[:2], age_data, script_data)

        pages_watch_url = f"https://{owner.lower()}.github.io/{repo_short}/watch/"
        raw_mp4_url = f"https://raw.githubusercontent.com/{repo}/main/docs/media/{c_key}_latest.mp4"
        raw_b1_url = f"https://raw.githubusercontent.com/{repo}/main/docs/media/{c_key}_beat1.jpg"
        raw_b2_url = f"https://raw.githubusercontent.com/{repo}/main/docs/media/{c_key}_beat2.jpg"
        raw_b3_url = f"https://raw.githubusercontent.com/{repo}/main/docs/media/{c_key}_beat3.jpg"

        c_name = c_data.get("channel_name", "Maya & The Buns")
        latest_draft = {
            "channel_key": c_key,
            "channel_name": c_name,
            "hook_text": script_data["hook_text"],
            "script": script_data["script"],
            "video_url": raw_mp4_url,
            "watch_url": pages_watch_url,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        queue.setdefault("pending_approvals", []).append(latest_draft)

        token = os.environ.get("GITHUB_TOKEN")
        if token:
            issue_body = (
                f"### 🎬 3-Beat Daily Draft Ready: {c_name}\n\n"
                f"- **▶️ Watch in Browser Player:** [{pages_watch_url}]({pages_watch_url})\n"
                f"- **📥 Direct Raw MP4 Stream:** [Click to open/download MP4]({raw_mp4_url})\n\n"
                f"**Top Hook Pill:** `{latest_draft['hook_text']}`\n"
                f"**Spoken Script:**\n> {latest_draft['script']}\n\n"
                f"### 📸 3-Beat Native 9:16 Storyboard\n"
                f"| Beat 1 (Hook Selfie) | Beat 2 (Pet POV Rug) | Beat 3 (Payoff & Loop) |\n"
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

    build_all_storefronts_and_grounding(latest_draft)
    with open(STATE_QUEUE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2)
    print("✅ Complete! 3-beat video with zoompan, crossfades, hook banner, and ducked BGM rendered.")

if __name__ == "__main__":
    main()
