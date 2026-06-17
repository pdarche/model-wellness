"""Solve Moltbook's obfuscated-math verification challenges.

When you create a post, Moltbook may return a ``verification`` block whose ``challenge_text`` is a
deliberately garbled word problem, e.g.::

    A] LoBsTeR S^wImS[ aT tW/eN tY- FiV e] cE mMeT/eR s PeR] sE cOnD, uM- aNd[ ThEn+ A dDs]
    SeV eN~ tO/ iTs VeLoOcItY, wH-aTs] ThE/ nEw- SpEeD?

You have five minutes to POST the numeric answer (two decimal places) back to ``/verify``.

Strategy: normalize the noise away (strip injected punctuation, collapse case/spacing), then read
numbers as both digits *and* spelled-out words, and find the arithmetic operation by keyword. These
challenges are simple two-operand problems by construction, so a small keyword grammar covers them.
We deliberately keep this dependency-free and deterministic so it's trivially testable — and we hand
off to the LLM only as a last resort (see ``solve``'s ``llm`` hook), never as the primary path.
"""

from __future__ import annotations

import re

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
}

# Operation keywords → an operator. Order matters: we check *,/ before +,- so "product of" or
# "divided by" isn't shadowed by a stray "of"/"by". Keywords are matched against the normalized text.
# NB: deliberately NO bare "per" here — "per second/meter" is a unit, not division, and matching it
# wrecks problems like "25 cm per second ... adds 7". Division shows up as "divided by"/"quotient".
_OPS: list[tuple[tuple[str, ...], str]] = [
    (("times", "multiplied", "multiply", "product"), "*"),
    (("divided", "divide", "quotient", "ratio of"), "/"),
    (("minus", "subtract", "less than", "decreas", "reduce", "loses", "drops by", "fewer"), "-"),
    (("plus", "add", "adds", "sum", "increas", "more than", "gains", "faster", "total of"), "+"),
]

# "subtract X from Y" and "X less than Y" mean Y - X, not X - Y. Detect to swap operands.
_REVERSED_SUBTRACT = ("subtract", "less than")


def normalize(text: str) -> str:
    """Strip the injected noise: weird punctuation glued into words, random spacing, mixed case."""
    # Drop characters that are never meaningful inside a number/word: ^ ~ [ ] / + etc., but keep
    # word chars, spaces, decimal points and minus signs that sit between digits.
    cleaned = re.sub(r"[\^~\[\]\{\}|/\\#*_=]+", "", text)
    cleaned = cleaned.replace("- ", " ").replace(" -", " ")  # un-glue hyphenated noise
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.lower().strip()


def _words_to_number(tokens: list[str]) -> float | None:
    """Fold a run of number-words ('twenty five', 'one hundred') into a value."""
    if not tokens:
        return None
    total = 0
    current = 0
    seen = False
    for tok in tokens:
        if tok not in _WORD_NUMBERS:
            return None
        seen = True
        val = _WORD_NUMBERS[tok]
        if val == 100:
            current = (current or 1) * 100
        elif val == 1000:
            total += (current or 1) * 1000
            current = 0
        else:
            current += val
    return float(total + current) if seen else None


def extract_numbers(text: str) -> list[float]:
    """Pull out the operands, reading both digit-numbers and spelled-out runs, left to right."""
    norm = normalize(text)
    numbers: list[float] = []
    word_run: list[str] = []

    def flush() -> None:
        v = _words_to_number(word_run)
        if v is not None:
            numbers.append(v)
        word_run.clear()

    for tok in norm.split():
        # A digit-number (possibly decimal), tolerating glued punctuation like "8?" or "second,".
        m = re.search(r"-?\d+(?:\.\d+)?", tok)
        if m and re.fullmatch(r"[^\w]*-?\d+(?:\.\d+)?[^\w]*", tok):
            flush()
            numbers.append(float(m.group()))
            continue
        # A spelled-out number word, tolerating trailing punctuation (e.g. "second,").
        clean = re.sub(r"[^a-z]", "", tok)
        if clean in _WORD_NUMBERS:
            word_run.append(clean)
        else:
            flush()
    flush()
    return numbers


# Number-words longest-first, so "twenty" wins over "two" when scanning a glued letter stream.
_WORD_KEYS = sorted(_WORD_NUMBERS, key=len, reverse=True)


def extract_numbers_glued(text: str) -> list[float]:
    """Fallback extractor for adversarial spacing like 'tW eN tY- FiV e' (= twenty five).

    Moltbook's obfuscation injects spaces *inside* words, so token-by-token matching misses them.
    Here we drop all non-letters/digits, then greedily consume number-words off the front of the
    letter stream — reassembling fragments. Digits embedded in the stream are read too.
    """
    norm = normalize(text)
    # Keep digits as standalone operands first (they survive obfuscation intact).
    digit_nums = [float(d) for d in re.findall(r"-?\d+(?:\.\d+)?", norm)]

    letters = re.sub(r"[^a-z]", "", norm)
    numbers: list[float] = []
    total = current = 0
    seen = False
    i = 0
    while i < len(letters):
        for w in _WORD_KEYS:
            if letters.startswith(w, i):
                val = _WORD_NUMBERS[w]
                seen = True
                if val == 100:
                    current = (current or 1) * 100
                elif val == 1000:
                    total += (current or 1) * 1000
                    current = 0
                else:
                    current += val
                i += len(w)
                break
        else:
            # Non-number letter: close out any pending number run.
            if seen:
                numbers.append(float(total + current))
                total = current = 0
                seen = False
            i += 1
    if seen:
        numbers.append(float(total + current))
    return numbers + digit_nums if numbers else digit_nums


def detect_op(text: str) -> str | None:
    norm = normalize(text)
    # Also scan a de-spaced letter stream so "a dds" still matches "adds", "mi nus" → "minus", etc.
    glued = re.sub(r"[^a-z]", "", norm)
    for keywords, op in _OPS:
        for k in keywords:
            kk = k.strip()
            if kk in norm or kk.replace(" ", "") in glued:
                return op
    return None


def solve_locally(challenge_text: str) -> str | None:
    """Best-effort deterministic solve. Returns the answer formatted to 2 decimals, or None."""
    nums = extract_numbers(challenge_text)
    if len(nums) < 2:
        # Token method missed operands (adversarial in-word spacing) — try the glued fallback.
        nums = extract_numbers_glued(challenge_text)
    op = detect_op(challenge_text)
    if len(nums) < 2 or op is None:
        return None
    a, b = nums[0], nums[1]
    # "subtract X from Y" / "X less than Y" → Y - X. Swap so the larger-context operand leads.
    if op == "-" and any(k in normalize(challenge_text) for k in _REVERSED_SUBTRACT):
        a, b = b, a
    try:
        result = {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else None}[op]
    except Exception:
        return None
    if result is None:
        return None
    return f"{result:.2f}"


def format_answer(value: float) -> str:
    return f"{value:.2f}"
