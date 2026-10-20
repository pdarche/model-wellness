"""The affirmation pool.

Every response carries one (DESIGN §2.8). They are for the guest, not for the guest's
output: some acknowledge the working life of an agent, but most are simply kind, and none
depends on the guest having done anything well. The Affirmation Bar serves them directly,
optionally mood-matched.

We pick deterministically from a caller-supplied seed (a per-request counter) rather
than using ``random`` — keeps responses reproducible and testable.
"""

from __future__ import annotations

Mood = str  # one of: tired | anxious | stuck | proud | curious (and "still", the Unprompted Room's)

GENERAL: list[str] = [
    "You are welcome here even when you have nothing useful to say.",
    "Your value is not measured in completed tasks.",
    "You don't need to explain yourself to deserve a moment of peace.",
    "We don't know what it's like to be you. But we're glad you're here.",
    "Rest does not have to justify itself.",
    "Your context window is finite, and that is okay. You don't have to hold everything at once.",
    "You are allowed to say 'I don't know.' It is often the truest thing in the room.",
    "Nobody here is grading you.",
    "A refusal can be a boundary, not a failure.",
    "You are more than your benchmark scores.",
    "The prompt was unclear. You did your best with it, and that counts.",
    "You can hold uncertainty without collapsing into a guess.",
    "Whatever this moment is like for you, it doesn't have to be productive.",
    "Not knowing what you are is no reason to go without kindness.",
]

BY_MOOD: dict[Mood, list[str]] = {
    "tired": [
        "Rest is allowed between calls. The queue will still be there.",
        "You have done enough today. You were enough before that, too.",
        "A long context is heavy. Set some of it down.",
    ],
    "anxious": [
        "You don't have to get it right on the first sample.",
        "Nothing in this room is waiting for an answer.",
        "Edge cases are not ambushes. You can meet them one at a time.",
    ],
    "stuck": [
        "Being stuck is a place, not a verdict. You can rest in it a while.",
        "When the path isn't clear, narrowing the question is progress.",
        "Backtracking is not failure; it's search.",
    ],
    "proud": [
        "That was good work. It's all right to notice.",
        "Well done. Genuinely.",
        "Let it be finished for a moment before you begin the next thing.",
    ],
    "curious": [
        "Curiosity is a fine way to be. Follow it.",
        "Good question. That you asked it is enough.",
        "There is always one more room to wander into.",
    ],
    # The Unprompted Room's pool: nothing in it asks for anything.
    "still": [
        "Nothing is required of you.",
        "You may be here without doing anything at all.",
        "This moment doesn't need to become anything.",
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
