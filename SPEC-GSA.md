# SPEC: Growing-String Anchor (GSA) for Scan Page Alignment

**Author:** Apprentice
**Date:** 2026-05-12
**Target:** `src/gerrata/aligner/vision_aligner.py`
**Tests:** `tests/test_growing_string_anchor.py`

## Problem

The current RETAS anchor strategy finds words that appear exactly *once* in both the scan transcription and PG text, then uses their positions to estimate where the page maps. This fails when:

1. A "unique" word exists in both texts but at the wrong location (cross-chapter false anchor)
2. No single word is unique in both texts (short pages, common vocabulary)
3. OCR noise in the scan prevents exact word matches

Chapter constraints help with (1) but add complexity. A simpler, more deterministic approach: **grow a string from the scan text until it matches exactly once in PG**, and do the same from the PG side. Both strings should converge on the same location.

## Algorithm: Growing-String Anchor (GSA)

### Core Idea

Given a scan transcription and PG text within a search window:

1. **From the scan side:** Take the first N words of the scan transcription, starting at N=1. Find all matches of this string in PG text. Keep growing N until match count reaches 1. Record the PG position.

2. **From the PG side (optional validation):** Starting from the PG position found in step 1, extract the same-length string and check it also appears once in the scan text. If it doesn't match, the anchor is unreliable.

3. **If scan-side never reaches 1 match:** Try growing from the *end* of the scan text (reverse direction) instead. If still no unique match, fall back to brute-force.

4. **If scan-side reaches 0 matches before 1:** The growing string has diverged (OCR error). Back off to the last N that had >0 matches and use the *first* match position, then validate with a longer context window.

### Pseudocode

```python
def _grow_string_anchor(
    trans_norm: str,       # normalized scan transcription
    pg_norm: str,          # normalized PG text (full or search window)
    search_start: int = 0,
    search_end: int = -1,
    min_word_len: int = 3, # skip short words as seed
) -> tuple[int | None, bool]:
    """Grow a string from scan transcription until unique match in PG.
    
    Returns (pg_char_position, is_confident) or (None, False).
    """
    if search_end == -1:
        search_end = len(pg_norm)
    pg_search = pg_norm[search_start:search_end]
    
    trans_words = trans_norm.split()
    if len(trans_words) < 2:
        return None, False
    
    # Skip leading short words
    start_idx = 0
    while start_idx < len(trans_words) and len(trans_words[start_idx]) < min_word_len:
        start_idx += 1
    
    if start_idx >= len(trans_words):
        return None, False
    
    # Try forward growth (from scan start)
    result = _grow_forward(trans_words, start_idx, pg_search)
    
    if result is not None:
        pg_pos, n_words, is_unique = result
        # Validate: does the matched PG region also match in the scan?
        if is_unique and _validate_anchor(pg_search, pg_pos, n_words, trans_norm):
            return search_start + pg_pos, True
        elif not is_unique:
            # Multiple matches — use the first one but mark unconfident
            return search_start + pg_pos, False
    
    # Try backward growth (from scan end)
    result = _grow_backward(trans_words, pg_search)
    if result is not None:
        pg_pos, n_words, is_unique = result
        return search_start + pg_pos, is_unique
    
    return None, False


def _grow_forward(
    trans_words: list[str],
    start_idx: int,
    pg_search: str,
) -> tuple[int, int, bool] | None:
    """Grow word string forward from start_idx until unique match."""
    last_match_count = 0
    last_match_pos = -1
    
    for n in range(1, len(trans_words) - start_idx + 1):
        phrase = " ".join(trans_words[start_idx : start_idx + n])
        
        # Find all matches in PG search region
        positions = _find_all_positions(pg_search, phrase)
        match_count = len(positions)
        
        if match_count == 0:
            # String has diverged (OCR error or edition difference)
            # Return last known position if we had one
            if last_match_count > 0 and last_match_pos >= 0:
                return last_match_pos, n - 1, False  # unconfident
            return None
        
        if match_count == 1:
            return positions[0], n, True  # confident
        
        # match_count > 1: still ambiguous, keep growing
        # Track first match position as fallback
        last_match_count = match_count
        last_match_pos = positions[0]
    
    # Exhausted all words without uniqueness
    if last_match_count > 0:
        return last_match_pos, len(trans_words) - start_idx, False
    return None


def _grow_backward(
    trans_words: list[str],
    pg_search: str,
) -> tuple[int, int, bool] | None:
    """Grow word string backward from end of scan transcription."""
    last_match_count = 0
    last_match_pos = -1
    
    for n in range(1, len(trans_words) + 1):
        end_idx = len(trans_words) - n + 1
        start_idx = max(0, end_idx - n)
        phrase = " ".join(trans_words[start_idx:end_idx])
        
        positions = _find_all_positions(pg_search, phrase)
        match_count = len(positions)
        
        if match_count == 0:
            if last_match_count > 0 and last_match_pos >= 0:
                return last_match_pos, n - 1, False
            return None
        
        if match_count == 1:
            return positions[0], n, True
        
        last_match_count = match_count
        last_match_pos = positions[0]
    
    if last_match_count > 0:
        return last_match_pos, len(trans_words), False
    return None


def _validate_anchor(
    pg_search: str,
    pg_pos: int,
    n_words: int,
    trans_norm: str,
) -> bool:
    """Validate anchor by checking PG region also appears uniquely in scan."""
    pg_words = pg_search[pg_pos:].split()[:n_words]
    if len(pg_words) < 2:
        return False
    pg_phrase = " ".join(pg_words)
    count = _find_all_positions(trans_norm, pg_phrase)
    return len(count) == 1
```

## Integration

### As a new anchor method in `align_transcription_to_pg`

Insert between RETAS and n-gram fallback in the alignment flow:

```
Phase 1: RETAS unique word anchoring (existing, preferred)
Phase 1.5: GSA growing-string anchor (NEW)
Phase 2: N-gram anchoring (existing, fallback)
Phase 3: Brute-force (existing, last resort)
```

When RETAS finds no anchors (`anchor_result is None`), try GSA before falling back to n-gram. If GSA returns a position with `is_confident=True`, use it the same way as the RETAS sliding window — estimate the transcription's PG location and score the match.

If GSA returns `is_confident=False`, still use the position but mark `was_anchored = False` (same as brute-force).

### Changes to `align_transcription_to_pg`

After the RETAS block (around line 1340) and before the n-gram fallback (around line 1362), add:

```python
# ── Phase 1.5: Growing-String Anchor (GSA) ──
if best_score == 0:
    gsa_pos, gsa_confident = self._grow_string_anchor(
        trans_norm, pg_norm,
        search_start=effective_start,
        search_end=effective_end,
    )
    
    if gsa_pos is not None:
        logger.debug(
            f"Page {scan_page}: GSA anchor at pg_norm[{gsa_pos}] "
            f"(confident={gsa_confident})"
        )
        
        # Use GSA position as anchor estimate, then do sliding window
        window_size = int(len(trans_norm) * 1.2)
        best_sliding_score = 0.0
        search_range = 200  # tighter than RETAS since GSA is more precise
        step_size = 25
        
        for offset in range(-search_range, search_range + 1, step_size):
            start_pos = max(0, gsa_pos + offset)
            end_pos = min(len(pg_text), start_pos + window_size)
            pg_window = pg_text[start_pos:end_pos]
            pg_window_norm = normalize_for_matching(pg_window)
            
            if len(pg_window_norm) >= len(trans_norm) * 0.5:
                score = SequenceMatcher(None, trans_norm, pg_window_norm).ratio()
                if score > best_sliding_score:
                    best_sliding_score = score
                    best_pg_start = start_pos
                    best_pg_end = end_pos
        
        if best_sliding_score > self.match_threshold:
            best_pg_raw = pg_text[best_pg_start:best_pg_end]
            best_pg_norm_window = normalize_for_matching(best_pg_raw)
            final_score = SequenceMatcher(None, trans_norm, best_pg_norm_window).ratio()
            
            best_score = final_score * (1.0 + 0.2 * min(1.0, (best_pg_end - best_pg_start) / 500.0))
            best_match_len = best_pg_end - best_pg_start
            best_pg_end = min(best_pg_end, len(pg_text))
            was_anchored = gsa_confident  # only advance constraint if confident
```

## Helper Functions

### `_find_all_positions(text: str, phrase: str) -> list[int]`

Find all occurrences of `phrase` in `text` and return their character positions. This already exists conceptually in `_anchor_transcription` as `find_ngram_positions` — extract it as a standalone helper or reuse.

```python
@staticmethod
def _find_all_positions(text: str, phrase: str) -> list[int]:
    """Find all character positions where phrase occurs in text."""
    positions = []
    start = 0
    while True:
        idx = text.find(phrase, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + 1
    return positions
```

## Test Spec

Create `tests/test_growing_string_anchor.py` with the following test cases:

### `TestFindAllPositions`
- Empty text returns []
- Phrase not in text returns []
- Single occurrence returns [pos]
- Multiple occurrences returns all positions
- Overlapping occurrences (e.g., "aaa" in "aaaaa") returns all

### `TestGrowStringAnchor`
- **Unique from start:** "Call me Ishmael" in a text with other "Call" instances → returns correct unique position
- **Multiple "Call" but unique "Call me Ishmael":** Growing reaches N=3 for uniqueness
- **No unique string:** Common phrases like "it was a" → returns first match, unconfident
- **OCR noise:** "skrimshander" → "skirmshander" → growth hits 0, falls back to last known
- **Empty/short transcription:** Returns None
- **Search window constraint:** Only finds matches within search_start..search_end

### `TestGrowForward`
- Growing from word index 0, reaching uniqueness at N=3
- Growth hits 0 matches, backs off to last valid position
- Growth exhausts all words without uniqueness → returns first match, unconfident

### `TestGrowBackward`
- Growing from end of transcription, reaching uniqueness
- Same edge cases as forward

### `TestValidateAnchor`
- PG region also appears once in scan → True
- PG region appears multiple times in scan → False
- PG region not in scan at all → False

### `TestGSATwinnedWithRETAS` (integration-style, no API calls)
- Set up a VisionAligner with test data
- Verify GSA is called when RETAS finds no anchors
- Verify GSA result is used (or not) based on score threshold
- Test with chapter-constrained search window

### `TestGSAEdgeCases`
- Transcription is a single long word repeated: "the the the the" → None
- Transcription matches at exactly search_start boundary
- Transcription matches at exactly search_end boundary
- Very long transcription (1000+ words) — should still be fast

## Performance Considerations

The growing search calls `text.find()` in a loop. For each page:
- Worst case: scan all N words × search through PG text → O(N × M) where M is PG length
- With chapter constraints: M is reduced to ~3000-44000 chars instead of 1.2M
- `str.find()` is C-optimized, so this is fast in practice
- Expected: < 10ms per page even without constraints

If performance is a concern, the growing loop can be capped at max 20 words (pages rarely need more than 5-8 words for uniqueness).

## Commit Criteria

1. All tests pass (`python3 -m pytest tests/test_growing_string_anchor.py -v`)
2. Existing tests still pass (`python3 -m pytest tests/ -v`)
3. `pip install -e .` succeeds
4. No changes to RETAS logic, n-gram logic, or brute-force logic
5. GSA is only called as an additional fallback between RETAS and n-gram
6. `was_anchored` is set correctly: `True` only when `gsa_confident=True`
7. The `align_all_pages` sequential constraint advancement is unchanged
