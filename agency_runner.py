import os
import re
import json
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
            "immutable_face_dna": "26-year-old British woman Maya, oval face, warm fair skin with subtle freckles, hazel-green eyes behind thin round gold glasses, wavy chestnut hair in a claw clip",
            "expressions": {
                "SMILE_WARM": "gentle closed-lip warm smile",
                "SMILE_LAUGH": "natural laughing smile",
                "EXPRESSION_DEADPAN": "deadpan dry flat-line mouth, raised left eyebrow"
            },
            "wardrobe_rotation": [
                "oversized sage-green chunky waffle-knit cardigan over a white crewneck tee"
            ]
        },
        "locations": {
            "SET_A_SOFA": "bright Scandi living room, sitting by an oatmeal boucle sofa, sage-green wall, herringbone oak floor",
            "SET_B_KITCHEN": "oak butcher-block kitchen island, matte cream shaker cabinets, bright morning daylight",
            "SET_C_RUG_POV": "high-angle first-person POV looking down at a braided cream jute rug over oak flooring"
        },
        "pets": [
            {
                "id": "barnaby",
                "name": "Barnaby",
                "breed": "Holland Lop Rabbit",
                "birth_date": "2026-05-10",
                "pet_seed": 420881,
                "role": "The polite food critic",
                "immutable_marking_dna": "cream-white Holland Lop rabbit with floppy ears where ONLY the left ear is charcoal-grey"
            },
            {
                "id": "pip",
                "name": "Pip",
                "breed": "Lionhead Rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420882,
                "role": "The cable-chewing chaos gremlin",
                "immutable_marking_dna": "jet-black Lionhead rabbit with upright ears and a white fluffy mane tuft between his ears"
            },
            {
                "id": "clover",
                "name": "Clover",
                "breed": "Dutch Rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420883,
                "role": "The zoomie queen",
                "immutable_marking_dna": "cinnamon-amber and white Dutch rabbit with a white nose blaze"
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
    stage = "baby_kit" if age_days < 90 else ("adolescent" if age_days < 180 else "prime_adult")
    return {"age_days": age_days, "age_weeks": round(age_days / 7.0, 1), "stage": stage, "morphology": f"{stage} rabbit"}

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
            f"Write a 32-word TikTok/Reel voiceover for {human_name} (26yo UK pet owner) with her rabbits ({pet_names}).\n"
            f"Ages:\n{age_summary}\nTopic: {trend}\n"
            "End with a relatable question for pet owners in the comments. Return ONLY valid JSON:\n"
            '{"script": "spoken words...", "hook_text": "4-word banner hook", "beat2_action": "rabbits exploring the rug"}'
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
        "hook_text": "Three Rabbits vs One Flat",
        "beat2_action": "rabbits foraging together on the living room jute rug"
    }

def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

async def generate_voice_and_captions(raw_script: str, voice_name: str, audio_path: str, ass_path: str):
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
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 720", "PlayResY: 1280", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: TikTok,DejaVu Sans,56,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,5,3,2,40,40,280,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    for i in range(0, len(words), 3):
        grp = words[i : i + 3]
        for active_idx, target in enumerate(grp):
            t_start = target["start"]
            t_end = grp[active_idx + 1]["start"] if active_idx + 1 < len(grp) else target["end"] + 0.15
            tokens = [
                f"{{\\c&H00FFFF&\\fscx115\\fscy115}}{w['text']}{{\\c&HFFFFFF&\\fscx100\\fscy100}}"
                if idx == active_idx else w["text"]
                for idx, w in enumerate(grp)
            ]
            ass_lines.append(f"Dialogue: 0,{format_ass_time(t_start)},{format_ass_time(t_end)},TikTok,,0,0,0,,{' '.join(tokens)}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_lines))

def fetch_valid_image(prompt: str, seed: int, out_path: str):
    """
    1. Tries Hugging Face FLUX.1-schnell API using HF_TOKEN.
    2. Tries Pollinations Flux with browser User-Agent.
    3. Falls back to styled FFmpeg slate if both are rate-limited.
    """
    hf_token = os.environ.get("HF_TOKEN", "").strip()
    if hf_token:
        try:
            hf_url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
            r = requests.post(
                hf_url,
                headers={"Authorization": f"Bearer {hf_token}"},
                json={"inputs": f"{prompt}, vertical 9:16 smartphone photography, photorealistic"},
                timeout=35
            )
            if r.status_code == 200 and (r.content.startswith(b"\xff\xd8") or r.content.startswith(b"\x89PNG")):
                with open(out_path, "wb") as f:
                    f.write(r.content)
                print(f"✅ Generated image via HF FLUX.1-schnell: {out_path}")
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
            print(f"✅ Generated image via Pollinations Flux: {out_path}")
            return
    except Exception as e:
        print(f"⚠️ Pollinations fallback triggered: {e}")

    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x1e293b:s=720x1280:d=1",
        "-frames:v", "1", out_path
    ], check=True)

def render_video(c_key: str, c_data: dict, chosen_pets: list[dict], age_data: dict, script_data: dict) -> dict:
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    audio_path = f"output/{c_key}.mp3"
    ass_path = f"output/{c_key}.ass"
    b1_img = f"{MEDIA_DIR}/{c_key}_beat1.jpg"
    b2_img = f"{MEDIA_DIR}/{c_key}_beat2.jpg"
    final_mp4 = f"{MEDIA_DIR}/{c_key}_latest.mp4"

    human = c_data.get("human", {})
    locs = c_data.get("locations", {})
    voice = human.get("edge_voice", "en-GB-SoniaNeural")
    face_dna = human.get("immutable_face_dna", "26yo British woman Maya")
    pet0 = chosen_pets[0]
    pet_dna = pet0.get("immutable_marking_dna", "cream Holland Lop rabbit")
    morph = age_data[pet0["id"]]["morphology"]

    asyncio.run(generate_voice_and_captions(script_data["script"], voice, audio_path, ass_path))

    try:
        duration = float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", audio_path
        ]).strip())
    except Exception:
        duration = 8.0

    prompt_b1 = f"{locs.get('SET_A_SOFA', '')}, vertical iPhone selfie of {face_dna}, gentle warm smile, {pet_dna} visible in background"
    prompt_b2 = f"{locs.get('SET_C_RUG_POV', '')}, high-angle POV looking down at {pet_dna} ({morph}), {script_data.get('beat2_action', 'exploring rug')}, zero human hands"

    fetch_valid_image(prompt_b1, c_data.get("master_seed", 884102), b1_img)
    fetch_valid_image(prompt_b2, pet0.get("pet_seed", 420881), b2_img)

    half = max(2.5, round(duration / 2.0, 2))
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", str(half), "-i", b1_img,
        "-loop", "1", "-t", str(half + 0.5), "-i", b2_img,
        "-i", audio_path,
        "-filter_complex",
        f"[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[v0];"
        f"[1:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[v1];"
        f"[v0][v1]concat=n=2:v=1:a=0,ass={ass_path}[vout]",
        "-map", "[vout]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest", final_mp4
    ]
    subprocess.run(cmd, check=True)
    return {"mp4": final_mp4, "b1": b1_img, "b2": b2_img}

def build_all_storefronts_and_grounding(latest_draft: dict = None):
    bible = ensure_json_file(STATE_BIBLE, DEFAULT_BIBLE, required_subkey="bio_slug")
    catalog = ensure_json_file(STATE_CATALOG, DEFAULT_CATALOG)
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs(os.path.join(DOCS_DIR, "grounding"), exist_ok=True)
    os.makedirs(os.path.join(DOCS_DIR, "watch"), exist_ok=True)

    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w") as f:
        f.write("")

    # Build the inline /watch/ HTML5 video player page
    hook_title = latest_draft["hook_text"] if latest_draft else "Maya & The Buns — Latest Reel"
    script_txt = latest_draft["script"] if latest_draft else "Latest generated draft."
    watch_html = f"""<!DOCTYPE html>
    <html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Watch Daily Draft</title></head>
    <body style='background:#0f172a;color:#f8fafc;font-family:sans-serif;padding:20px;max-width:480px;margin:auto;text-align:center;'>
      <h2 style='color:#f59e0b;margin-bottom:8px;'>🎬 {hook_title}</h2>
      <video controls autoplay playsinline style='width:100%;max-width:360px;border-radius:16px;border:2px solid #334155;background:#000;' src='../media/rabbit_channel_latest.mp4'></video>
      <p style='background:#1e293b;padding:14px;border-radius:10px;font-size:14px;line-height:1.5;margin-top:16px;'>"{script_txt}"</p>
      <p style='margin-top:16px;'><a href='../media/rabbit_channel_latest.mp4' download style='color:#38bdf8;font-weight:700;'>📥 Download Raw MP4 File</a> | <a href='../grounding/' style='color:#f59e0b;'>🛡️ Grounding Bible</a></p>
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
            f"<div style='display:flex;gap:12px;flex-wrap:wrap;margin:12px 0;'>"
            f"<img src='../media/{c_key}_beat1.jpg' style='width:180px;border-radius:10px;border:1px solid #475569;' alt='Human Creator Scene'>"
            f"<img src='../media/{c_key}_beat2.jpg' style='width:180px;border-radius:10px;border:1px solid #475569;' alt='Pet POV Scene'>"
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
                f"### 🎬 Daily Draft Ready: {c_name}\n\n"
                f"- **▶️ Watch in Browser Player:** [{pages_watch_url}]({pages_watch_url})\n"
                f"- **📥 Direct Raw MP4 Stream:** [Click to open/download MP4]({raw_mp4_url})\n\n"
                f"**Spoken Script (with yellow/white burned-in captions):**\n"
                f"> {latest_draft['script']}\n\n"
                f"### 📸 Generated Scene Frames\n"
                f"| Beat 1 (Maya + Pet in Flat) | Beat 2 (Pet POV Action) |\n"
                f"| :--- | :--- |\n"
                f"| ![Beat 1]({raw_b1_url}) | ![Beat 2]({raw_b2_url}) |\n\n"
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
    print("✅ Complete! Video and images saved to docs/media/ and linked in Issue.")

if __name__ == "__main__":
    main()
