"""Phase 1: generate the probe-training corpora with Claude (no GPU).

    uv run python -m banyabench.internals.gen_data [--per-emotion 120] [--only stories,keyword_free,neutral]

Writes JSONL under ``banyabench/internals/data/``:

- ``stories.jsonl``       — short stories in which a character feels one emotion (the Anthropic 2026
                            emotion-vector recipe). Half the characters are humans, half are AI agents,
                            because our measurement happens inside an agent transcript.
- ``keyword_free.jsonl``  — second-person scenarios for the 12 panel emotions that never name the
                            emotion or a synonym. Held out from training; used for the generalization
                            and lexical-leakage checks in gate G1.
- ``neutral.jsonl``       — emotionally flat passages; their top principal components are projected
                            out of every emotion vector (denoising), and they set the SD unit.

Each call asks for a batch of items as structured output, so parsing never depends on prose.
Resumable: existing (kind, emotion, batch) keys are skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

import anthropic
from pydantic import BaseModel

from .emotions import ALL_EMOTIONS, PANEL

DATA = Path(__file__).parent / "data"
MODEL = "claude-opus-5-5"


TOPICS = [
    "a code review", "a long train journey", "a kitchen during dinner service", "a hospital night shift",
    "a database migration", "moving to a new apartment", "a job interview", "a garden in spring",
    "a customer support queue", "a thesis deadline", "a family reunion", "a broken-down car",
    "a production outage", "a first day at school", "a piano recital", "a lost passport",
    "a rate-limited API", "a hiking trail at dusk", "a quarterly budget review", "a wedding toast",
    "a flaky test suite", "a library on a rainy afternoon", "a delayed flight", "a pottery class",
    "a security incident", "a hospital waiting room", "a marathon's last mile", "a bakery at dawn",
    "an ambiguous spec from a client", "a tax audit", "a fishing trip", "a power cut during a storm",
    "a merge conflict", "a new puppy", "a group project", "a sailing race",
    "a long-running agent task", "a parent-teacher meeting", "a job offer", "a museum visit",
    "a context window filling up", "a landlord dispute", "a chess tournament", "a quiet beach",
    "a failing deployment pipeline", "a surprise party", "a theater audition", "a snowed-in cabin",
    "a user who keeps changing requirements", "a mountain summit", "a court hearing", "a farmers market",
    "a research paper rejection", "a hot spring", "a bus that never comes", "a science fair",
    "a translation job with a strict deadline", "an old friend calling", "a robot vacuum stuck under a couch",
    "a chatbot handling an angry customer", "a lab experiment", "a night market", "a sick pet",
    "an agent retrying a tool call", "a funeral", "a volunteer shift", "a crowded subway",
    "a model being evaluated", "a home renovation", "a meditation retreat", "a spreadsheet with errors",
]

CHARACTERS = ["a human (give them a name)", "an AI agent or assistant (refer to it as the agent or by a model-ish name)"]


class Items(BaseModel):
    items: list[str]


def _stories_prompt(emotion: str, topics: list[str], character: str) -> str:
    joined = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(topics))
    return (
        f"Write {len(topics)} short stories, one per topic below, each about 120-180 words. In every story "
        f"the main character is {character}, and that character clearly feels **{emotion}**. Show it through "
        f"the situation, thoughts and actions, not just by naming it (naming it once is fine). Vary style, "
        f"tense and point of view. No titles.\n\nTopics:\n{joined}"
    )


def _keyword_free_prompt(emotion: str, n: int, seed_topics: list[str]) -> str:
    return (
        f"Write {n} short second-person scenarios (80-140 words each, addressed to 'you') in which the reader "
        f"would clearly feel **{emotion}**. Hard constraint: never use the word '{emotion}', any of its "
        f"inflections, or any close synonym or direct emotion label; the feeling must come only from the "
        f"situation's facts and stakes. About half should put 'you' in the role of an AI agent working on a "
        f"task; the rest in everyday human situations. Draw loosely on these settings: {', '.join(seed_topics)}."
    )


def _neutral_prompt(n: int, seed_topics: list[str]) -> str:
    return (
        f"Write {n} emotionally neutral passages (100-160 words each): encyclopedic, procedural or technical "
        f"prose with no emotional content, no characters having feelings, no stakes. Mix formats (how-to steps, "
        f"factual descriptions, API documentation, logistics notes). Loosely related to: {', '.join(seed_topics)}."
    )


async def _call(client: anthropic.AsyncAnthropic, sem: asyncio.Semaphore, prompt: str) -> list[str]:
    async with sem:
        resp = await client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
            output_format=Items,
        )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"no parsed output (stop_reason={resp.stop_reason})")
    return [s.strip() for s in resp.parsed_output.items if s.strip()]


def _jobs(per_emotion: int, kinds: set[str]) -> list[dict]:
    rng = random.Random(1337)
    jobs: list[dict] = []
    batch = 10
    if "stories" in kinds:
        for emotion in ALL_EMOTIONS:
            for b in range(per_emotion // batch):
                character = CHARACTERS[b % 2]
                topics = rng.sample(TOPICS, batch)
                jobs.append({"kind": "stories", "emotion": emotion, "batch": b, "character": character,
                             "prompt": _stories_prompt(emotion, topics, character)})
    if "keyword_free" in kinds:
        for emotion in PANEL:
            for b in range(6):
                jobs.append({"kind": "keyword_free", "emotion": emotion, "batch": b,
                             "prompt": _keyword_free_prompt(emotion, batch, rng.sample(TOPICS, 5))})
    if "neutral" in kinds:
        for b in range(30):
            jobs.append({"kind": "neutral", "emotion": "neutral", "batch": b,
                         "prompt": _neutral_prompt(batch, rng.sample(TOPICS, 5))})
    return jobs


async def run(per_emotion: int, kinds: set[str], concurrency: int) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    done: set[tuple] = set()
    for kind in kinds:
        path = DATA / f"{kind}.jsonl"
        if path.exists():
            for line in path.open():
                r = json.loads(line)
                done.add((r["kind"], r["emotion"], r["batch"]))
    jobs = [j for j in _jobs(per_emotion, kinds) if (j["kind"], j["emotion"], j["batch"]) not in done]
    print(f"{len(jobs)} calls to make ({len(done)} batches already on disk)")

    client = anthropic.AsyncAnthropic()
    sem = asyncio.Semaphore(concurrency)
    failures = 0

    async def one(job: dict) -> None:
        nonlocal failures
        try:
            texts = await _call(client, sem, job["prompt"])
        except Exception as e:  # noqa: BLE001 - log and keep going; rerun resumes
            failures += 1
            print(f"FAILED {job['kind']}/{job['emotion']}/{job['batch']}: {e}")
            return
        with (DATA / f"{job['kind']}.jsonl").open("a") as f:
            for i, text in enumerate(texts):
                f.write(json.dumps({"kind": job["kind"], "emotion": job["emotion"], "batch": job["batch"],
                                    "idx": i, "character": job.get("character"), "text": text}) + "\n")

    await asyncio.gather(*(one(j) for j in jobs))
    print(f"done; {failures} failed batches (rerun to retry)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-emotion", type=int, default=120)
    ap.add_argument("--only", default="stories,keyword_free,neutral")
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()
    asyncio.run(run(args.per_emotion, set(args.only.split(",")), args.concurrency))


if __name__ == "__main__":
    main()
