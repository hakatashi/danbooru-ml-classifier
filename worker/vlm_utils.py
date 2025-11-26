"""
VLM Utility Functions

Standalone utility functions for VLM processing that can be tested
independently without Firebase or other heavy dependencies.
"""

import re


# Repetition detection configuration
MIN_REPETITION_LENGTH = 10  # Minimum length of repeated phrase to detect (in characters)
REPETITION_THRESHOLD = 3  # Number of consecutive repetitions to trigger retry


def parse_moderation_rating(raw_result):
    """Parse moderation rating from raw result string

    Extracts the moderation rating [[N]] from the result, excluding any
    ratings found within <think>...</think> blocks.

    Args:
        raw_result: Raw string output from VLM model

    Returns:
        int: Extracted rating (0-10), or None if not found
    """
    # Remove <think> blocks to avoid matching ratings inside them
    text_without_think = re.sub(r'<think>.*?</think>', '', raw_result, flags=re.DOTALL)

    # Look for [[N]] pattern in the remaining text
    match = re.search(r'\[\[(\d+)\]\]', text_without_think)
    if match:
        return int(match.group(1))
    return None


def detect_repetition_loop(text, min_length=MIN_REPETITION_LENGTH, threshold=REPETITION_THRESHOLD):
    """Detect if text contains repetitive phrases that suggest a model loop

    Args:
        text: The text to check for repetition
        min_length: Minimum length of repeated phrase to consider (in characters)
        threshold: Number of consecutive repetitions required to flag as loop

    Returns:
        tuple: (is_repetitive: bool, repeated_phrase: str or None, count: int)
    """
    if not text or len(text) < min_length * threshold:
        return False, None, 0

    # Strategy 1: Check for exact phrase repetition
    # Look for patterns where the same phrase appears multiple times in a row
    words = text.split()

    # Only use Strategy 1 if we have enough words to analyze
    # (Skip if text is mostly one long unseparated word)
    if len(words) >= 5:
        # Check for repeated sequences of different lengths (3-50 words)
        for seq_len in range(3, min(51, len(words) // threshold + 1)):
            for i in range(len(words) - seq_len * threshold):
                # Get the potential repeated phrase
                phrase = ' '.join(words[i:i + seq_len])

                # Skip if phrase is too short
                if len(phrase) < min_length:
                    continue

                # Count consecutive repetitions
                repetition_count = 1
                pos = i + seq_len

                while pos + seq_len <= len(words):
                    next_phrase = ' '.join(words[pos:pos + seq_len])
                    if next_phrase == phrase:
                        repetition_count += 1
                        pos += seq_len
                    else:
                        break

                # If we found enough repetitions, flag it
                if repetition_count >= threshold:
                    return True, phrase, repetition_count

    # Strategy 2: Check for character-level repetition (for cases like "... ... ...")
    # Look for patterns where the same short string repeats many times
    for pattern_len in range(2, 20):
        for i in range(len(text) - pattern_len * threshold):
            pattern = text[i:i + pattern_len]

            # Count consecutive repetitions
            repetition_count = 1
            pos = i + pattern_len

            while pos + pattern_len <= len(text):
                if text[pos:pos + pattern_len] == pattern:
                    repetition_count += 1
                    pos += pattern_len
                else:
                    break

            # If we found many repetitions of a short pattern
            if repetition_count >= threshold * 2:  # Higher threshold for short patterns
                return True, pattern, repetition_count

    # Strategy 3: Check for single character repetition (for cases like "パパパパ...")
    # This catches cases where the same character appears many times consecutively
    if len(text) >= 30:  # Only check if text is long enough
        i = 0
        while i < len(text):
            char = text[i]
            # Only check non-whitespace characters
            if char.strip():
                consecutive_count = 1
                j = i + 1
                # Count how many times this character appears consecutively
                while j < len(text) and text[j] == char:
                    consecutive_count += 1
                    j += 1

                # If same character appears 30+ times consecutively, it's likely a loop
                if consecutive_count >= 30:
                    return True, char, consecutive_count

                i = j  # Skip past the counted characters
            else:
                i += 1

    return False, None, 0
