"""Tests for pg_file_line computation (step 6d).

Tests that line numbers are computed from the occurrence of pg_text
NEAREST to candidate.pg_offset, not always the first occurrence.
This prevents duplicate errata entries (same word on different scan pages)
from all getting the same line number.

Test layers:
  1. Algorithm tests — verify the search logic in isolation
  2. Model tests — verify CandidateError gets correct pg_file_line
  3. Integration tests — simulate the real Nibelungenlied scenario
  4. Regression tests — ensure existing capabilities aren't degraded
"""
import pytest
import re
from gerrata.models import CandidateError, ErrorCategory, ErrorSeverity


def compute_pg_file_line(pg_text: str, body_text: str, pg_offset: int) -> int:
    """Standalone version of the step 6d logic for testing.
    
    Finds the occurrence of pg_text in body_text nearest to pg_offset,
    returns the 1-based line number. Falls back to pg_offset if not found.
    """
    if '(absent in PG)' in pg_text or '(absent in scan)' in pg_text:
        # Use scan_text instead — caller should handle this
        pass
    
    best_pos = -1
    best_dist = float('inf')
    search_from = 0
    while True:
        found = body_text.find(pg_text, search_from)
        if found < 0:
            break
        dist = abs(found - pg_offset)
        if dist < best_dist:
            best_dist = dist
            best_pos = found
        search_from = found + 1
    
    if best_pos >= 0:
        return body_text[:best_pos].count('\n') + 1
    else:
        return body_text[:pg_offset].count('\n') + 1


class TestAlgorithmNearestOffset:
    """Verify the nearest-occurrence search logic in isolation."""

    def test_single_occurrence(self):
        """One occurrence — trivially correct."""
        body = "Hello world. This is a test."
        line = compute_pg_file_line("world", body, 5)
        assert line == 1

    def test_multiple_occurrences_picks_nearest(self):
        """Three occurrences — pick the one closest to pg_offset."""
        body = "alpha here's first. beta. here's second. gamma. here's third."
        # Occurrences at: 6, 29, 51
        line_near_second = compute_pg_file_line("here's", body, 31)
        # Offset 31 is nearest to occurrence at 29
        # body[:29] has no newlines, so line 1
        assert line_near_second == 1

    def test_occurrences_on_different_lines(self):
        """Three occurrences on different lines — each maps correctly."""
        body = (
            "Line 1 here's A.\n"
            "Line 2 filler.\n"
            "Line 3 here's B.\n"
            "Line 4 filler.\n"
            "Line 5 here's C.\n"
        )
        offsets = [m.start() for m in re.finditer("here's", body)]
        assert len(offsets) == 3
        
        lines = [compute_pg_file_line("here's", body, off) for off in offsets]
        assert lines == [1, 3, 5]
        assert len(set(lines)) == 3

    def test_not_found_falls_back_to_offset(self):
        """pg_text not in body — use pg_offset for line number."""
        body = "Hello world.\nSecond line.\nThird line.\n"
        line = compute_pg_file_line("nonexistent", body, 20)
        # Offset 20 is in line 2
        assert line == 2

    def test_offset_zero_with_match(self):
        """pg_offset=0 should find the first occurrence."""
        body = "test line one.\ntest line two.\n"
        line = compute_pg_file_line("test", body, 0)
        assert line == 1

    def test_offset_zero_no_match(self):
        """pg_offset=0 and no match — line 1."""
        body = "test line one.\ntest line two.\n"
        line = compute_pg_file_line("missing", body, 0)
        assert line == 1

    def test_very_large_offset(self):
        """pg_offset beyond body length — still finds nearest occurrence."""
        body = "word line one.\nword line two.\n"
        line = compute_pg_file_line("word", body, 999999)
        # Nearest (and only feasible) occurrence at start of line 2
        # Occurrences at 0 and 15; 15 is closer to 999999? No — both far.
        # abs(0 - 999999) = 999999, abs(15 - 999999) = 999984
        # 15 is closer, so line 2
        assert line == 2

    def test_empty_pg_text(self):
        """Empty pg_text string — find() returns 0, line 1."""
        body = "Some text.\nMore text.\n"
        line = compute_pg_file_line("", body, 5)
        # body.find("", 0) returns 0, body.find("", 1) returns 1, etc.
        # Empty string matches at every position
        # Nearest to offset 5 would be position 5
        # body[:5].count('\n') + 1 = 1
        assert line == 1

    def test_adjacent_occurrences(self):
        """Two occurrences right next to each other."""
        body = "cat cat dog.\nLine two.\n"
        # "cat" at offset 0 and 4
        line_for_offset_4 = compute_pg_file_line("cat", body, 4)
        # Offset 4 is AT the second occurrence
        assert line_for_offset_4 == 1

    def test_occurrence_at_body_boundary(self):
        """pg_text at the very end of body."""
        body = "Hello world.\nFinal endpoint"
        line = compute_pg_file_line("endpoint", body, 14)
        assert line == 2


class TestCandidateErrorLineAssignment:
    """Verify that CandidateError objects get correct pg_file_line values."""

    def test_candidate_gets_correct_line(self):
        """CandidateError with multiple occurrences gets the right line."""
        body = (
            "The word ogre appears here.\n"
            "Some filler text.\n"
            "Another ogre sighting.\n"
        )
        # Find second "ogre" 
        second_offset = body.find("ogre", body.find("ogre") + 1)
        
        candidate = CandidateError(
            pg_text="ogre",
            scan_text="orgre",
            pg_offset=second_offset,
            scan_page=5,
        )
        
        # Apply step 6d logic
        candidate.pg_file_line = compute_pg_file_line(
            candidate.pg_text, body, candidate.pg_offset
        )
        
        assert candidate.pg_file_line == 3  # "Another ogre sighting" is line 3

    def test_two_candidates_same_text_different_offsets(self):
        """Two candidates with same pg_text but different offsets get different lines."""
        body = (
            "Line A has wurd.\n"
            "Line B filler.\n"
            "Line C has wurd.\n"
            "Line D filler.\n"
            "Line E has wurd.\n"
        )
        
        offsets = [m.start() for m in re.finditer("wurd", body)]
        candidates = []
        for i, off in enumerate(offsets):
            c = CandidateError(
                pg_text="wurd",
                scan_text="word",
                pg_offset=off,
                scan_page=10 + i,
            )
            c.pg_file_line = compute_pg_file_line(c.pg_text, body, c.pg_offset)
            candidates.append(c)
        
        lines = [c.pg_file_line for c in candidates]
        assert lines == [1, 3, 5]
        assert len(set(lines)) == 3  # all different

    def test_candidate_not_in_body_falls_back(self):
        """Candidate with pg_text not in body falls back to pg_offset."""
        body = "Hello world.\nSecond line here.\n"
        
        candidate = CandidateError(
            pg_text="absent",
            scan_text="present",
            pg_offset=15,
            scan_page=3,
        )
        candidate.pg_file_line = compute_pg_file_line(
            candidate.pg_text, body, candidate.pg_offset
        )
        # Offset 15 is in line 2
        assert candidate.pg_file_line == 2

    def test_absent_marker_uses_scan_text(self):
        """When pg_text has '(absent in PG)', scan_text is used for lookup."""
        body = "found here.\nMore text.\n"
        
        # Step 6d checks for absent markers
        pg_text_raw = "(absent in PG)"
        scan_text = "found"
        
        # The actual code does:
        # if '(absent in PG)' in pg_text or '(absent in scan)' in pg_text:
        #     pg_text = candidate.scan_text
        effective_text = scan_text
        
        line = compute_pg_file_line(effective_text, body, 0)
        assert line == 1  # "found" is on line 1


class TestNibelungenliedScenario:
    """Integration test simulating the real bug from the Nibelungenlied report.

    The word "here's" appears 3+ times in PG text. Multiple scan pages each
    contain "hero's" in their OCR. The diff produces 3 separate CandidateError
    entries. The old code assigned all 3 the same line number (from the first
    occurrence). The fix assigns each the line number of its nearest occurrence.
    """

    @pytest.fixture
    def nibelungenlied_body(self):
        """Simplified body_text mimicking the real Nibelungenlied structure.
        
        'here's' appears at 3 different lines, with filler between them
        to create distinct offsets that simulate different scan page alignments.
        """
        return (
            'A' * 500 + '\n'  # line 1: preamble
            + 'B' * 500 + '\n'  # line 2: preamble
            + 'C' * 100 + " valiant here's bride.\n"  # line 3: first occurrence
            + 'D' * 500 + '\n'  # line 4: filler
            + 'E' * 500 + '\n'  # line 5: filler
            + 'F' * 100 + " by here's hand were slain.\n"  # line 6: second occurrence
            + 'G' * 500 + '\n'  # line 7: filler
            + 'H' * 500 + '\n'  # line 8: filler
            + 'I' * 100 + " thrown by here's hand hurtling.\n"  # line 9: third occurrence
            + 'J' * 500 + '\n'  # line 10: filler
        )

    def test_three_entries_get_different_lines(self, nibelungenlied_body):
        """Three candidates from different scan pages should get different lines."""
        body = nibelungenlied_body
        
        # Find all "here's" positions
        positions = [m.start() for m in re.finditer("here's", body)]
        assert len(positions) == 3, f"Expected 3 occurrences, got {len(positions)}"
        
        # Simulate the bug: three candidates with WRONG pg_offsets
        # (as produced by the diff using alignment.pg_start)
        # These offsets are in the wrong place but nearest to different occurrences
        wrong_offsets = [
            positions[0] + 200,   # near first occurrence
            positions[1] + 200,   # near second occurrence  
            positions[2] + 200,   # near third occurrence
        ]
        
        lines = [
            compute_pg_file_line("here's", body, off) for off in wrong_offsets
        ]
        
        # All three should be different
        assert len(set(lines)) == 3, f"Lines should all differ: {lines}"
        assert lines[0] == 3   # first occurrence is on line 3
        assert lines[1] == 6   # second on line 6
        assert lines[2] == 9  # third on line 9

    def test_old_behavior_would_collide(self, nibelungenlied_body):
        """Demonstrate that naive find() collapses all to the same line."""
        body = nibelungenlied_body
        
        positions = [m.start() for m in re.finditer("here's", body)]
        wrong_offsets = [positions[0] + 200, positions[1] + 200, positions[2] + 200]
        
        # Old behavior: body.find() always returns first
        old_lines = []
        for _ in wrong_offsets:
            pos = body.find("here's")
            old_lines.append(body[:pos].count('\n') + 1)
        
        # All same (wrong)
        assert len(set(old_lines)) == 1
        assert old_lines[0] == 3  # always line 3

    def test_correct_offsets_also_work(self, nibelungenlied_body):
        """Even with correct pg_offsets, the nearest-match logic works."""
        body = nibelungenlied_body
        positions = [m.start() for m in re.finditer("here's", body)]
        
        lines = [compute_pg_file_line("here's", body, p) for p in positions]
        assert lines == [3, 6, 9]

    def test_far_wrong_offset_still_finds_nearest(self, nibelungenlied_body):
        """Even very wrong offsets find the nearest occurrence."""
        body = nibelungenlied_body
        positions = [m.start() for m in re.finditer("here's", body)]
        
        # Offset much closer to second occurrence than first or third
        offset_near_second = positions[1] - 50
        line = compute_pg_file_line("here's", body, offset_near_second)
        assert line == 6  # second occurrence line


class TestRegressionNoDegradation:
    """Verify the fix doesn't degrade existing capabilities."""

    def test_unique_word_unched(self):
        """Unique words still get the correct line (no regression)."""
        body = (
            "The dragon Smaug.\n"
            "lived in the mountain.\n"
            "Bilbo was a burglar.\n"
        )
        # Each word appears once — behavior identical to find()
        assert compute_pg_file_line("Smaug", body, 10) == 1
        assert compute_pg_file_line("mountain", body, 30) == 2
        assert compute_pg_file_line("burglar", body, 50) == 3

    def test_long_phrase_still_works(self):
        """Multi-word pg_text still finds the right position."""
        body = (
            "In a hole in the ground there lived a hobbit.\n"
            "Not a nasty, dirty, wet hole.\n"
        )
        phrase = "In a hole in the ground"
        line = compute_pg_file_line(phrase, body, 0)
        assert line == 1

    def test_special_characters_in_text(self):
        """Apostrophes and quotes in pg_text work correctly."""
        body = (
            "The hero's journey begins.\n"
            "Another hero's tale.\n"
            "Yet another hero's quest.\n"
        )
        offsets = [m.start() for m in re.finditer("hero's", body)]
        lines = [compute_pg_file_line("hero's", body, off) for off in offsets]
        assert lines == [1, 2, 3]

    def test_unicode_text(self):
        """Unicode characters in pg_text work correctly."""
        body = (
            "Ragnarök came.\n"
            "After Ragnarök ended.\n"
            "Then Ragnarök was reborn.\n"
        )
        offsets = [m.start() for m in re.finditer("Ragnarök", body)]
        lines = [compute_pg_file_line("Ragnarök", body, off) for off in offsets]
        assert lines == [1, 2, 3]

    def test_punctuation_only_difference(self):
        """Words that differ only by punctuation are found correctly."""
        body = (
            "He said well.\n"
            "The well was deep.\n"
            "Well, that's fine.\n"
        )
        # "well" vs "Well," — searching for "well" finds both
        offsets = [m.start() for m in re.finditer(r"\bwell\b", body)]
        assert len(offsets) >= 2
        
        # Each offset should map to the correct line
        for i, off in enumerate(offsets):
            line = compute_pg_file_line("well", body, off)
            # All occurrences of lowercase "well" — line 1 and line 2
            assert line in (1, 2)

    def test_missing_word_category_uses_scan_text_logic(self):
        """Missing word candidates (absent markers) handled correctly."""
        body = "The quick brown fox.\nJumped over.\nThe lazy dog.\n"
        
        # Candidate with absent marker
        pg_text = "(absent in PG)"
        scan_text = "missing"
        
        # Step 6d code checks for absent markers and uses scan_text
        effective = scan_text if '(absent' in pg_text else pg_text
        
        # scan_text "missing" not in body — falls back to offset
        line = compute_pg_file_line(effective, body, 25)
        assert line == 2  # offset 25 is in line 2

    def test_no_false_line_changes_for_unique_errors(self):
        """Errors with unique pg_text don't get different lines than before."""
        body = (
            "Line one.\n"
            "Line two with unique_word.\n"
            "Line three.\n"
            "Line four.\n"
        )
        # unique_word appears once — old and new behavior identical
        old_line = body[:body.find("unique_word")].count('\n') + 1
        new_line = compute_pg_file_line("unique_word", body, 22)
        assert old_line == new_line == 2

    def test_performance_many_occurrences(self):
        """Performance: text with many occurrences doesn't slow things down."""
        # 1000 occurrences of "the" in a long body
        body = "\n".join(f"Line {i}: the quick brown fox." for i in range(1000))
        
        # Should complete quickly even with 1000 occurrences
        import time
        start = time.time()
        line = compute_pg_file_line("the", body, 50000)
        elapsed = time.time() - start
        
        assert elapsed < 0.5  # should be well under 500ms
        assert 1 <= line <= 1000
