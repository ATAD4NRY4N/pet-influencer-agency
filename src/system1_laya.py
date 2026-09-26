from laya import Router

PHASE_CONFIG = {
    "PHASE_1_INCUBATION": {
        "description": "0-999 Followers: Pure organic soap-opera & comment debate bait. ZERO product pitches.",
        "angles": {
            "sibling_chaos": "contrasting how the pets react differently to a relatable household moment",
            "growth_diary": "documenting how fast the baby pets are growing and learning naughty habits",
            "owner_debate": "asking viewers a playful, polarizing pet-parent question to spark comments"
        },
        "allow_product": False
    },
    "PHASE_2_TRUST_AND_DM": {
        "description": "1k-10k Followers: Story-first with subtle Problem->Solution keyword comment trigger.",
        "angles": {
            "pet_crisis_solution": "a pet causes a chaotic mess or chew problem that a clever item solved",
            "sibling_chaos": "relatable multi-pet household drama",
            "care_hack": "surprising daily hack learned from raising 3 pets together"
        },
        "allow_product": True
    },
    "PHASE_3_FULL_MONETIZATION": {
        "description": "10k+ Followers: High-converting 3-pet product testing and storefront directives.",
        "angles": {
            "three_pet_product_test": "putting a pet product to the test against all 3 pets",
            "pet_crisis_solution": "solving a destructive pet habit with a tested product",
            "sibling_chaos": "viral multi-pet comparison"
        },
        "allow_product": True
    }
}

class System1Driver:
    def __init__(self):
        print("🧠 Initializing Laya (System 1) 421M ONNX Decision Engine...")
        self.router = Router()

    def select_cast_and_trend(
        self,
        channel_cfg: dict,
        pet_biologies: list[dict],
        candidate_trends: list[str],
        maturity_phase: str
    ) -> dict:
        """
        1. Checks if any pet hit a biological milestone day.
        2. Prevents pet starvation (prioritizes pets with highest days_since_featured).
        3. Uses Laya choice/score/noul to pick the winning trend, angle, and camera set.
        """
        phase_rules = PHASE_CONFIG.get(maturity_phase, PHASE_CONFIG["PHASE_1_INCUBATION"])

        # Sort pets so the most neglected pet is guaranteed lead star today
        pets_by_neglect = sorted(
            zip(channel_cfg["pets"], pet_biologies),
            key=lambda pair: pair[0].get("days_since_featured", 0),
            reverse=True
        )
        lead_pet_raw, lead_pet_bio = pets_by_neglect[0]
        second_pet_raw, second_pet_bio = pets_by_neglect[1]

        # Check for milestone override
        milestone_trend = next((b["milestone_topic"] for b in pet_biologies if b["milestone_topic"]), None)
        if milestone_trend:
            candidate_trends = [milestone_trend]

        best_choice = None
        best_score = -1.0

        for trend in candidate_trends:
            state_str = (
                f"Creator: {channel_cfg['human']['name']}\n"
                f"Pets: {', '.join(b['name'] + ' (' + b['role'] + ', ' + b['stage'] + ')' for b in pet_biologies)}\n"
                f"Channel Phase: {phase_rules['description']}\n"
                f"Candidate Topic: {trend}"
            )
            questions = {
                "angle": {
                    "type": "choice",
                    "instructions": "Which short-form story angle fits this topic and phase best?",
                    "criteria": phase_rules["angles"]
                },
                "cast_mode": {
                    "type": "choice",
                    "instructions": "How many pets should appear in this episode?",
                    "criteria": {
                        "solo_spotlight": "Focus deeply on 1 lead pet with the human creator",
                        "duo_comparison": "Contrast 2 sibling pets side-by-side",
                        "full_trio": "Show all 3 pets together in a routine"
                    }
                },
                "hook_set": {
                    "type": "choice",
                    "instructions": "Which apartment location fits the opening hook best?",
                    "criteria": {
                        "SET_A_SOFA": "Cozy living room sofa setting",
                        "SET_B_KITCHEN": "Kitchen counter / feeding preparation area"
                    }
                },
                "viral_score": {
                    "type": "score",
                    "instructions": "Rate viewer retention and comment potential for pet lovers:",
                    "criteria": ["low", "solid", "viral"]
                },
                "pet_safe": {
                    "type": "noul",
                    "instructions": "Is this scenario completely safe, ethical, and positive for animal welfare?"
                }
            }

            ans = self.router.predict(state_str, questions)["answers"]
            safety = ans["pet_safe"]["noul"]
            v_score = ans["viral_score"]["score"]
            composite = (v_score * 0.7) + (safety * 0.3)

            if safety >= 0.70 and composite > best_score:
                best_score = composite
                cast_mode = ans["cast_mode"]["choice"]
                if cast_mode == "solo_spotlight":
                    active_pets = [lead_pet_bio]
                elif cast_mode == "duo_comparison":
                    active_pets = [lead_pet_bio, second_pet_bio]
                else:
                    active_pets = pet_biologies

                best_choice = {
                    "trend": trend,
                    "angle": ans["angle"]["choice"],
                    "cast_mode": cast_mode,
                    "hook_set": ans["hook_set"]["choice"],
                    "active_pets": active_pets,
                    "viral_score": v_score,
                    "safety_prob": round(safety, 2),
                    "include_product": phase_rules["allow_product"]
                }

        return best_choice

    def audit_script(self, script_text: str, maturity_phase: str) -> dict:
        """Audits System 2's script in ~35ms on CPU before expensive video synthesis."""
        questions = {
            "natural_human_voice": {
                "type": "noul",
                "instructions": "Does this sound like a real human pet owner talking naturally about their pets?"
            },
            "loop_ending": {
                "type": "noul",
                "instructions": "Does the script build curiosity and end smoothly so it loops back to the start?"
            },
            "hook_punch": {
                "type": "score",
                "instructions": "How scroll-stopping is the opening line?",
                "criteria": ["weak", "good", "scroll_stopping"]
            }
        }
        ans = self.router.predict(script_text, questions)["answers"]
        passed = (
            ans["natural_human_voice"]["noul"] >= 0.65
            and ans["hook_punch"]["score"] >= 1
        )
        return {
            "passed": passed,
            "human_pov_prob": round(ans["natural_human_voice"]["noul"], 2),
            "loop_prob": round(ans["loop_ending"]["noul"], 2),
            "hook_score": ans["hook_punch"]["score"]
        }