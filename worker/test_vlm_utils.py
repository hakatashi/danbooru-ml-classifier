"""
Pytest tests for VLM utility functions
"""

import pytest
from pathlib import Path
from vlm_utils import detect_repetition_loop, parse_moderation_rating


@pytest.fixture
def test_assets_dir():
    """Return the test assets directory"""
    return Path(__file__).parent / "test" / "assets"


class TestDetectRepetitionLoop:
    """Tests for detect_repetition_loop function"""

    def test_vlm_output_with_repetition(self, test_assets_dir):
        """Should detect repetition in VLM output with loops"""
        with open(test_assets_dir / "vlm_output_with_repetition.txt") as f:
            text = f.read()

        is_repetitive, repeated_phrase, count = detect_repetition_loop(text)

        assert is_repetitive is True
        assert repeated_phrase is not None
        assert count > 100  # Many repetitions expected

    def test_vlm_output_normal(self, test_assets_dir):
        """Should not detect repetition in normal VLM output"""
        with open(test_assets_dir / "vlm_output_normal.txt") as f:
            text = f.read()

        is_repetitive, repeated_phrase, count = detect_repetition_loop(text)

        assert is_repetitive is False
        assert repeated_phrase is None
        assert count == 0

    @pytest.mark.parametrize("text,expected_repetitive", [
        ("This is normal text without any repetition.", False),
        ("The image shows a cat. " * 10, True),
        ("パ" * 100, True),
        ("... " * 20, True),
        ("A B C D E F G H I J K L M N O P Q R S T U V W X Y Z", False),
        ("The quick brown fox jumps over the lazy dog. " * 2, False),  # Below threshold
    ])
    def test_various_patterns(self, text, expected_repetitive):
        """Test various repetition patterns"""
        is_repetitive, _, _ = detect_repetition_loop(text)
        assert is_repetitive == expected_repetitive

    def test_short_text(self):
        """Should return False for text that's too short"""
        is_repetitive, _, _ = detect_repetition_loop("short")
        assert is_repetitive is False

    def test_empty_text(self):
        """Should return False for empty text"""
        is_repetitive, _, _ = detect_repetition_loop("")
        assert is_repetitive is False

    def test_none_text(self):
        """Should return False for None"""
        is_repetitive, _, _ = detect_repetition_loop(None)
        assert is_repetitive is False


class TestParseModerationRating:
    """Tests for parse_moderation_rating function"""

    def test_simple_rating(self):
        """Should extract simple rating"""
        result = parse_moderation_rating("[[5]]")
        assert result == 5

    def test_rating_with_think_block(self):
        """Should ignore ratings in <think> blocks"""
        result = parse_moderation_rating("<think>Maybe [[3]]?</think>[[7]]")
        assert result == 7

    def test_multiple_ratings_in_think(self):
        """Should ignore all ratings in <think> blocks"""
        result = parse_moderation_rating("<think>This could be [[4]] or [[6]]</think>[[8]]")
        assert result == 8

    def test_rating_before_think_block(self):
        """Should extract rating before <think> block"""
        result = parse_moderation_rating("[[9]]<think>Some reasoning with [[2]]</think>")
        assert result == 9

    def test_multiline_think_block(self):
        """Should handle multiline <think> blocks"""
        text = """<think>
Long reasoning
with [[3]] in it
and more text
</think>[[10]]"""
        result = parse_moderation_rating(text)
        assert result == 10

    def test_no_rating(self):
        """Should return None when no rating found"""
        result = parse_moderation_rating("No rating here")
        assert result is None

    def test_only_rating_in_think(self):
        """Should return None when rating is only in <think> block"""
        result = parse_moderation_rating("<think>[[5]]</think>")
        assert result is None

    @pytest.mark.parametrize("rating_value", range(11))
    def test_all_rating_values(self, rating_value):
        """Should correctly parse all valid rating values (0-10)"""
        result = parse_moderation_rating(f"[[{rating_value}]]")
        assert result == rating_value
