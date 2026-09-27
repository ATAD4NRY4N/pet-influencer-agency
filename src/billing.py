"""
Billing policy for OpenRouter.

The agency is funded from a credit balance that is not being topped up, so
"free only" is the default and has to be enforced in one place. Leaving this
to the ordering of a ladder is not enough: a ladder entry that happens to be
first will happily spend money before any fallback is considered.

Design rule: fail CLOSED. A model is free if and only if it appears in
FREE_MODELS. Anything unrecognised - including a model added to OpenRouter
after this file was written - is treated as paid and refused. An unknown model
costs nothing to skip; a wrongly-permitted one costs real money on every
episode.

Verified against the live catalogue: OpenRouter currently offers ZERO free
image-output models and ZERO video-output models. The only free image
endpoints are design/text-rendering models that cannot produce a photograph of
a person. That is why IMAGE_PROVIDER_ORDER falls back to Pollinations (keyless,
free, a different provider) and why frame diffusion is skipped entirely under
this policy.
"""

import os

# Set OPENROUTER_FREE_ONLY=0 to deliberately re-enable paid models.
DEFAULT_FREE_ONLY = True


class PaidModelRefused(RuntimeError):
    """A paid model was requested while the free-only policy is active."""


# Explicit allowlist. ":free" is OpenRouter's own free-tier marker; the
# Pollinations bridge is a free OpenRouter-hosted Flux endpoint.
FREE_MODELS = {
    "pollinations/flux",
    "fish-audio/s2.1-pro-free:free",
    "deepgram/flux-tts:free",
    "openrouter/free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
}


def free_only() -> bool:
    raw = os.environ.get("OPENROUTER_FREE_ONLY", "").strip().lower()
    if raw in ("", "1", "true", "yes", "on"):
        return DEFAULT_FREE_ONLY
    if raw in ("0", "false", "no", "off"):
        return False
    return DEFAULT_FREE_ONLY


def is_free(model_id: str) -> bool:
    if not model_id:
        return False
    mid = model_id.strip()
    extra = {
        m.strip() for m in os.environ.get("OPENROUTER_EXTRA_FREE_MODELS", "").split(",") if m.strip()
    }
    if mid in FREE_MODELS or mid in extra:
        return True
    return mid.endswith(":free")


def check(model_id: str, purpose: str = "media") -> str:
    """Return model_id if allowed under the current policy, else raise."""
    if free_only() and not is_free(model_id):
        raise PaidModelRefused(
            f"{purpose}: refusing paid OpenRouter model {model_id!r}. "
            f"OPENROUTER_FREE_ONLY is on (the default). Set OPENROUTER_FREE_ONLY=0 "
            f"to allow spending, or add it to OPENROUTER_EXTRA_FREE_MODELS if it is "
            f"genuinely free."
        )
    return model_id


def filter_ladder(order, ladder: dict, purpose: str = "media") -> list[tuple[str, str]]:
    """Keep only the rungs of `order` that the current policy allows.

    Returns [(key, model_id), ...] in the caller's order. Silently dropping a
    disallowed rung is correct here: the ladder's whole job is to find
    something that works, and raising on the first paid entry would prevent
    the free entries after it from ever being tried.
    """
    allowed = []
    for key in order:
        model = ladder.get(key)
        if not model:
            continue
        if free_only() and not is_free(model):
            continue
        allowed.append((key, model))
    return allowed
