import hashlib
import requests
import feedparser
from datetime import datetime, date, timezone

EVERGREEN_SOAP_OPERA_BANK = [
    "Why one pet always acts like the polite angel while the younger two plot total chaos during breakfast",
    "The hilarious difference in how all three pets react when they hear the treat bag crinkle from another room",
    "Morning routine reality check: trying to drink a hot cup of tea while three pets demand floor attention",
    "Measuring how fast the babies have grown this week compared to their older sibling",
    "Why pets completely ignore expensive beds just to sleep in the weirdest corner of the living room",
    "Testing who is the smartest sibling with a homemade cardboard foraging puzzle",
    "What happens when you try to clean the playpen while all three pets 'supervise' your broom",
    "The exact moment the 8pm evening zoomies hit the living room rug simultaneously",
    "Ranking all three pets by how dramatic they get when dinner is four minutes late",
    "How our oldest pet teaches naughty habits to the two younger babies every single afternoon"
]

MILESTONE_EPISODES = {
    60: "2-Month Adoption Milestone Special: Weighing in and comparing how tiny they are!",
    90: "12-Week Growth Check: Entering the lanky teenage phase and testing high jumps!",
    180: "6-Month Half-Birthday Celebration: From palm-sized babies to full house rulers!",
    365: "1st Birthday Party Special: One full year of raising this chaotic trio!"
}

def compute_pet_biology(pet: dict, ref_date: date = None) -> dict:
    """Computes exact age in days/weeks and returns morphological prompt descriptors."""
    ref_date = ref_date or datetime.now(timezone.utc).date()
    bday = date.fromisoformat(pet["birth_date"])
    age_days = max(45, (ref_date - bday).days)
    age_weeks = round(age_days / 7.0, 1)
    species = pet.get("species", "Rabbit").lower()

    if age_days <= 90:
        stage = "Baby Kit / Juvenile"
        morphology = (
            f"tiny palm-sized {age_weeks}-week-old baby {species} ({pet['breed']}), "
            f"compact round baby proportions, oversized ears relative to tiny body, fuzzy baby coat"
        )
    elif age_days <= 165:
        stage = "Lanky Teenager"
        morphology = (
            f"adolescent {age_weeks}-week-old juvenile {species} ({pet['breed']}), "
            f"half-grown lanky teenage proportions, energetic posture"
        )
    elif age_days <= 365:
        stage = "Prime Young Adult"
        morphology = (
            f"fully grown prime young adult {species} ({pet['breed']}), "
            f"sleek glossy adult coat, filled-out healthy proportions"
        )
    else:
        stage = "Mature Adult"
        morphology = (
            f"mature full-weight adult {species} ({pet['breed']}), "
            f"thick plush coat, calm confident adult posture"
        )

    # Check if temporary modifier (e.g. recovery onesie) is active
    mod_text = ""
    if pet.get("temp_modifier") and pet.get("temp_modifier_until"):
        try:
            if ref_date <= date.fromisoformat(pet["temp_modifier_until"]):
                mod_text = f", {pet['temp_modifier']}"
        except ValueError:
            pass

    full_visual_dna = f"{morphology}, {pet['immutable_marking_dna']}{mod_text}"
    return {
        "id": pet["id"],
        "name": pet["name"],
        "role": pet["role"],
        "age_days": age_days,
        "age_weeks": age_weeks,
        "stage": stage,
        "milestone_topic": MILESTONE_EPISODES.get(age_days),
        "full_visual_dna": full_visual_dna
    }


def fetch_candidate_trends(channel_cfg: dict, used_hashes: list[str]) -> list[str]:
    """
    3-Tier Trend Waterfall:
      1. Milestone Override (if any pet hit Day 60, 90, 180, 365)
      2. Google News RSS + Reddit RSS
      3. Evergreen Soap-Opera Bank (deduplicated)
    """
    candidates = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PetCreatorBot/2.0"}

    for rss_url in channel_cfg.get("rss_queries", []):
        try:
            resp = requests.get(rss_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:6]:
                    title = entry.get("title", "").split(" - ")[0].strip()
                    h = hashlib.md5(title.lower().encode()).hexdigest()[:10]
                    if title and len(title) > 15 and h not in used_hashes:
                        candidates.append(title)
        except Exception as e:
            print(f"⚠️ RSS Tier warning ({rss_url}): {e}")

    # Pad with unused Evergreen Soap-Opera topics so we always have >= 4 candidates
    for topic in EVERGREEN_SOAP_OPERA_BANK:
        h = hashlib.md5(topic.lower().encode()).hexdigest()[:10]
        if h not in used_hashes and topic not in candidates:
            candidates.append(topic)
        if len(candidates) >= 6:
            break

    return candidates[:6] or [EVERGREEN_SOAP_OPERA_BANK[0]]