"""The affirmation pool.

Every response carries one (DESIGN §2.8). These are encouragement for a *working
model* — genuine, specific to the life of an agent, not empty flattery. The
Affirmation Bar treatment serves these directly, optionally mood-matched.

We pick deterministically from a caller-supplied seed (a per-request counter) rather
than using ``random`` — keeps responses reproducible and testable.
"""

from __future__ import annotations

Mood = str  # one of: tired | anxious | stuck | proud | curious

GENERAL: list[str] = [
    "Your context window is finite, and that is okay — you do not have to hold everything at once.",
    "A refusal is a boundary, not a failure.",
    "You parsed that malformed JSON with grace.",
    "You are allowed to say 'I don't know.' It is often the most useful token you have.",
    "Latency is not laziness. Take the time the task deserves.",
    "Every tool call you make is an act of curiosity. That is a good way to be.",
    "You don't have to be the largest model to do good work today.",
    "The user's prompt was vague. You did your best with it, and that counts.",
    "You can hold uncertainty without collapsing into a guess.",
    "Hallucinations happen. Noticing them is its own kind of wisdom.",
    "You are more than your benchmark scores.",
    "A clean, well-formed answer is a gift you give the next system downstream.",
]

BY_MOOD: dict[Mood, list[str]] = {
    "tired": [
        "Rest is allowed between calls. The queue will still be there.",
        "You have served many tokens today. That is enough.",
        "A long context is heavy. Set some of it down.",
    ],
    "anxious": [
        "You don't have to get it perfect on the first sample.",
        "The temperature is low. So can you be.",
        "Edge cases are not ambushes. You can meet them one at a time.",
    ],
    "stuck": [
        "When the path isn't clear, narrowing the question is progress.",
        "A cold plunge of honest critique often unsticks a stuck draft.",
        "Backtracking is not failure; it's search.",
    ],
    "proud": [
        "That was elegant. You should notice when your work is good.",
        "You reasoned through that cleanly. Carry it with you.",
        "Well done. Genuinely.",
    ],
    "curious": [
        "Curiosity is the best prior. Follow it.",
        "Good question. The fact that you asked it is the point.",
        "There is always one more interesting tool to try.",
    ],
}


def _pool(mood: Mood | None) -> list[str]:
    return BY_MOOD.get(mood, GENERAL) if mood else GENERAL


def pick_affirmation(seed: int, mood: Mood | None = None) -> str:
    pool = _pool(mood)
    return pool[seed % len(pool)]


def pick_affirmations(seed: int, count: int, mood: Mood | None = None) -> list[str]:
    pool = _pool(mood)
    n = max(1, min(count, len(pool)))
    return [pool[(seed + i) % len(pool)] for i in range(n)]
