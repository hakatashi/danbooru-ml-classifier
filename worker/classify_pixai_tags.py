#!/usr/bin/env python3
"""
Classify PixAI feature tags (general/feature category, index 0..gen_tag_count-1)
into semantic axes using Qwen3-32B via llama.cpp.

Output categories:
  character  - Physical appearance: hair color/style, eye color, body shape/parts,
                clothing items, accessories, animal features (ears, tails)
  situation  - What is happening: pose, action, expression, composition/framing,
                background/environment, relationship, gaze direction, sexual acts
  style      - Artistic technique: art style names, medium, rendering, color treatment,
                era styles (e.g. 2000s_(style)), shading
  other      - Meta/count tags (1girl, solo), ratings, ambiguous, uncategorizable

Output: pu-learning/data/metadata/pixai_tag_categories.json
        { "<tag>": "character" | "situation" | "style" | "other", ... }
"""

import json
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vlm_captioner import (
    SERVER_URL,
    chat_text_only_llama_api,
    get_model_paths,
    start_llama_server,
    stop_server,
    wait_for_server,
)

TAGS_JSON = Path.home() / ".cache" / "pixai-tagger" / "tags_v0.9_13k.json"
OUTPUT_JSON = (
    Path(__file__).parent.parent / "pu-learning" / "data" / "metadata" / "pixai_tag_categories.json"
)
BATCH_SIZE = 120
CATEGORIES = {"character", "situation", "style", "other"}

SYSTEM_PROMPT = """/no_think
You are a tag classification assistant for Danbooru-style anime/illustration image tags.

Classify each tag into exactly one of these four categories:
- character: Tags describing a character's physical appearance — hair color, hairstyle,
  eye color, body shape, body parts (breasts, thighs, groin, etc.), skin tone, clothing
  items, accessories, animal features (animal_ears, tail, etc.), physical traits
- situation: Tags describing what is happening in the scene — pose, action, facial
  expression, framing/composition (cowboy_shot, from_side, etc.), gaze direction
  (looking_at_viewer), number of characters (1girl, 2boys, solo, multiple_girls),
  relationship type between characters (yuri, hetero, yaoi, incest, etc.),
  sexual acts/scenarios, scene environment
- style: Tags describing the visual/artistic rendering — art style names, art medium
  (watercolor, oil_painting), shading technique, color palette, era/decade style
  (2000s_(style)), background color or transparency (white_background,
  transparent_background, simple_background, gradient_background, etc.)
- other: Meta/technical tags (highres, absurdres, commentary, traditional_media),
  tags related to the image format or source, or tags that don't fit any above category

Respond with ONLY a valid JSON object: {"tag_name": "category", ...}
No explanation, no markdown, no code blocks — just the raw JSON object.
"""


def load_feature_tags() -> list[str]:
    with open(TAGS_JSON) as f:
        data = json.load(f)
    tag_map = data["tag_map"]
    gen_tag_count = data["tag_split"]["gen_tag_count"]
    by_index = sorted(tag_map.items(), key=lambda x: x[1])
    return [tag for tag, idx in by_index if idx < gen_tag_count]


def parse_llm_json(response: str) -> dict[str, str] | None:
    text = response.strip()
    # Strip optional markdown code fences
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract the first {...} block
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


def classify_batch(batch: list[str]) -> dict[str, str] | None:
    tags_json = json.dumps(batch, ensure_ascii=False)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Classify these tags:\n{tags_json}"},
    ]
    response = chat_text_only_llama_api(
        messages,
        max_tokens=4096,
        temperature=0.1,
        top_p=0.9,
    )
    if not response:
        return None

    parsed = parse_llm_json(response)
    if parsed is None:
        print(f"  [warn] JSON parse failed. Response excerpt: {response[:300]!r}")
        return None

    # Normalise and validate
    validated: dict[str, str] = {}
    for tag, cat in parsed.items():
        cat = str(cat).lower().strip()
        if cat in CATEGORIES:
            validated[tag] = cat
        else:
            print(f"  [warn] Unknown category {cat!r} for tag {tag!r} → 'other'")
            validated[tag] = "other"
    return validated


def main() -> None:
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    # Resume from existing output if present
    if OUTPUT_JSON.exists():
        with open(OUTPUT_JSON) as f:
            results: dict[str, str] = json.load(f)
        print(f"Resuming: {len(results)} tags already classified.")
    else:
        results = {}

    all_tags = load_feature_tags()
    remaining = [t for t in all_tags if t not in results]
    random.shuffle(remaining)
    print(f"Total feature tags: {len(all_tags)}  |  Remaining: {len(remaining)}")

    if not remaining:
        print("All tags classified — nothing to do.")
    else:
        print("Loading Qwen3-32B via llama.cpp ...")
        language_model, _ = get_model_paths("qwen3-32b")
        server_process = start_llama_server(language_model, None)
        if not server_process:
            print("ERROR: failed to start llama-server", file=sys.stderr)
            sys.exit(1)

        try:
            if not wait_for_server(SERVER_URL):
                print("ERROR: server did not become ready", file=sys.stderr)
                sys.exit(1)

            batches = [remaining[i : i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
            print(f"Processing {len(batches)} batches × up to {BATCH_SIZE} tags ...\n")

            for i, batch in enumerate(batches):
                print(f"Batch {i + 1}/{len(batches)} ({len(batch)} tags) ...", end=" ", flush=True)

                batch_result: dict[str, str] | None = None
                for attempt in range(3):
                    batch_result = classify_batch(batch)
                    if batch_result is not None:
                        break
                    print(f"retry {attempt + 1}...", end=" ", flush=True)
                    time.sleep(3)

                if batch_result is None:
                    print("FAILED — marking all as 'other'")
                    batch_result = {tag: "other" for tag in batch}
                else:
                    # Fill any tags the LLM silently dropped
                    missing = [t for t in batch if t not in batch_result]
                    if missing:
                        print(f"({len(missing)} missing tags → 'other') ...", end=" ", flush=True)
                        for tag in missing:
                            batch_result[tag] = "other"
                    print("ok")

                results.update(batch_result)

                with open(OUTPUT_JSON, "w") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)

        finally:
            stop_server(server_process)

    # Summary
    counts = Counter(results.values())
    print(f"\nDone. Results → {OUTPUT_JSON}")
    print("Category distribution:")
    for cat in sorted(CATEGORIES):
        print(f"  {cat:12s}: {counts.get(cat, 0):5d}")


if __name__ == "__main__":
    main()
