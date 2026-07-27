"""Global anchor chain aligner.

Instead of greedy sequential alignment (which accumulates drift),
this module takes a global approach:

1. Extract distinctive word sequences from each page transcription
2. Search for those word sequences in the raw PG text (word-by-word)
3. Use the monotonic ordering constraint (page N must map before page N+1)
4. Find the globally optimal assignment

Key insight: we search in RAW text using word-level matching, not in
normalized text using character-level substring search. This preserves
the correct mapping between match positions and raw PG offsets.

Normalization-based matching has a fatal flaw: removing punctuation
shifts character positions, so a match found at norm_offset N doesn't
correspond to raw_offset N. For a 489K char book, this offset drift
can be thousands of characters, making every alignment wrong.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from book_projects.gerrata.src.gerrata.aligner.vision_aligner import PageTranscription
from book_projects.gerrata.src.gerrata.models import Alignment, AlignmentMethod

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r'\b[a-zA-Z]+\b')


def tokenize_words(text: str) -> list[tuple[int, str]]:
    """Extract words with their raw character positions.
    
    Returns list of (char_offset, lowercase_word) tuples.
    """
    return [(m.start(), m.group().lower()) for m in _WORD_RE.finditer(text)]


def find_word_sequence(
    pg_word_positions: list[tuple[int, str]],
    target_words: list[str],
    start_from: int = 0,
) -> list[tuple[int, int]]:
    """Find all occurrences of a word sequence in PG text.
    
    Args:
        pg_word_positions: Pre-computed [(char_offset, word), ...] from tokenize_words().
        target_words: Words to search for (lowercase).
        start_from: Minimum PG word index to start searching from.
    
    Returns:
        List of (pg_char_offset, pg_word_index) tuples for each match start.
    """
    if not target_words or not pg_word_positions:
        return []
    
    matches = []
    n_targets = len(target_words)
    n_pg = len(pg_word_positions)
    
    for i in range(start_from, n_pg - n_targets + 1):
        match = True
        for j in range(n_targets):
            if pg_word_positions[i + j][1] != target_words[j]:
                match = False
                break
        if match:
            matches.append((pg_word_positions[i][0], i))
    
    return matches


def extract_distinctive_phrases(
    text: str,
    min_words: int = 8,
    max_phrases: int = 5,
    skip_chars: int = 60,
) -> list[list[str]]:
    """Extract distinctive word sequences from text.
    
    Returns:
        List of phrases, where each phrase is a list of lowercase words.
    """
    if not text or len(text) < skip_chars + 20:
        return []
    
    body = text[skip_chars:]
    
    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', body)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 30]
    
    if not sentences:
        sentences = re.split(r';\s+', body)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 40]
    
    common_words = {
        "the", "a", "an", "and", "of", "to", "in", "is", "was", "it", "that",
        "he", "she", "his", "her", "they", "their", "them", "but", "for", "with",
        "not", "all", "were", "had", "has", "been", "from", "this", "which",
        "or", "as", "at", "be", "by", "on", "do", "if", "so", "no", "we",
        "would", "could", "should", "may", "one", "into", "more", "than",
        "its", "him", "my", "me", "your", "what", "when", "there", "each",
        "very", "much", "any", "some", "up", "out", "about", "how", "other",
    }
    
    scored = []
    for sent in sentences:
        words = [m.group().lower() for m in _WORD_RE.finditer(sent)]
        if len(words) < min_words:
            continue
        
        uncommon = sum(1 for w in words if w not in common_words and len(w) > 3)
        score = len(words) + uncommon * 2
        scored.append((words, score))
    
    scored.sort(key=lambda x: x[1], reverse=True)
    
    phrases = []
    for words, score in scored[:max_phrases * 2]:
        if len(words) > 25:
            words = words[:20]
        if len(words) >= min_words:
            phrases.append(words)
    
    return phrases[:max_phrases]


def build_monotonic_assignment(
    page_candidates: dict[int, list[tuple[int, float]]],
    n_pages: int = 0,
) -> dict[int, int]:
    """Find the best monotonic assignment of pages to PG positions.
    
    For each page in order, pick the candidate position that:
    1. Is >= the previous page's assigned position
    2. Is most consistent with expected spacing from confirmed assignments
    3. Has the highest match score
    """
    if not page_candidates:
        return {}
    
    sorted_pages = sorted(page_candidates.keys())
    assignment: dict[int, int] = {}
    prev_pos = 0
    confirmed = []
    
    for page_num in sorted_pages:
        candidates = page_candidates[page_num]
        if not candidates:
            continue
        
        # Filter to monotonic candidates
        valid = [(pos, score) for pos, score in candidates if pos >= prev_pos]
        if not valid:
            valid = [(pos, score) for pos, score in candidates if pos >= prev_pos - 200]
        if not valid:
            valid = candidates[:1]
        
        # Expected position from confirmed assignments
        expected = prev_pos
        if len(confirmed) >= 3:
            recent = confirmed[-5:]
            total_chars = recent[-1][1] - recent[0][1]
            total_pages = recent[-1][0] - recent[0][0]
            if total_pages > 0:
                cpp = total_chars / total_pages
                pages_gap = page_num - confirmed[-1][0]
                expected = confirmed[-1][1] + cpp * pages_gap
        
        # Pick best candidate
        best = None
        best_combined = -float('inf')
        for pos, score in valid:
            if len(confirmed) >= 3 and expected > 0:
                deviation = abs(pos - expected)
                recent = confirmed[-5:]
                cpp = max(500, (recent[-1][1] - recent[0][1]) / max(1, recent[-1][0] - recent[0][0]))
                pos_score = max(0.0, 1.0 - deviation / (cpp * 4))
            else:
                pos_score = 1.0
            
            combined = score * 0.7 + pos_score * 0.3
            if combined > best_combined:
                best_combined = combined
                best = (pos, score)
        
        if best:
            assignment[page_num] = best[0]
            prev_pos = best[0]
            confirmed.append((page_num, best[0]))
    
    return assignment


def score_window(trans_text: str, pg_text: str, start: int, end: int) -> float:
    """Score how well transcription matches a PG text window using word-level SequenceMatcher."""
    if start < 0 or end > len(pg_text) or start >= end:
        return 0.0
    
    pg_window = pg_text[start:end]
    
    trans_words = [m.group().lower() for m in _WORD_RE.finditer(trans_text)]
    pg_words = [m.group().lower() for m in _WORD_RE.finditer(pg_window)]
    
    if not trans_words or not pg_words:
        return 0.0
    
    return SequenceMatcher(None, trans_words, pg_words).ratio()


class GlobalAnchorAligner:
    """Align page transcriptions to PG text using global word-sequence matching.
    
    Pipeline:
    1. Tokenize PG text into words with positions (once)
    2. Extract distinctive word sequences from each page transcription
    3. Find all occurrences of each word sequence in PG text
    4. Build monotonic assignment (page order = text order)
    5. Compute alignment windows with multi-scale scoring
    6. Score and filter
    """
    
    def __init__(
        self,
        min_phrase_words: int = 8,
        max_phrases_per_page: int = 5,
        min_score: float = 0.35,
    ):
        self.min_phrase_words = min_phrase_words
        self.max_phrases_per_page = max_phrases_per_page
        self.min_score = min_score
    
    def align_all_pages(
        self,
        transcriptions: list[PageTranscription],
        pg_text: str,
        pg_paragraphs: list[str] | None = None,
        chapters: list | None = None,
    ) -> list[Alignment]:
        """Align all page transcriptions using global phrase matching.
        
        Returns list of Alignment objects sorted by pg_start.
        """
        logger.info(f"Global anchor alignment: {len(transcriptions)} pages, "
                     f"{len(pg_text):,} chars PG text")
        
        # Step 1: Tokenize PG text ONCE
        logger.info("Tokenizing PG text...")
        pg_words = tokenize_words(pg_text)
        logger.info(f"PG text: {len(pg_words):,} words")
        
        # Step 2: Extract phrases and find hits for each page
        page_candidates: dict[int, list[tuple[int, float]]] = {}
        page_trans_map: dict[int, PageTranscription] = {}
        
        for trans in transcriptions:
            if not trans.success or not trans.transcription:
                continue
            
            text = trans.transcription_cleaned or trans.transcription
            if len(text) < 50:
                continue
            
            page_trans_map[trans.page_num] = trans
            
            phrases = extract_distinctive_phrases(
                text,
                min_words=self.min_phrase_words,
                max_phrases=self.max_phrases_per_page,
                skip_chars=60,
            )
            
            if not phrases:
                logger.debug(f"Page {trans.page_num}: no distinctive phrases")
                continue
            
            all_hits = []
            for words in phrases:
                hits = find_word_sequence(pg_words, words)
                for char_offset, word_idx in hits:
                    score = min(1.0, len(words) / 12.0)
                    all_hits.append((char_offset, score))
            
            if all_hits:
                seen = set()
                unique_hits = []
                for pos, score in sorted(all_hits):
                    if pos not in seen:
                        seen.add(pos)
                        unique_hits.append((pos, score))
                page_candidates[trans.page_num] = unique_hits
            else:
                logger.debug(f"Page {trans.page_num}: no hits in PG text")
        
        logger.info(f"Pages with phrase hits: {len(page_candidates)}/{len(transcriptions)}")
        
        # Step 3: Build monotonic assignment
        assignment = build_monotonic_assignment(page_candidates)
        logger.info(f"Monotonic assignment: {len(assignment)} pages")
        
        if len(assignment) < 3:
            logger.warning(f"Too few anchored pages ({len(assignment)})")
        
        # Step 4: Compute alignment windows
        alignments = self._compute_alignments(assignment, page_trans_map, pg_text)
        
        alignments.sort(key=lambda a: a.pg_start)
        
        covered = sum(a.pg_end - a.pg_start for a in alignments)
        coverage = covered / len(pg_text) if pg_text else 0
        logger.info(
            f"Global alignment complete: {len(alignments)} pages, "
            f"coverage={coverage:.1%}"
        )
        
        return alignments
    
    def _compute_alignments(
        self,
        assignment: dict[int, int],
        page_trans_map: dict[int, PageTranscription],
        pg_text: str,
    ) -> list[Alignment]:
        """Compute alignment windows from page assignments.
        
        For each page, try multiple window sizes centered on the phrase position,
        bounded by neighboring pages. Pick the best-scoring window.
        """
        sorted_pages = sorted(assignment.keys())
        if not sorted_pages:
            return []
        
        # Compute average trans length and PG/Trans ratio
        trans_lengths: dict[int, int] = {}
        for p in sorted_pages:
            t = page_trans_map.get(p)
            if t and t.success:
                text = t.transcription_cleaned or t.transcription or ""
                trans_lengths[p] = len(text)
        
        sample = [v for v in trans_lengths.values() if v > 0][:10]
        avg_trans = sum(sample) / len(sample) if sample else 1500
        n_assigned = len(sorted_pages)
        avg_pg_page = len(pg_text) / max(n_assigned, 1)
        ratio = avg_pg_page / avg_trans if avg_trans > 0 else 1.2
        
        alignments = []
        
        for i, page_num in enumerate(sorted_pages):
            phrase_pos = assignment[page_num]
            trans_len = trans_lengths.get(page_num, 1500)
            
            t = page_trans_map.get(page_num)
            if not t or not t.success:
                continue
            
            text = t.transcription_cleaned or t.transcription or ""
            if len(text) < 50:
                continue
            
            # Compute neighbor boundaries (hard limits)
            lower_bound = 0
            upper_bound = len(pg_text)
            
            if i > 0 and sorted_pages[i - 1] in assignment:
                prev_phrase = assignment[sorted_pages[i - 1]]
                lower_bound = (prev_phrase + phrase_pos) // 2
            
            if i < len(sorted_pages) - 1 and sorted_pages[i + 1] in assignment:
                next_phrase = assignment[sorted_pages[i + 1]]
                upper_bound = (phrase_pos + next_phrase) // 2
            
            # Try multiple window scales, pick best
            # Use tighter windows centered on the phrase position
            est_pg_len = int(trans_len * ratio)
            
            best_score = -1
            best_start = phrase_pos
            best_end = phrase_pos + est_pg_len
            
            for scale in [0.7, 0.85, 1.0, 1.15]:
                wlen = int(est_pg_len * scale)
                # Center window on phrase (not assuming phrase offset)
                w_start = phrase_pos - wlen // 2
                w_end = w_start + wlen
                
                # Clamp to neighbor boundaries
                w_start = max(lower_bound, w_start)
                w_end = min(upper_bound, w_end)
                
                if w_end - w_start < 200:
                    continue
                
                s = score_window(text, pg_text, w_start, w_end)
                if s > best_score:
                    best_score = s
                    best_start = w_start
                    best_end = w_end
            
            if best_score < self.min_score:
                logger.debug(
                    f"Page {page_num}: best score {best_score:.3f} < {self.min_score}"
                )
                continue
            
            alignment = Alignment(
                pg_start=best_start,
                pg_end=best_end,
                scan_page=page_num,
                scan_image_path=str(t.image_path) if t.image_path else None,
                confidence=best_score,
                method=AlignmentMethod.LLM_VISION,
            )
            alignments.append(alignment)
            
            logger.info(
                f"Page {page_num}: [{best_start}:{best_end}] score={best_score:.3f}"
            )
        
        return alignments
    
    def alignment_confidence(self, alignments: list, pg_text_len: int) -> float:
        """Calculate overall alignment coverage."""
        if pg_text_len == 0:
            return 0.0
        covered = sum(a.pg_end - a.pg_start for a in alignments)
        return min(1.0, covered / pg_text_len)
