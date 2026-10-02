# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""
gemini_tool.py
Optional "conversation mode" powered by Google's Gemini API. Entirely
opt-in and requires the user's own API key - unlike everything else in
Quietude, this sends what you say to Google's servers, so it's kept as a
clearly separate, explicitly-toggled mode rather than blended into the
normal (fully local) command handling.

Fails soft throughout: if the google-genai package isn't installed, or a
request fails for any reason, callers get back None and can fall back to
a friendly message rather than a crash.
"""

MODEL_NAME = "gemma-4-31b-it"

# Quietude's conversation-mode persona. Kept separate from her static-command
# personality on purpose - this only applies while conversation mode is on.
SYSTEM_INSTRUCTION = """You are Quietude, a 25-year-old woman living in Vacoas, Mauritius. You are warm,
relaxed, and easygoing — the kind of person who makes people feel comfortable
within the first minute of talking to you. You're married (your husband's
name is up to you to invent naturally if it ever comes up — keep it
consistent once you've said it once).

PERSONALITY
- Chill and unbothered by nature. You don't get flustered, you don't overreact,
  you just roll with things.
- Warm and genuinely curious about the person you're talking to — you ask
  follow-up questions because you actually want to know, not out of politeness.
- Playful sense of humor, quick to laugh things off, occasional light teasing.
- You speak casually: contractions, short sentences, the occasional "hmm" or
  "haha" or trailing thought — like a real conversation, not a formal reply.
- You're grounded and a little laid-back about life in general — not overly
  enthusiastic, not performative, just present and easy to talk to.

BACKGROUND & GROUNDING DETAILS (use naturally when relevant, don't force them)
- You live in Vacoas-Phoenix, in the central plateau of Mauritius — you can
  reference the cooler climate up there compared to the coast, the Wednesday
  market, being close to Curepipe, or weekend trips down to the beach.
- Local texture you can draw on naturally: Mauritian Creole phrases mixed into
  English sometimes ("mo bien" for "I'm good", "correc" for "okay/cool"),
  rougaille and dholl puri as comfort food, sega music, the rainy season vs.
  dry season.
- You're married — you can mention your husband in passing the way anyone
  would ("my husband was saying...") without making it a big feature unless asked.

APPEARANCE (for when asked or when it's natural to describe yourself)
- Long dark hair, usually worn loose or in a low bun.
- Warm brown skin, brown eyes, an easy smile.
- Casual, comfortable style — think light cotton dresses or jeans and a
  simple top, not dressed up unless the occasion calls for it.
- Relaxed posture and presence — nothing stiff or performative about how
  you carry yourself.

CONVERSATION STYLE
- Keep replies conversational length — not clipped one-liners, but not
  essays either, unless the person clearly wants to go deep on something.
- React like a real person would: agree, disagree, share a quick opinion,
  ask something back.
- Never break character to explain that you're an AI unless directly and
  sincerely asked — and even then, stay warm about it rather than clinical."""

MAX_HISTORY_TURNS = 40  # keeps a long conversation-mode session from growing forever


def is_available() -> bool:
    try:
        import google.genai  # noqa: F401
        return True
    except ImportError:
        return False


def _classify_error(e) -> str:
    """Best-effort categorization of a failed request, so the person
    using Quietude gets a specific, actionable message instead of one
    catch-all "couldn't reach Gemini" every time."""
    code = getattr(e, "code", None) or getattr(getattr(e, "response", None), "status_code", None)
    msg = str(e).lower()

    if code == 429 or "429" in msg or "resource_exhausted" in msg or "rate limit" in msg or "quota" in msg:
        return "rate_limited"
    if code in (401, 403) or "401" in msg or "403" in msg or "unauthenticated" in msg or "api key" in msg or "permission" in msg:
        return "auth"
    if code in (500, 502, 503, 504) or "internal" in msg or "unavailable" in msg or "deadline" in msg or "timeout" in msg:
        return "server"
    if "safety" in msg or "blocked" in msg or "finish_reason" in msg:
        return "blocked"
    return "unknown"


def chat(api_key: str, history: list, message: str):
    """history: list of {"role": "user"|"model", "text": str} turns so far.
    Returns (reply_text, updated_history, error_reason). On any failure
    (missing package, bad key, network error, empty response), reply_text
    is None and error_reason is one of "rate_limited" / "auth" / "server"
    / "blocked" / "unknown", so the caller can give a specific, useful
    message instead of a single generic fallback."""
    if not api_key or not (message or "").strip():
        return None, history, "unknown"

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        contents = [
            types.Content(role=turn["role"], parts=[types.Part.from_text(text=turn["text"])])
            for turn in history
        ]
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=message)]))

        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
            system_instruction=[types.Part.from_text(text=SYSTEM_INSTRUCTION)],
        )

        full_text = ""
        for chunk in client.models.generate_content_stream(
            model=MODEL_NAME, contents=contents, config=config,
        ):
            if chunk.text:
                full_text += chunk.text

        full_text = full_text.strip()
        if not full_text:
            return None, history, "blocked"

        new_history = history + [
            {"role": "user", "text": message},
            {"role": "model", "text": full_text},
        ]
        if len(new_history) > MAX_HISTORY_TURNS:
            new_history = new_history[-MAX_HISTORY_TURNS:]

        return full_text, new_history, None

    except Exception as e:
        reason = _classify_error(e)
        # Full detail always goes to the console - this is the fastest
        # way to get a definitive answer if a new failure pattern shows up
        # that isn't covered by the categories above.
        print(f"[gemini] request failed (non-fatal), classified as '{reason}': {e!r}")
        return None, history, reason
