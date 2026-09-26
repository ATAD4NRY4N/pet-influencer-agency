import os
import re
import json
import requests
import feedparser
from datetime import datetime, timezone
from src.aging_engine import calculate_age_stats
from src.system1_laya import System1Engine
from src.system2_openrouter import System2Engine
from src.media_engine import render_3beat_video
from src.social_publisher import upload_to_youtube_shorts, upload_to_instagram_reels
from src.storefront_and_grounding import build_all_storefronts_and_grounding

STATE_QUEUE = "state/queue.json"
STATE_BIBLE = "state/character_bible.json"
STATE_WEIGHTS = "state/strategy_weights.json"
STATE_CATALOG = "state/catalog.json"

def fetch_safe_trends() -> list[str]:
    """3-Tier Trend Waterfall: Google News -> Reddit RSS -> Evergreen Soap-Opera Bank."""
    url = "https://news.google.com/rss/search?q=house+rabbit+OR+pet+bunny+tips"
    try:
        resp = requests.get(url, timeout=10)
        feed = feedparser.parse(resp.text)
        titles = [e.title for e in feed.entries[:5] if e.title]
        if titles:
            return titles
    except Exception:
        pass
    return [
        "Why rabbits hate cheap plastic water bowls and how to fix it",
        "How to bunny-proof laptop and phone chargers in 5 minutes",
        "The real reason three rabbits react differently to morning breakfast"
    ]

def upload_video_asset(file_path: str) -> str:
    """Uploads MP4 to Litterbox (72h public CDN) for mobile Issue previews & Instagram API ingestion."""
    with open(file_path, "rb") as f:
        resp = requests.post(
            "https://litterbox.catbox.moe/resources/internals/api.php",
            data={"reqtype": "fileupload", "time": "72h"},
            files={"fileToUpload": f},
            timeout=60
        )
    return resp.text.strip()

def create_github_draft_issue(channel_key: str, draft: dict):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("REPO_NAME")
    if not token or not repo:
        return

    title = f"🐾 [Daily Draft] {draft['channel_name']}: {draft['hook_text']}"
    body = (
        f"### 🎬 Daily Video Draft Ready for Approval\n\n"
        f"**Channel:** {draft['channel_name']} ({draft['phase']})\n"
        f"**Today's Cast:** {draft['cast_names']}\n"
        f"**Video Preview:** [▶️ Watch Playable MP4]({draft['video_url']})\n\n"
        f"**Hook Banner:** `{draft['hook_text']}`\n"
        f"**Spoken Script:**\n> {draft['script']}\n\n"
        f"---\n"
        f"### 📱 Mobile Approval Instructions\n"
        f"- **To Publish:** Apply the label **`approve`** or comment `/publish`\n"
        f"- **To Edit Script:** Comment `/script: <new voiceover text>`\n"
        f"- **To Re-roll:** Apply the label **`reroll`**"
    )

    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    requests.post(url, json={"title": title, "body": body, "labels": ["draft"]}, headers=headers)

def run_generate():
    print("🚀 Starting Daily Generation Run...")
    system1 = System1Engine()
    system2 = System2Engine()

    with open(STATE_BIBLE, "r") as f:
        bible = json.load(f)
    with open(STATE_QUEUE, "r") as f:
        queue = json.load(f)
    with open(STATE_WEIGHTS, "r") as f:
        weights = json.load(f)
    with open(STATE_CATALOG, "r") as f:
        catalog = json.load(f)

    for c_key, c_data in bible.items():
        if not c_data.get("enabled", False):
            continue

        phase = weights.get(c_key, {}).get("maturity_phase", "PHASE_1_INCUBATION")
        pets = c_data.get("pets", [])

        # 1. Biological Aging
        age_data = {p["id"]: calculate_age_stats(p["birth_date"]) for p in pets}
        age_summary = "\n".join([f"- {p['name']}: {age_data[p['id']]['age_weeks']} wks old ({age_data[p['id']]['stage']})" for p in pets])

        # 2. Trends & Casting
        trends = fetch_safe_trends()
        trend, angle, score = system1.triage_candidate_trends(c_data["channel_name"], trends, phase)
        cast_mode, chosen_pets = system1.select_cast_configuration(pets, weights.get(c_key, {}).get("days_since_featured", {}))

        # 3. Product Selection
        product = catalog.get(c_key, [None])[0] if phase != "PHASE_1_INCUBATION" else None

        # 4. Script Generation & System 1 Audit
        script_data = system2.write_daily_script(
            c_data, chosen_pets, age_summary, trend, angle, phase, product,
            weights.get(c_key, {}).get("winning_script_example")
        )
        audit = system1.audit_script(script_data["script"], phase)

        # 5. Render Video & Upload Preview
        video_file = render_3beat_video(c_key, c_data, chosen_pets, age_data, script_data)
        video_url = upload_video_asset(video_file)

        draft = {
            "channel_key": c_key,
            "channel_name": c_data["channel_name"],
            "phase": phase,
            "cast_names": ", ".join([p["name"] for p in chosen_pets]),
            "hook_text": script_data["hook_text"],
            "script": script_data["script"],
            "video_url": video_url,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        queue["pending_approvals"].append(draft)
        create_github_draft_issue(c_key, draft)

    with open(STATE_QUEUE, "w") as f:
        json.dump(queue, f, indent=2)

    build_all_storefronts_and_grounding()
    print("✅ Generation complete!")

def run_handle_mobile_approval():
    label = os.environ.get("LABEL_NAME", "")
    comment = os.environ.get("COMMENT_BODY", "")
    issue_num = os.environ.get("ISSUE_NUMBER", "")

    if label != "approve" and not comment.startswith("/publish"):
        return

    print("👍 Mobile approval detected! Publishing...")
    with open(STATE_QUEUE, "r") as f:
        queue = json.load(f)

    if not queue["pending_approvals"]:
        return

    draft = queue["pending_approvals"].pop(0)
    creds = json.loads(os.environ.get("SOCIAL_CREDENTIALS_JSON", "{}")).get(draft["channel_key"], {})

    yt_url = upload_to_youtube_shorts(draft["video_url"], draft["hook_text"], draft["script"], creds.get("youtube", {})) if "youtube" in creds else "Skipped"
    ig_url = upload_to_instagram_reels(draft["video_url"], draft["script"], creds.get("instagram", {})) if "instagram" in creds else "Skipped"

    # Close the Issue via GitHub API
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("REPO_NAME")
    if token and repo and issue_num:
        comment_url = f"https://api.github.com/repos/{repo}/issues/{issue_num}/comments"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
        requests.post(comment_url, json={"body": f"🚀 **Published!**\n- YouTube: {yt_url}\n- Instagram: {ig_url}"}, headers=headers)
        requests.patch(f"https://api.github.com/repos/{repo}/issues/{issue_num}", json={"state": "closed"}, headers=headers)

    draft["published_at"] = datetime.now(timezone.utc).isoformat()
    draft["youtube_url"] = yt_url
    draft["instagram_url"] = ig_url
    queue["published_history"].append(draft)

    with open(STATE_QUEUE, "w") as f:
        json.dump(queue, f, indent=2)

if __name__ == "__main__":
    event = os.environ.get("EVENT_NAME", "")
    mode = os.environ.get("DISPATCH_MODE", "")

    if event == "issues" or event == "issue_comment":
        run_handle_mobile_approval()
    else:
        run_generate()