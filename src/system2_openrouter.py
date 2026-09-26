import os
import re
import json
from openai import OpenAI

FREE_MODEL_WATERFALL = [
    "openrouter/free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
    "qwen/qwen-2.5-72b-instruct:free"
]

def generate_episode_plan(
    channel_cfg: dict,
    s1_decision: dict,
    maturity_phase: str,
    episode_num: int,
    matched_product: dict = None,
    best_example: str = "",
    feedback_note: str = ""
) -> dict:
    """
    Uses OpenRouter Free models to write the 32-40 word loop-bridged script
    with Fish Audio S2.1 emotion tags and strict expression enum keys.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    human = channel_cfg["human"]
    active_pets = s1_decision["active_pets"]
    pets_desc = "; ".join(
        f"{p['name']} ({p['age_weeks']} weeks old, {p['stage']}, {p['role']})"
        for p in active_pets
    )

    if maturity_phase == "PHASE_1_INCUBATION" or not matched_product:
        cta_instruction = (
            "PHASE 1 RULE (ZERO SELLING): Do NOT mention any product or link in bio. "
            "At second 8, include a playful question asking viewers to vote in the comments or follow the daily growth diary."
        )
    elif maturity_phase == "PHASE_2_TRUST_AND_DM":
        kw = matched_product.get("comment_keyword", "LINK")
        cta_instruction = (
            f"PHASE 2 RULE (TROJAN HORSE): Mention how '{matched_product['title']}' solved today's pet problem, "
            f"and tell viewers: 'Comment {kw} and I will send you the exact one we use.'"
        )
    else:
        cta_instruction = (
            f"PHASE 3 RULE: Feature '{matched_product['title']}' naturally and tell viewers it is pinned at the top of the bio link."
        )

    valid_expressions = list(human["expressions"].keys())

    prompt = f"""You are writing Episode {episode_num} for {human['name']} ({human['age']}yo British creator) and her pets: {pets_desc}.
Topic: {s1_decision['trend']}
Story Angle: {s1_decision['angle']}
{cta_instruction}
{f'Audit Correction Required: {feedback_note}' if feedback_note else ''}

CRITICAL RULES FOR 115% RETENTION & FISH AUDIO S2.1 TTS:
1. Total spoken length MUST be between 30 and 42 words.
2. Include 1 or 2 Fish Audio inline emotion tags in brackets like [sigh], [laughing], or [excited].
3. INFINITE LOOP BRIDGE: The final words of the script MUST trail off mid-clause (e.g., "...and honestly that is the exact reason why") so it grammatically flows directly into the VERY FIRST word of the script when the video loops!
4. Example pacing: "{best_example}"
5. You MUST NOT invent human faces or rooms. Pick expressions ONLY from: {valid_expressions}

Return ONLY valid JSON with these exact keys:
{{
  "script": "The 30-42 word script with [sigh]/[laughing] tags and seamless loop bridge",
  "hook_banner": "4-to-6 word punchy text pill for top of screen",
  "beat1_expression": "{valid_expressions[0]}",
  "beat2_pet_action": "8-word action of the pet(s) on the floor rug (NO human hands touching fur)",
  "beat3_expression": "{valid_expressions[-1]}"
}}"""

    for model_id in FREE_MODEL_WATERFALL:
        try:
            resp = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                timeout=35
            )
            content = resp.choices[0].message.content or ""
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                if "script" in data and len(data["script"].split()) >= 15:
                    if data.get("beat1_expression") not in valid_expressions:
                        data["beat1_expression"] = valid_expressions[0]
                    if data.get("beat3_expression") not in valid_expressions:
                        data["beat3_expression"] = valid_expressions[0]
                    return data
        except Exception as e:
            print(f"⚠️ OpenRouter model {model_id} fallback triggered: {e}")

    # Deterministic emergency fallback if all cloud LLMs are down
    pet_name = active_pets[0]["name"]
    return {
        "script": (
            f"living with three {active_pets[0]['stage'].lower()}s means zero peace. "
            f"[sigh] {pet_name} just discovered a brand new way to cause chaos on the rug—Tell me in the comments if yours does this too, because seeing that face is why"
        ),
        "hook_banner": f"DAY {episode_num} WITH 3 PETS",
        "beat1_expression": valid_expressions[0],
        "beat2_pet_action": f"curiously exploring the braided jute rug and sniffing around",
        "beat3_expression": valid_expressions[1] if len(valid_expressions) > 1 else valid_expressions[0]
    }