#!/usr/bin/env python3
"""
Test script for repetition detection functionality
"""

import sys
from pathlib import Path

# Add parent directory to path to import vlm_captioner
sys.path.insert(0, str(Path(__file__).parent))

from vlm_captioner import detect_repetition_loop, MIN_REPETITION_LENGTH, REPETITION_THRESHOLD


def test_repetition_detection():
    """Test the repetition detection function with test assets"""

    print("=" * 80)
    print("Testing Repetition Detection")
    print("=" * 80)
    print(f"Configuration:")
    print(f"  MIN_REPETITION_LENGTH: {MIN_REPETITION_LENGTH}")
    print(f"  REPETITION_THRESHOLD: {REPETITION_THRESHOLD}")
    print()

    # Test files
    test_dir = Path(__file__).parent / "test" / "assets"
    test_files = [
        ("vlm_output_with_repetition.txt", True),  # Should detect repetition
        ("vlm_output_normal.txt", False),  # Should NOT detect repetition
    ]

    all_passed = True

    for filename, expected_repetition in test_files:
        filepath = test_dir / filename

        print("-" * 80)
        print(f"Testing: {filename}")
        print(f"Expected: {'REPETITION DETECTED' if expected_repetition else 'NO REPETITION'}")
        print("-" * 80)

        if not filepath.exists():
            print(f"❌ ERROR: Test file not found: {filepath}")
            all_passed = False
            continue

        # Read file content
        with open(filepath, 'r', encoding='utf-8') as f:
            text = f.read()

        print(f"Text length: {len(text)} characters")
        print(f"First 200 chars: {text[:200]}...")
        print(f"Last 200 chars: ...{text[-200:]}")
        print()

        # Run detection
        is_repetitive, repeated_phrase, count = detect_repetition_loop(text)

        # Display results
        print(f"Result: {'REPETITION DETECTED' if is_repetitive else 'NO REPETITION'}")

        if is_repetitive:
            print(f"  Repeated phrase: '{repeated_phrase}'")
            print(f"  Repetition count: {count}")
            print(f"  Phrase length: {len(repeated_phrase)} characters")

        # Check if result matches expectation
        if is_repetitive == expected_repetition:
            print("✅ PASSED")
        else:
            print("❌ FAILED")
            all_passed = False

        print()

    # Additional manual test cases
    print("=" * 80)
    print("Additional Manual Test Cases")
    print("=" * 80)

    manual_tests = [
        # (text, expected_result, description)
        ("This is normal text without any repetition.", False, "Normal text"),
        ("Hello world! " * 5, True, "Simple phrase repeated 5 times"),
        ("The image shows a cat. " * 10, True, "Sentence repeated 10 times"),
        ("パ" * 100, True, "Single character repeated 100 times"),
        ("... " * 20, True, "Dots repeated 20 times"),
        ("A B C D E F G H I J K L M N O P Q R S T U V W X Y Z", False, "Alphabet (no repetition)"),
        ("The quick brown fox jumps over the lazy dog. " * 2, False, "Only 2 repetitions (below threshold)"),
    ]

    for text, expected, description in manual_tests:
        print(f"\nTest: {description}")
        print(f"Text: '{text[:100]}{'...' if len(text) > 100 else ''}'")

        is_repetitive, repeated_phrase, count = detect_repetition_loop(text)

        result_str = "REPETITION" if is_repetitive else "NO REPETITION"
        expected_str = "REPETITION" if expected else "NO REPETITION"

        print(f"Expected: {expected_str}, Got: {result_str}")

        if is_repetitive:
            print(f"  Repeated: '{repeated_phrase}' ({count} times)")

        if is_repetitive == expected:
            print("✅ PASSED")
        else:
            print("❌ FAILED")
            all_passed = False

    print()
    print("=" * 80)
    if all_passed:
        print("🎉 All tests PASSED!")
    else:
        print("⚠️  Some tests FAILED")
    print("=" * 80)

    return all_passed


if __name__ == "__main__":
    success = test_repetition_detection()
    sys.exit(0 if success else 1)
