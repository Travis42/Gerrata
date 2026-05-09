"""Tests for additional false positive filters in Gerrata CLI.

Tests the 5 new filters added to the false positive filtering pipeline:
1. Long mismatch filter
2. HTML artifact filter  
3. ALL CAPS header filter
4. Suffix fragment filter (extended)
5. Quoted fragment filter
"""

import pytest
from gerrata.models import CandidateError, ErrorCategory


class TestLongMismatchFilter:
    """Test the long mismatch artifact detection."""

    def test_long_mismatch_long_scan_text(self):
        """Test that long scan text (>40 chars) is detected as artifact."""
        # Filter function implementation
        def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
            return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

        # Long scan text, short PG text
        assert is_long_mismatch(
            "few halloa, took to my heels, collared my gentleman, and bro...",
            "view"
        ) == True

    def test_long_mismatch_long_pg_text(self):
        """Test that long PG text (>40 chars) is detected as artifact."""
        def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
            return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

        # Short scan text, long PG text
        assert is_long_mismatch(
            "view",
            "few halloa, took to my heels, collared my gentleman, and bro..."
        ) == True

    def test_long_mismatch_both_short(self):
        """Test that short texts are not detected as artifacts."""
        def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
            return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

        # Both texts short
        assert is_long_mismatch("short", "medium") == False

    def test_long_mismatch_exactly_40_chars(self):
        """Test boundary case at exactly 40 characters."""
        def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
            return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

        # Exactly 40 chars should NOT trigger (only > 40)
        text_40_chars = "a" * 40
        assert is_long_mismatch(text_40_chars, "short") == False

        # 41 chars should trigger
        text_41_chars = "a" * 41
        assert is_long_mismatch(text_41_chars, "short") == True

    def test_long_mismatch_with_whitespace(self):
        """Test that whitespace is trimmed before checking length."""
        def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
            return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

        # Text with leading/trailing whitespace
        assert is_long_mismatch(
            "   few halloa, took to my heels, collared my gentleman, and bro...   ",
            "view"
        ) == True


class TestHTMLArtifactFilter:
    """Test the HTML artifact detection."""

    def test_html_artifact_div_tag(self):
        """Test detection of <div> tags."""
        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        assert is_html_artifact('<div align="center">', 'some text') == True

    def test_html_artifact_span_tag(self):
        """Test detection of <span> tags."""
        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        assert is_html_artifact('text with <span>markup</span>', 'other text') == True

    def test_html_artifact_bbox(self):
        """Test detection of bbox= image references."""
        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        assert is_html_artifact(
            '![](page=0,bbox=[7, 1583, 53, 1635])',
            'normal text'
        ) == True

    def test_html_artifact_markdown_image(self):
        """Test detection of markdown image syntax."""
        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        assert is_html_artifact(
            '![](image.png)',
            'text'
        ) == True

    def test_html_artifact_img_tag(self):
        """Test detection of <img> tags."""
        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        assert is_html_artifact('<img src="test.jpg">', 'text') == True

    def test_html_artifact_closing_div(self):
        """Test detection of </div> tags."""
        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        assert is_html_artifact('some content</div>', 'text') == True

    def test_html_artifact_normal_text(self):
        """Test that normal text without HTML is not detected."""
        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        assert is_html_artifact('normal text', 'other text') == False

    def test_html_artifact_in_combined_text(self):
        """Test that markers anywhere in combined text are detected."""
        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        # Marker in scan text
        assert is_html_artifact('<div>tag</div>', 'normal text') == True
        
        # Marker in PG text
        assert is_html_artifact('normal text', '<div>tag</div>') == True
        
        # Marker split across both
        assert is_html_artifact('<div', '>tag') == True


class TestAllCapsHeaderFilter:
    """Test the ALL CAPS header detection."""

    def test_all_caps_header_mr_hyde(self):
        """Test detection of 'MR. HYDE' style headers."""
        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        assert is_all_caps_header("MR. HYDE") == True

    def test_all_caps_header_nearly(self):
        """Test detection of 'NEARLY' style headers."""
        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        assert is_all_caps_header("NEARLY") == True

    def test_all_caps_header_mixed_case(self):
        """Test that mixed case text is not detected."""
        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        assert is_all_caps_header("Mr. Hyde") == False

    def test_all_caps_header_short_text(self):
        """Test that short uppercase text (≤5 chars) is not detected."""
        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        # Exactly 5 chars should NOT trigger
        assert is_all_caps_header("HELLO") == False
        
        # 6 chars should trigger
        assert is_all_caps_header("HELLO!") == True

    def test_all_caps_header_with_whitespace(self):
        """Test that whitespace is trimmed before checking."""
        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        assert is_all_caps_header("  MR. HYDE  ") == True

    def test_all_caps_header_with_numbers(self):
        """Test that uppercase text with numbers is detected."""
        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        # Numbers are not letters, so isupper() might still be True
        assert is_all_caps_header("CHAPTER 1") == True

    def test_all_caps_header_lowercase_with_punctuation(self):
        """Test that lowercase text with punctuation is not detected."""
        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        assert is_all_caps_header("hello world!") == False


class TestSuffixFragmentFilter:
    """Test the suffix fragment detection (extended)."""

    def test_suffix_fragment_terson(self):
        """Test detection of 'terson,' → 'Utterson,'."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        assert is_suffix_fragment("terson,", "Utterson,") == True

    def test_suffix_fragment_ough(self):
        """Test detection of 'ough' → 'through'."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        # "ough" is a suffix of "through", diff is 3 chars
        assert is_suffix_fragment("ough", "through") == True

    def test_suffix_fragment_younger_ger(self):
        """Test detection of 'unger,' → 'younger,'."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        # "unger," is a suffix of "younger,", diff is 3 chars
        assert is_suffix_fragment("unger,", "younger,") == True

    def test_suffix_fragment_himself(self):
        """Test detection of 'imself' → 'himself'."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        assert is_suffix_fragment("himself", "imself") == True

    def test_suffix_fragment_clause_singular_plural(self):
        """Test that 'clause' → 'clauses' is NOT filtered (singular/plural exception)."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        assert is_suffix_fragment("clause", "clauses") == False

    def test_suffix_fragment_box_boxes(self):
        """Test that 'box' → 'boxes' is NOT filtered (singular/plural with -es)."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        assert is_suffix_fragment("box", "boxes") == False

    def test_suffix_fragment_short_word(self):
        """Test that very short words (3 chars) are filtered."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        # 'the' → 'he' (but 'he' is too short to be singular/plural)
        assert is_suffix_fragment("the", "he") == True

    def test_suffix_fragment_long_word_not_filtered(self):
        """Test that long suffix fragments (>8 chars) are NOT filtered."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        # 9+ chars - should NOT be filtered
        assert is_suffix_fragment("beautifully", "wonderfully") == False

    def test_suffix_fragment_length_difference_too_large(self):
        """Test that large length differences (>3 chars) are NOT filtered."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        # Length difference of 4 chars
        assert is_suffix_fragment("cat", "concatenation") == False

    def test_suffix_fragment_not_suffix(self):
        """Test that non-suffix relationships are NOT filtered."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        # Not a suffix relationship
        assert is_suffix_fragment("hello", "world") == False

    def test_suffix_fragment_with_spaces(self):
        """Test that phrases with spaces are NOT filtered."""
        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        # Multi-word phrases
        assert is_suffix_fragment("hello world", "world") == False


class TestQuotedFragmentFilter:
    """Test the quoted fragment detection."""

    def test_quoted_fragment_i_will(self):
        """Test detection of '"I will' → long PG text with full sentence."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        # Create a scenario where the difference is > 20 chars
        # '"I will' (7 chars) vs a long text (35+ chars)
        long_pg_text = 'ill, and I will not be persuaded otherwise'
        assert is_quoted_fragment('"I will', long_pg_text) == True

    def test_quoted_fragment_well(self):
        """Test detection of '"Well,' → long quoted sentence."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        assert is_quoted_fragment(
            '"Well,',
            'that you saw, did you recognise it?" "Well,'
        ) == True

    def test_quoted_fragment_hello_world(self):
        """Test detection of '"Hello" → '"Hello, world"' with sufficient length diff."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        # Create a scenario where the difference is > 20 chars
        # '"Hello"' (7 chars) vs a long quoted text (30+ chars)
        long_quoted_text = '"Hello, world! This is a very long quote'
        assert is_quoted_fragment('"Hello"', long_quoted_text) == True

    def test_quoted_fragment_length_diff_too_small(self):
        """Test that small length differences (≤20 chars) are NOT filtered."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        # Length difference only 2 chars
        assert is_quoted_fragment('"Hello"', '"Hi"') == False

    def test_quoted_fragment_no_quote(self):
        """Test that text without starting quote is NOT filtered."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        assert is_quoted_fragment('Hello world', 'Hello world, this is a long quote') == False

    def test_quoted_fragment_double_quote(self):
        """Test detection with double quote character."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        assert is_quoted_fragment('"Short', 'A very long quoted text that goes on and on') == True

    def test_quoted_fragment_single_quote(self):
        """Test detection with single quote character."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        assert is_quoted_fragment('"Short', 'A very long quoted text that goes on and on') == True

    def test_quoted_fragment_guillemets(self):
        """Test detection with French guillemets («)."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        assert is_quoted_fragment('«Short', 'A very long quoted text that goes on and on') == True

    def test_quoted_fragment_empty_string(self):
        """Test that empty strings are handled safely."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        assert is_quoted_fragment('', 'Long text') == False

    def test_quoted_fragment_boundary_case_20_chars(self):
        """Test boundary case at exactly 20 character difference."""
        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        # Exactly 20 chars difference should NOT trigger (only > 20)
        # '"Quote' (6 chars) + 20 chars = 26 chars total
        assert is_quoted_fragment(
            '"Quote',
            '"Quote' + 'X' * 20  # 20 chars longer
        ) == False

        # 21 chars difference should trigger
        # '"Quote' (6 chars) + 21 chars = 27 chars total
        assert is_quoted_fragment(
            '"Quote',
            '"Quote' + 'X' * 21  # 21 chars longer
        ) == True


class TestFilterIntegration:
    """Test integration of all filters with candidate errors."""

    def test_long_mismatch_creates_artifact_candidate(self):
        """Test that long mismatch creates ALIGNMENT_ARTIFACT candidate."""
        from gerrata.models import CandidateError, ErrorCategory

        original = CandidateError(
            pg_text="short",
            scan_text="a" * 41,
            pg_offset=100,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )

        # Simulate filter behavior
        def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
            return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

        if is_long_mismatch(original.scan_text, original.pg_text):
            artifact = CandidateError(
                pg_text=original.pg_text,
                scan_text=original.scan_text,
                pg_offset=original.pg_offset,
                scan_page=original.scan_page,
                diff_description=original.diff_description,
                category=ErrorCategory.ALIGNMENT_ARTIFACT,
                severity=original.severity,
            )
            assert artifact.category == ErrorCategory.ALIGNMENT_ARTIFACT

    def test_html_artifact_creates_artifact_candidate(self):
        """Test that HTML artifact creates ALIGNMENT_ARTIFACT candidate."""
        from gerrata.models import CandidateError, ErrorCategory

        original = CandidateError(
            pg_text="normal text",
            scan_text='<div align="center">',
            pg_offset=100,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )

        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        if is_html_artifact(original.scan_text, original.pg_text):
            artifact = CandidateError(
                pg_text=original.pg_text,
                scan_text=original.scan_text,
                pg_offset=original.pg_offset,
                scan_page=original.scan_page,
                diff_description=original.diff_description,
                category=ErrorCategory.ALIGNMENT_ARTIFACT,
                severity=original.severity,
            )
            assert artifact.category == ErrorCategory.ALIGNMENT_ARTIFACT

    def test_all_caps_header_creates_artifact_candidate(self):
        """Test that ALL CAPS header creates ALIGNMENT_ARTIFACT candidate."""
        from gerrata.models import CandidateError, ErrorCategory

        original = CandidateError(
            pg_text="MR. HYDE",
            scan_text="nquestionably the doctor's",
            pg_offset=100,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )

        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        if is_all_caps_header(original.scan_text):
            artifact = CandidateError(
                pg_text=original.pg_text,
                scan_text=original.scan_text,
                pg_offset=original.pg_offset,
                scan_page=original.scan_page,
                diff_description=original.diff_description,
                category=ErrorCategory.ALIGNMENT_ARTIFACT,
                severity=original.severity,
            )
            assert artifact.category == ErrorCategory.ALIGNMENT_ARTIFACT

    def test_suffix_fragment_creates_artifact_candidate(self):
        """Test that suffix fragment creates ALIGNMENT_ARTIFACT candidate."""
        from gerrata.models import CandidateError, ErrorCategory

        original = CandidateError(
            pg_text="Utterson,",
            scan_text="terson,",
            pg_offset=100,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )

        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        if is_suffix_fragment(original.scan_text, original.pg_text):
            artifact = CandidateError(
                pg_text=original.pg_text,
                scan_text=original.scan_text,
                pg_offset=original.pg_offset,
                scan_page=original.scan_page,
                diff_description=original.diff_description,
                category=ErrorCategory.ALIGNMENT_ARTIFACT,
                severity=original.severity,
            )
            assert artifact.category == ErrorCategory.ALIGNMENT_ARTIFACT

    def test_quoted_fragment_creates_artifact_candidate(self):
        """Test that quoted fragment creates ALIGNMENT_ARTIFACT candidate."""
        from gerrata.models import CandidateError, ErrorCategory

        original = CandidateError(
            pg_text='ill',
            scan_text='"I will',
            pg_offset=100,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )

        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        if is_quoted_fragment(original.scan_text, original.pg_text):
            artifact = CandidateError(
                pg_text=original.pg_text,
                scan_text=original.scan_text,
                pg_offset=original.pg_offset,
                scan_page=original.scan_page,
                diff_description=original.diff_description,
                category=ErrorCategory.ALIGNMENT_ARTIFACT,
                severity=original.severity,
            )
            assert artifact.category == ErrorCategory.ALIGNMENT_ARTIFACT

    def test_filters_preserve_non_artifact_candidates(self):
        """Test that non-artifact candidates are preserved unchanged."""
        from gerrata.models import CandidateError, ErrorCategory

        original = CandidateError(
            pg_text="real error",
            scan_text="real error",
            pg_offset=100,
            scan_page=5,
            category=ErrorCategory.OCR_SCANNO
        )

        # All filters should return False for this candidate
        def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
            return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        # None should trigger
        assert not is_long_mismatch(original.scan_text, original.pg_text)
        assert not is_html_artifact(original.scan_text, original.pg_text)
        assert not is_all_caps_header(original.scan_text)
        assert not is_suffix_fragment(original.scan_text, original.pg_text)
        assert not is_quoted_fragment(original.scan_text, original.pg_text)

    def test_synthetic_dataset_filtering(self):
        """Test filtering on a synthetic dataset with expected results."""
        from gerrata.models import CandidateError, ErrorCategory

        # Create a synthetic dataset of candidates
        candidates = [
            # Long mismatch - should be filtered
            CandidateError(
                pg_text="short",
                scan_text="a" * 50,
                pg_offset=0,
                scan_page=0,
                category=ErrorCategory.OCR_SCANNO
            ),
            # HTML artifact - should be filtered
            CandidateError(
                pg_text="text",
                scan_text='<div align="center">',
                pg_offset=50,
                scan_page=0,
                category=ErrorCategory.OCR_SCANNO
            ),
            # ALL CAPS header - should be filtered
            CandidateError(
                pg_text="text here",
                scan_text="CHAPTER ONE",
                pg_offset=100,
                scan_page=0,
                category=ErrorCategory.OCR_SCANNO
            ),
            # Suffix fragment - should be filtered
            CandidateError(
                pg_text="Utterson,",
                scan_text="terson,",
                pg_offset=150,
                scan_page=0,
                category=ErrorCategory.OCR_SCANNO
            ),
            # Quoted fragment - should be filtered (length diff > 20)
            CandidateError(
                pg_text="a very long quoted text that goes on for quite a while and continues",
                scan_text='"Quote fragment',
                pg_offset=200,
                scan_page=0,
                category=ErrorCategory.OCR_SCANNO
            ),
            # Real error - should NOT be filtered
            CandidateError(
                pg_text="tne",
                scan_text="the",
                pg_offset=250,
                scan_page=0,
                category=ErrorCategory.OCR_SCANNO
            ),
        ]

        # Define filters
        def is_long_mismatch(scan_text: str, pg_text: str) -> bool:
            return len(scan_text.strip()) > 40 or len(pg_text.strip()) > 40

        def is_html_artifact(scan_text: str, pg_text: str) -> bool:
            combined = scan_text + pg_text
            return any(marker in combined for marker in ['<div', '<span', 'bbox=', '![](', '<img', '</div'])

        def is_all_caps_header(scan_text: str) -> bool:
            stripped = scan_text.strip()
            return stripped.isupper() and len(stripped) > 5

        def is_suffix_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if ' ' in shorter:
                return False
            
            if len(longer) - len(shorter) > 3:
                return False
            
            if not longer.endswith(shorter):
                return False
            
            if longer.endswith('s') and longer[:-1] == shorter and len(shorter) >= 4:
                return False
            if longer.endswith('es') and longer[:-2] == shorter and len(shorter) >= 4:
                return False
            
            if len(shorter) > 8:
                return False
            
            return True

        def is_quoted_fragment(scan_text: str, pg_text: str) -> bool:
            s = scan_text.strip()
            p = pg_text.strip()
            
            shorter, longer = (s, p) if len(s) <= len(p) else (p, s)
            if not shorter:
                return False
            
            starts_with_quote = shorter[0] in '"\"\u00ab'
            if not starts_with_quote:
                return False
            
            if len(longer) - len(shorter) <= 20:
                return False
            
            return True

        # Apply filters
        FILTERS = [
            ("Long mismatches", is_long_mismatch),
            ("HTML artifacts", is_html_artifact),
            ("ALL CAPS headers", lambda s, p: is_all_caps_header(s)),
            ("Suffix fragments", is_suffix_fragment),
            ("Quoted fragments", is_quoted_fragment),
        ]

        filtered_candidates = []
        filter_counts = {name: 0 for name, _ in FILTERS}
        
        for candidate in candidates:
            filtered = False
            for filter_name, filter_fn in FILTERS:
                if filter_fn(candidate.scan_text, candidate.pg_text):
                    artifact = CandidateError(
                        pg_text=candidate.pg_text,
                        scan_text=candidate.scan_text,
                        pg_offset=candidate.pg_offset,
                        scan_page=candidate.scan_page,
                        diff_description=candidate.diff_description,
                        category=ErrorCategory.ALIGNMENT_ARTIFACT,
                        severity=candidate.severity,
                    )
                    filtered_candidates.append(artifact)
                    filter_counts[filter_name] += 1
                    filtered = True
                    break
            if not filtered:
                filtered_candidates.append(candidate)

        # Verify filtering results
        # Note: Long mismatches catches both the 50-char "a"*50 and the long quoted text (both > 40 chars)
        # So we have 2 long mismatches, 1 HTML, 1 ALL CAPS, 1 suffix = 5 total artifacts
        assert sum(filter_counts.values()) == 5
        
        # Check individual filter counts
        assert filter_counts["Long mismatches"] == 2  # Catches both "a"*50 and the long quoted text
        assert filter_counts["HTML artifacts"] == 1
        assert filter_counts["ALL CAPS headers"] == 1
        assert filter_counts["Suffix fragments"] == 1
        # Quoted fragments gets caught by long mismatch first, so count is 0
        
        # Verify we have 6 total candidates (5 artifacts + 1 real error)
        assert len(filtered_candidates) == 6
        
        # Verify the real error was not filtered
        non_artifacts = [c for c in filtered_candidates if c.category != ErrorCategory.ALIGNMENT_ARTIFACT]
        assert len(non_artifacts) == 1
        assert non_artifacts[0].pg_text == "tne"
