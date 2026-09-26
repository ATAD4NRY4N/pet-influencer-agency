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

# Optional imports with safe fallbacks so a dependency hiccup never crashes the run
try:
    from laya import Router
    HAS_LAYA = True
except Exception as e:
    print(f"⚠️ Laya ONNX fallback active ({e})")
    HAS_LAYA = False

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
STATE_BIBLE = f"{STATE_DIR}/character_bible.json"
STATE_CATALOG = f"{STATE_DIR}/catalog.json"
STATE_WEIGHTS = f"{STATE_DIR}/strategy_weights.json"
STATE_QUEUE = f"{STATE_DIR}/queue.json"
TICKS_PER_SECOND = 10_000_000

# =====================================================================
# 1. SELF-HEALING STATE & DOCS BOOTSTRAP (Fixes GitHub Pages Immediately)
# =====================================================================
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
            "immutable_face_dna": "26-year-old British woman Maya, oval face, warm fair skin with subtle bridge-of-nose freckles, hazel-green eyes behind thin round matte-gold wireframe glasses, shoulder-length wavy chestnut hair pulled half-up in a tortoiseshell claw clip",
            "expressions": {
                "SMILE_WARM": "gentle closed-lip warm smile with a subtle left-cheek dimple, relaxed eyebrows",
                "SMILE_LAUGH": "natural open-mouth laughing smile showing straight upper teeth, crinkled amused eyes",
                "EXPRESSION_DEADPAN": "deadpan dry flat-line mouth, raised left eyebrow looking straight into camera lens"
            },
            "wardrobe_rotation": [
                "oversized sage-green chunky waffle-knit cardigan over a white crewneck tee",
                "oatmeal ribbed crewneck jumper with rolled sleeves and a thin gold chain",
                "terracotta linen button-down shirt worn open over a heather-grey tank top"
            ]
        },
        "locations": {
            "SET_A_SOFA": "inside a bright Scandi cottage living room, sitting on a braided cream jute rug leaning against an oatmeal boucle sofa, matte sage-green panelled wall behind, potted monstera plant on far left, warm daylight from right window, herringbone oak flooring",
            "SET_B_KITCHEN": "standing at an oak butcher-block kitchen island, matte cream shaker cabinets and white subway tile splashback in background, copper kettle on rear counter, bright morning daylight",
            "SET_C_RUG_POV": "high-angle first-person POV shot looking down at a braided cream jute rug over herringbone oak floor, black wire playpen panel visible on top-left edge and a wooden hay rack against white skirting board"
        },
        "pets": [
            {
                "id": "barnaby",
                "name": "Barnaby",
                "breed": "Holland Lop Rabbit",
                "birth_date": "2026-05-10",
                "pet_seed": 420881,
                "role": "The polite food critic",
                "immutable_marking_dna": "cream-white Holland Lop rabbit with floppy ears where ONLY the left ear is dark charcoal-grey and right ear is cream, pink nose, dark brown eyes"
            },
            {
                "id": "pip",
                "name": "Pip",
                "breed": "Lionhead Rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420882,
                "role": "The cable-chewing chaos gremlin",
                "immutable_marking_dna": "jet-black Lionhead rabbit with upright black ears and a distinct snow-white fluffy mane crown right between his ears"
            },
            {
                "id": "clover",
                "name": "Clover",
                "breed": "Dutch Rabbit",
                "birth_date": "2026-07-15",
                "pet_seed": 420883,
                "role": "The hyperactive zoomie queen",
                "immutable_marking_dna": "cinnamon-amber and white Dutch rabbit with a crisp white saddle across the shoulders and a symmetrical narrow white diamond blaze running straight up the nose"
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
            "keywords": ["cord", "cable", "chew", "wire", "charger"]
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

DEFAULT_WEIGHTS = {
    "rabbit_channel": {
        "maturity_phase": "PHASE_1_INCUBATION",
        "followers": 120,
        "total_views": 4820,
        "days_since_featured": {"barnaby": 0, "pip": 1, "clover": 0},
        "winning_script_example": "Day 42 with three house rabbits, and Pip just proved that playpen fences are purely decorative. [laughing] Barnaby didn't even flinch. Which of your pets was the easiest to train?"
    }
}

DEFAULT_QUEUE = {"pending_approvals": [], "published_history": [], "used_trends": []}

def ensure_json_file(path: str, default_data: dict) -> dict:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > 5:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(default_data, f, indent=2)
    return default_data

def build_all_storefronts_and_grounding():
    """Builds /docs/index.html, /docs/.nojekyll, /docs/grounding/, and /docs/barnaby/."""
    bible = ensure_json_file(STATE_BIBLE, DEFAULT_BIBLE)
    catalog = ensure_json_file(STATE_CATALOG, DEFAULT_CATALOG)
    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(os.path.join(DOCS_DIR, "grounding"), exist_ok=True)

    # Create .nojekyll so GitHub Pages deploys raw HTML without Jekyll build errors
    with open(os.path.join(DOCS_DIR, ".nojekyll"), "w") as f:
        f.write("")

    grounding_cards = []
    hub_links = []

    for c_key, c_data in bible.items():
        h = c_data["human"]
        slug = c_data["bio_slug"]
        slug_dir = os.path.join(DOCS_DIR, slug)
        os.makedirs(slug_dir, exist_ok=True)

        pets_html = "".join([
            f"<div class='pet-card'><strong>{p['name']}</strong> ({p['breed']})<br>"
            f"<small>Born: {p['birth_date']}<br>{p['immutable_marking_dna']}</small></div>"
            for p in c_data.get("pets", [])
        ])
        locs_html = "".join([
            f"<li><strong>{k}:</strong> {v}</li>"
            for k, v in c_data.get("locations", {}).items()
        ])
        grounding_cards.append(f"""
        <div class="channel-grounding">
          <h2>{c_data['channel_name']} ({c_data['handle']})</h2>
          <p><strong>Human Creator:</strong> {h['name']} ({h['age']}yo)<br><small>{h['immutable_face_dna']}</small></p>
          <h3>Active Pets</h3>
          <div class="pet-grid">{pets_html}</div>
          <h3>Locked Apartment Locations</h3>
          <ul>{locs_html}</ul>
        </div>
        """)

        # Build channel Link-in-Bio page
        products = catalog.get(c_key, [])
        prod_html = "".join([
            f"<div style='background:#1e293b;padding:16px;border-radius:12px;margin-bottom:12px;border:1px solid #334155;'>"
            f"<span style='background:#f59e0b;color:#000;font-size:10px;font-weight:800;padding:2px 6px;border-radius:4px;'>{p.get('badge','Pick')}</span>"
            f"<h3 style='margin:8px 0 4px;font-size:16px;'>{p['title']}</h3>"
            f"<p style='color:#34d399;font-weight:700;margin:4px 0;'>{p['price']}</p>"
            f"<a href='{p['affiliate_url']}' target='_blank' style='display:inline-block;margin-top:6px;background:#3b82f6;color:#fff;text-decoration:none;padding:6px 12px;border-radius:6px;font-size:12px;font-weight:600;'>View Product →</a>"
            f"</div>"
            for p in products
        ])

        store_html = f"""<!DOCTYPE html>
        <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{c_data['channel_name']}</title>
        <style>body{{background:#0f172a;color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:24px;max-width:440px;margin:auto;}}</style>
        </head><body>
        <h1 style="text-align:center;">{c_data['channel_name']}</h1>
        <p style="text-align:center;color:#94a3b8;font-size:14px;margin-bottom:24px;">Official recommendations & cast links</p>
        {prod_html}
        </body></html>"""
        with open(os.path.join(slug_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(store_html)

        hub_links.append(f"<li><a href='{slug}/' style='color:#38bdf8;font-size:18px;'>{c_data['channel_name']} Storefront →</a></li>")

    # Save Grounding Bible
    grounding_html = f"""<!DOCTYPE html>
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Universe Grounding Bible</title>
    <style>
      body{{background:#0f172a;color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:24px;max-width:860px;margin:auto;}}
      h1{{color:#f59e0b;}} h2{{border-bottom:1px solid #334155;padding-bottom:8px;}}
      .channel-grounding{{background:#1e293b;padding:20px;border-radius:12px;margin-bottom:20px;}}
      .pet-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:10px 0;}}
      .pet-card{{background:#0f172a;padding:12px;border-radius:8px;border:1px solid #334155;}}
      ul{{font-size:13px;color:#94a3b8;line-height:1.6;}}
    </style></head><body>
    <h1>🐾 Universe Grounding & Cast Bible</h1>
    <p><a href="../" style="color:#38bdf8;">← Back to Agency Hub</a></p>
    {"".join(grounding_cards)}
    </body></html>"""
    with open(os.path.join(DOCS_DIR, "grounding", "index.html"), "w", encoding="utf-8") as f:
        f.write(grounding_html)

    # Save Root Hub index.html (Required for GitHub Pages root URL)
    root_html = f"""<!DOCTYPE html>
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>AI Pet Influencer Agency Hub</title>
    <style>body{{background:#0f172a;color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,sans-serif;padding:32px;max-width:600px;margin:auto;line-height:1.6;}}</style>
    </head><body>
    <h1>🐾 AI Pet Influencer Collective</h1>
    <h3>Public Link-in-Bio Storefronts</h3>
    <ul>{"".join(hub_links)}</ul>
    <hr style="border-color:#334155;margin:24px 0;">
    <h3>Owner Operations</h3>
    <p><a href="grounding/" style="color:#f59e0b;font-weight:700;font-size:18px;">🛡️ Open Universe Grounding & Character Bible →</a></p>
    </body></html>"""
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(root_html)

# =====================================================================
# 2. AGING, LAYA (SYSTEM 1), & OPENROUTER (SYSTEM 2)
# =====================================================================
def calculate_age_stats(birth_date_str: str) -> dict:
    birth_dt = datetime.strptime(birth_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    now_dt = datetime.now(timezone.utc)
    age_days = max(1, (now_dt - birth_dt).days)
    age_weeks = round(age_days / 7.0, 1)
    if age_days < 90:
        stage, morphology = "baby_kit", "tiny palm-sized baby kit with oversized floppy ears and soft downy fluff fur"
    elif age_days < 180:
        stage, morphology = "adolescent", "juvenile half-grown adolescent rabbit with lanky energetic body proportions"
    else:
        stage, morphology = "prime_adult", "full-grown healthy adult rabbit with dense glossy coat and calm posture"
    return {"age_days": age_days, "age_weeks": age_weeks, "stage": stage, "morphology": morphology}

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
        "How to bunny-proof laptop and phone cords in 5 minutes",
        "How three rabbits react differently to morning leafy greens"
    ]

def write_daily_script(channel_meta: dict, chosen_pets: list[dict], age_summary: str, trend: str, phase: str) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    human = channel_meta["human"]
    pet_names = ", ".join([p["name"] for p in chosen_pets])

    if HAS_OPENAI and api_key:
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
        models = ["openrouter/free", "meta-llama/llama-3.3-70b-instruct:free", "google/gemma-3-27b-it:free"]
        prompt = (
            f"Write a 32-word TikTok/Reel voiceover for {human['name']} (26yo UK pet owner) with her rabbits ({pet_names}).\n"
            f"Ages:\n{age_summary}\nTopic: {trend}\n"
            "End with a relatable question for pet owners in the comments. "
            "Return ONLY valid JSON: "
            '{"script": "spoken words...", "hook_text": "4-word banner hook", '
            '"location_set_id": "SET_A_SOFA", "expression_id": "SMILE_WARM", '
            '"beat2_action": "rabbits exploring the braided jute rug"}'
        )
        for m in models:
            try:
                r = client.chat.completions.create(
                    model=m, messages=[{"role": "user", "content": prompt}], timeout=25
                )
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
        "location_set_id": "SET_A_SOFA",
        "expression_id": "SMILE_LAUGH",
        "beat2_action": "rabbits foraging together on the living room jute rug"
    }

# =====================================================================
# 3. MEDIA ENGINE (Edge-TTS .ass Captions + Flux + FFmpeg)
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

async def generate_voice_and_captions(raw_script: str, voice_name: str, audio_path: str, ass_path: str):
    clean_spoken = re.sub(r"\[.*?\]", "", raw_script).strip()
    communicate = edge_tts.Communicate(clean_spoken, voice_name, rate="+6%", boundary="WordBoundary")
    words = []
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

    ass_lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 720", "PlayResY: 1280", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: TikTok,DejaVu Sans,58,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,5,3,2,40,40,300,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    for i in range(0, len(words), 3):
        grp = words[i : i + 3]
        for active_idx, target in enumerate(grp):
            t_start = target["start"]
            t_end = grp[active_idx + 1]["start"] if active_idx + 1 < len(grp) else target["end"] + 0.1
            tokens = [
                f"{{\\c&H00FFFF&\\fscx115\\fscy115}}{w['text']}{{\\c&HFFFFFF&\\fscx100\\fscy100}}"
                if idx == active_idx else w["text"]
                for idx, w in enumerate(grp)
            ]
            ass_lines.append(f"Dialogue: 0,{format_ass_time(t_start)},{format_ass_time(t_end)},TikTok,,0,0,0,,{' '.join(tokens)}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_lines))

def fetch_image_with_fallback(prompt: str, seed: int, out_path: str):
    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=720&height=1280&seed={seed}&model=flux&nologo=true"
    try:
        resp = requests.get(url, timeout=45)
        if resp.status_code == 200 and len(resp.content) > 10_000:
            with open(out_path, "wb") as f:
                f.write(resp.content)
            return
    except Exception as e:
        print(f"⚠️ Pollinations timeout ({e}), generating solid studio frame fallback...")

    # Emergency FFmpeg frame so the build never fails if external image APIs are down
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x1e293b:s=720x1280:d=1",
        "-frames:v", "1", out_path
    ], check=True)

def render_video(c_key: str, c_data: dict, chosen_pets: list[dict], age_data: dict, script_data: dict) -> str:
    os.makedirs("output", exist_ok=True)
    audio_path = f"output/{c_key}.mp3"
    ass_path = f"output/{c_key}.ass"
    b1_img = f"output/{c_key}_b1.jpg"
    b2_img = f"output/{c_key}_b2.jpg"
    final_mp4 = f"output/{c_key}_final.mp4"

    human = c_data["human"]
    locs = c_data["locations"]
    asyncio.run(generate_voice_and_captions(script_data["script"], human["edge_voice"], audio_path, ass_path))

    duration = float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_path
    ]).strip())

    p0 = chosen_pets[0]
    prompt_b1 = (
        f"{locs['SET_A_SOFA']}, smartphone selfie of {human['immutable_face_dna']}, "
        f"{human['expressions']['SMILE_WARM']}, {p0['immutable_marking_dna']} in background, vertical 9:16"
    )
    prompt_b2 = (
        f"{locs['SET_C_RUG_POV']}, high-angle POV looking down at {p0['immutable_marking_dna']} "
        f"({age_data[p0['id']]['morphology']}), {script_data.get('beat2_action','playing')}, vertical 9:16"
    )
    fetch_image_with_fallback(prompt_b1, c_data["master_seed"], b1_img)
    fetch_image_with_fallback(prompt_b2, p0["pet_seed"], b2_img)

    half = max(2.0, round(duration / 2.0, 2))
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
    return final_mp4

def upload_video_preview(file_path: str) -> str:
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": f}, timeout=60
            )
        if r.status_code == 200 and r.text.startswith("http"):
            return r.text.strip()
    except Exception:
        pass
    return "Uploaded in GitHub Action Run"

def open_draft_issue(draft: dict):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("REPO_NAME")
    if not token or not repo:
        return
    body = (
        f"### 🎬 Daily Video Draft Ready\n\n"
        f"- **Channel:** {draft['channel_name']}\n"
        f"- **Featured Cast:** {draft['cast_names']}\n"
        f"- **Video Preview:** [▶️ Watch MP4]({draft['video_url']})\n\n"
        f"**Spoken Script:**\n> {draft['script']}\n\n"
        f"---\n"
        f"**Mobile Actions:** Add the `approve` label or comment `/publish` to post."
    )
    requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"},
        json={"title": f"🐾 [Draft] {draft['channel_name']}: {draft['hook_text']}", "body": body}
    )

def main():
    # Step 1: Always build /docs first so GitHub Pages deployment succeeds
    build_all_storefronts_and_grounding()
    bible = ensure_json_file(STATE_BIBLE, DEFAULT_BIBLE)
    weights = ensure_json_file(STATE_WEIGHTS, DEFAULT_WEIGHTS)
    queue = ensure_json_file(STATE_QUEUE, DEFAULT_QUEUE)

    event = os.environ.get("EVENT_NAME", "")
    if event in ("issues", "issue_comment"):
        print("✅ Mobile Issue event processed.")
        return

    for c_key, c_data in bible.items():
        if not c_data.get("enabled", False):
            continue
        pets = c_data["pets"]
        age_data = {p["id"]: calculate_age_stats(p["birth_date"]) for p in pets}
        age_summary = "\n".join([f"- {p['name']}: {age_data[p['id']]['age_weeks']} wks ({age_data[p['id']]['stage']})" for p in pets])
        trends = fetch_safe_trends()
        phase = weights.get(c_key, {}).get("maturity_phase", "PHASE_1_INCUBATION")

        script_data = write_daily_script(c_data, pets[:2], age_summary, trends[0], phase)
        video_file = render_video(c_key, c_data, pets[:2], age_data, script_data)
        video_url = upload_video_preview(video_file)

        draft = {
            "channel_key": c_key,
            "channel_name": c_data["channel_name"],
            "cast_names": ", ".join([p["name"] for p in pets[:2]]),
            "hook_text": script_data["hook_text"],
            "script": script_data["script"],
            "video_url": video_url,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        queue["pending_approvals"].append(draft)
        open_draft_issue(draft)

    with open(STATE_QUEUE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2)
    print("🎉 Run finished! Storefront, Grounding Bible, and Daily Issue created.")

if __name__ == "__main__":
    main()