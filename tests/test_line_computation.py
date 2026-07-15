"""Tests for pg_file_line computation (step 6d).

Tests that line numbers are computed from the occurrence of pg_text
NEAREST to candidate.pg_offset, not always the first occurrence.
This prevents duplicate errata entries (same word on different scan pages)
from all getting the same line number.
"""
import pytest


class TestLineComputationNearestOffset:
    """Verify that find_nearest_occurrence logic picks the right match."""

    def test_single_occurrence_uses_first(self):
        """When pg_text appears once, that occurrence is used."""
        body = "Hello world. This is a test."
        pg_text = "world"
        offset = 5  # near the actual position

        # Simulate the nearest-occurrence search
        best_pos = -1
        best_dist = float("inf")
        search_from = 0
        while True:
            found = body.find(pg_text, search_from)
            if found < 0:
                break
            dist = abs(found - offset)
            if dist < best_dist:
                best_dist = dist
                best_pos = found
            search_from = found + 1

        assert best_pos == 6  # "world" at index 6

    def test_multiple_occurrences_picks_nearest(self):
        """When pg_text appears multiple times, pick the one nearest pg_offset."""
        body = "here's the first. Some text. here's the second. More text. here's the third."
        # Occurrences at 0, 29, 59
        pg_text = "here's"

        # Candidate aligned to offset ~32 (nearest to second occurrence at 29)
        offset = 32

        best_pos = -1
        best_dist = float("inf")
        search_from = 0
        while True:
            found = body.find(pg_text, search_from)
            if found < 0:
                break
            dist = abs(found - offset)
            if dist < best_dist:
                best_dist = dist
                best_pos = found
            search_from = found + 1

        assert best_pos == 29  # second occurrence, nearest to offset 32

    def test_all_three_get_different_lines(self):
        """Three candidates with different offsets should get different line numbers."""
        # Simulate the Nibelungenlied here's ==> hero's scenario
        body = (
            "Line one here's first.\n"
            "Line two some text.\n"
            "Line three here's second.\n"
            "Line four more text.\n"
            "Line five here's third.\n"
        )
        pg_text = "here's"
        # Find actual offsets
        import re
        offsets = [m.start() for m in re.finditer(pg_text, body)]
        # Use those offsets as candidate offsets

        results = []
        for candidate_offset in offsets:
            best_pos = -1
            best_dist = float("inf")
            search_from = 0
            while True:
                found = body.find(pg_text, search_from)
                if found < 0:
                    break
                dist = abs(found - candidate_offset)
                if dist < best_dist:
                    best_dist = dist
                    best_pos = found
                search_from = found + 1
            line = body[:best_pos].count("\n") + 1
            results.append(line)

        assert results == [1, 3, 5], f"Expected [1, 3, 5], got {results}"
        assert len(set(results)) == 3  # all different

    def test_old_behavior_first_match_gives_wrong_result(self):
        """Demonstrate that naive find() gives wrong results for duplicates."""
        body = (
            "Line one here's first.\n"
            "Line two some text.\n"
            "Line three here's second.\n"
        )
        pg_text = "here's"

        # Old behavior: always first occurrence
        old_pos = body.find(pg_text)
        old_line = body[:old_pos].count("\n") + 1
        assert old_line == 1  # always line 1, regardless of offset

        # New behavior for second occurrence
        offset = 40
        best_pos = -1
        best_dist = float("inf")
        search_from = 0
        while True:
            found = body.find(pg_text, search_from)
            if found < 0:
                break
            dist = abs(found - offset)
            if dist < best_dist:
                best_dist = dist
                best_pos = found
            search_from = found + 1
        new_line = body[:best_pos].count("\n") + 1
        assert new_line == 3  # correctly identifies line 3

    def test_not_found_falls_back_to_offset(self):
        """When pg_text isn't in body, fall back to pg_offset for line number."""
        body = "Hello world.\nSecond line.\nThird line.\n"
        pg_text = "missing"

        best_pos = -1
        best_dist = float("inf")
        search_from = 0
        while True:
            found = body.find(pg_text, search_from)
            if found < 0:
                break
            dist = abs(found - 20)
            if dist < best_dist:
                best_dist = dist
                best_pos = found
            search_from = found + 1

        # Should fall back
        offset = 20
        if best_pos >= 0:
            line = body[:best_pos].count("\n") + 1
        else:
            line = body[:offset].count("\n") + 1

        assert line == 2  # offset 20 is in line 2
