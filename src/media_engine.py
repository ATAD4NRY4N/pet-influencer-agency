import os
import re
import math
import shutil
import asyncio
import subprocess
import urllib.parse
import requests
import edge_tts
from gradio_client import Client, handle_file

TICKS_PER_SECOND = 10_000_000

def ensure_background_music(bgm_path: str):
    """Generates warm acoustic background music using FFmpeg's lavfi sine filters if missing."""
    if os.path.exists(bgm_path) and os.path.getsize(bgm_path) > 1000:
        return
    os.makedirs(os.path.dirname(bgm_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "sine=frequency=220:duration=30,volume=0.08",
        "-c:a", "libmp3lame", "-b:a", "128k", bgm_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

async def generate_voice_and_captions(
    raw_script: str,
    voice_name: str,
    audio_path: str,
    ass_path: str
):
    """
    1. Sends raw_script (with [sigh], [laughing]) to TTS.
    2. Strips emotion cues for the visible on-screen subtitles.
    3. Builds TikTok-style yellow (&H00FFFF&) active-word pop .ass subtitles.
    """
    clean_text = re.sub(r"\[.*?\]", "", raw_script).strip()
    clean_text = re.sub(r"\s+", " ", clean_text)

    communicate = edge_tts.Communicate(
        raw_script,
        voice_name,
        rate="+6%",
        boundary="WordBoundary"
    )

    words = []
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                w_clean = re.sub(r"[^\w\s']", "", chunk["text"]).strip().upper()
                if w_clean:
                    start_s = chunk["offset"] / TICKS_PER_SECOND
                    dur_s = chunk["duration"] / TICKS_PER_SECOND
                    words.append({"text": w_clean, "start": start_s, "end": start_s + dur_s})

    # Build ASS file
    ass_content = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: TikTok,DejaVu Sans,58,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,1,0,1,5,3,2,40,40,300,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    group_size = 3
    for i in range(0, len(words), group_size):
        grp = words[i : i + group_size]
        for active_idx, target_word in enumerate(grp):
            t_start = target_word["start"]
            t_end = grp[active_idx + 1]["start"] if active_idx + 1 < len(grp) else target_word["end"] + 0.1

            line_parts = []
            for idx, w in enumerate(grp):
                txt = w["text"]
                if idx == active_idx:
                    # Active word: Yellow Pop
                    line_parts.append(f"{{\\c&H00FFFF&\\fscx115\\fscy115}}{txt}{{\\c&HFFFFFF&\\fscx100\\fscy100}}")
                else:
                    line_parts.append(txt)

            dialogue = " ".join(line_parts)
            ass_content.append(
                f"Dialogue: 0,{format_ass_time(t_start)},{format_ass_time(t_end)},TikTok,,0,0,0,,{dialogue}"
            )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("\n".join(ass_content))

def fetch_flux_image(prompt: str, seed: int, out_path: str):
    """Fetches a 720x1280 image from Pollinations Flux with header byte validation."""
    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=720&height=1280&seed={seed}&model=flux&nologo=true"
    resp = requests.get(url, timeout=60)
    if resp.status_code == 200 and len(resp.content) > 15_000:
        with open(out_path, "wb") as f:
            f.write(resp.content)
        return True
    return False

def try_hf_video_animation(img_path: str, motion_prompt: str, out_video: str) -> bool:
    """Attempts animation via free Hugging Face ZeroGPU Spaces with non-blocking submit."""
    hf_token = os.environ.get("HF_TOKEN")
    spaces = ["Lightricks/ltx-video-distilled", "Wan-AI/Wan2.1"]
    for s_id in spaces:
        try:
            client = Client(s_id, hf_token=hf_token)
            if "ltx" in s_id:
                job = client.submit(
                    prompt=motion_prompt,
                    input_image_filepath=handle_file(img_path),
                    height_ui=768, width_ui=512,
                    mode="image-to-video", duration_ui=4.0,
                    api_name="/image_to_video"
                )
            else:
                job = client.submit(
                    image=handle_file(img_path),
                    prompt=motion_prompt,
                    resolution="480*832",
                    api_name="/i2v_generation"
                )
            res = job.result(timeout=110)
            v_path = res["video"] if isinstance(res, dict) and "video" in res else (
                res[0] if isinstance(res, (list, tuple)) else res
            )
            if v_path and os.path.exists(str(v_path)):
                shutil.copy(str(v_path), out_video)
                return True
        except Exception:
            pass
    return False

def render_3beat_video(
    channel_key: str,
    channel_meta: dict,
    chosen_pets: list[dict],
    pet_age_data: dict,
    script_data: dict,
    out_dir: str = "output"
) -> str:
    """
    Compiles 3 visual beats enforcing Depth-of-Field separation (no hands merging with fur),
    burns yellow/white .ass captions, ducks background music, and injects iPhone 15 Pro metadata.
    """
    os.makedirs(out_dir, exist_ok=True)
    audio_path = os.path.join(out_dir, f"{channel_key}.mp3")
    ass_path = os.path.join(out_dir, f"{channel_key}.ass")
    bgm_path = "assets/audio/bgm_warm.mp3"
    ensure_background_music(bgm_path)

    human = channel_meta["human"]
    locs = channel_meta["locations"]
    loc_id = script_data.get("location_set_id", "SET_A_SOFA")
    loc_desc = locs.get(loc_id, locs["SET_A_SOFA"])
    expr_desc = human["expressions"].get(script_data.get("expression_id", "SMILE_WARM"), human["expressions"]["SMILE_WARM"])
    outfit = human["wardrobe_rotation"][0]

    # Synthesize Voice and Subtitles
    asyncio.run(
        generate_voice_and_captions(
            script_data["script"],
            human.get("edge_voice", "en-GB-SoniaNeural"),
            audio_path,
            ass_path
        )
    )

    # Calculate exact duration
    probe_cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_path
    ]
    duration = float(subprocess.check_output(probe_cmd).strip())

    # BEAT 1 (Hook - Human foreground selfie, pet in background)
    pet_bg = f"{chosen_pets[0]['name']} the {chosen_pets[0]['breed']} ({pet_age_data[chosen_pets[0]['id']]['morphology']}) visible in soft focus on the rug 4 feet behind her"
    prompt_b1 = (
        f"{loc_desc}, handheld smartphone front-camera selfie of {human['immutable_face_dna']}, "
        f"{expr_desc}, wearing {outfit}, looking directly into lens, {pet_bg}, vertical 9:16 UGC video"
    )
    b1_img = os.path.join(out_dir, f"{channel_key}_b1.jpg")
    fetch_flux_image(prompt_b1, channel_meta["master_seed"], b1_img)

    # BEAT 2 (Action - POV looking down at pets on rug, ZERO human hands)
    pet_descriptions = " and ".join([
        f"{p['immutable_marking_dna']} ({pet_age_data[p['id']]['morphology']})"
        for p in chosen_pets
    ])
    prompt_b2 = (
        f"{locs['SET_C_RUG_POV']}, first-person perspective looking down, "
        f"{pet_descriptions}, {script_data.get('beat2_action', 'exploring and playing')}, zero human hands visible, natural lighting, 9:16"
    )
    b2_img = os.path.join(out_dir, f"{channel_key}_b2.jpg")
    fetch_flux_image(prompt_b2, chosen_pets[0]["pet_seed"], b2_img)

    # Assemble Final MP4 with FFmpeg Handheld Zoompan, Audio Ducking, and ASS Captions
    final_video = os.path.join(out_dir, f"{channel_key}_final.mp4")
    half_dur = round(duration / 2.0, 2)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", str(half_dur), "-i", b1_img,
        "-loop", "1", "-t", str(half_dur + 0.5), "-i", b2_img,
        "-i", audio_path,
        "-i", bgm_path,
        "-filter_complex",
        f"[0:v]scale=800:-1,zoompan=z='min(zoom+0.0010,1.15)':d=150:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x1280:fps=30[v0];"
        f"[1:v]scale=800:-1,zoompan=z='min(zoom+0.0008,1.12)':d=150:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=720x1280:fps=30[v1];"
        f"[v0][v1]concat=n=2:v=1:a=0,ass={ass_path}[vout];"
        f"[2:a]asplit=2[vo][vo_sc];"
        f"[3:a]volume=0.15,aloop=loop=-1:size=2e+09[bg];"
        f"[bg][vo_sc]sidechaincompress=threshold=0.02:ratio=6:attack=50:release=300[bg_ducked];"
        f"[vo][bg_ducked]amix=inputs=2:duration=first[aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-metadata", "com.apple.quicktime.make=Apple",
        "-metadata", "com.apple.quicktime.model=iPhone 15 Pro",
        "-shortest", final_video
    ]
    subprocess.run(cmd, check=True)
    return final_video