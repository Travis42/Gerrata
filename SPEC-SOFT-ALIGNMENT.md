# SPEC: Soft Alignment — Fallback-Widening Alignment Pipeline

**Date:** 2026-05-12
**Status:** Approved
**Target file:** `src/gerrata/aligner/vision_aligner.py`

## Problem

The current alignment pipeline uses chapter boundaries as **hard constraints** — once a chapter is detected on a scan page, the search window is locked to `[chapter.offset : chapter.end_offset]`. This causes two classes of failure:

1. **Edition-variant chapter mismatch:** The scan and PG editions have different chapter structures. A scan page heading "Midnight, Forecastle" matches PG chapter 122 (offset 1,085,327), but the actual content is at offset 379,618 in chapter "HARPOONEERS AND SAILORS." Pages constrained to the wrong chapter fail silently.

2. **Sequential drift:** The chapter tracker (`current_chapter_idx`) advances based on heading detection and (previously) anchored match positions. Small errors compound, causing later pages to be constrained to wrong chapters.

**Current best result:** 202/340 pages matched (59% coverage) on Moby Dick, despite all 340 pages matching successfully when tested individually with unconstrained search.

## Core Principle

> **Chapters are hints, not walls. Content is ground truth.**

If a page matches in standalone (unconstrained) mode, it MUST match in pipeline mode. The chapter constraint is a performance optimization that should NEVER cause a silent failure.

## Solution: Three-Phase Fallback Widening

### Phase 1: Chapter-Constrained Search (fast, usually correct)

Try alignment within the chapter window detected from the scan page heading.

- **Search window:** `[chapter.offset : chapter.end_offset]` (current behavior)
- **Accept threshold:** score ≥ `CHAPTER_THRESHOLD` (0.50 — higher bar to reject uncertain matches)
- **If accepted:** Record as HIGH confidence match, update sequential tracker
- **If rejected (or no chapter detected):** Fall through to Phase 2

### Phase 2: Sequential Neighborhood Search (medium speed, covers drift)

Try alignment in a window centered on the expected position based on recent confirmed matches.

- **Expected position:** Calculated from the last confirmed match using linear interpolation (see Sequential Tracker below)
- **Search window:** `[expected_pos - margin : expected_pos + forward_margin]`
  - `margin = 2000` chars (generous backward lookback)
  - `forward_margin = 8000` chars (very generous forward — chapters can be 30K+ chars)
- **Accept threshold:** score ≥ `SEQUENTIAL_THRESHOLD` (0.40)
- **If accepted:** Record as MEDIUM confidence match, update sequential tracker
- **If rejected:** Fall through to Phase 3

### Phase 3: Global Search (slowest, guaranteed coverage)

Search the entire PG text with no constraints.

- **Search window:** `[0 : len(pg_text)]`
- **Accept threshold:** score ≥ `GLOBAL_THRESHOLD` (0.35 — current baseline)
- **If accepted:** Record as LOW confidence match, update sequential tracker
- **If rejected:** Page truly fails — log at INFO level (not DEBUG)

### Score Thresholds Summary

| Phase | Search scope | Min score | Confidence label |
|-------|-------------|-----------|-----------------|
| 1     | Chapter window | 0.50 | HIGH |
| 2     | Sequential neighborhood | 0.40 | MEDIUM |
| 3     | Global | 0.35 | LOW |

These thresholds are **minimum scores for acceptance, not search termination.** Within each phase, the aligner still uses its existing RETAS → GSA → n-gram → brute-force cascade. The phase just determines the search window.

## Sequential Tracker

Replace the current `search_start` integer with a lightweight tracker that maintains:

```python
@dataclass
class SequentialTracker:
    matches: list[tuple[int, int, float, int]]  # (page_num, pg_offset, confidence, match_length)
    
    @property
    def last_confirmed_position(self) -> int:
        """Most recent HIGH or MEDIUM confidence match end."""
        for page_num, offset, confidence, match_length in reversed(self.matches):
            if confidence >= 0.40:  # HIGH or MEDIUM
                return offset + match_length
        return 0
    
    @property
    def chars_per_page(self) -> float:
        """Estimated chars per page from recent confirmed matches."""
        recent = [(pn, off) for pn, off, conf, ml in self.matches if conf >= 0.40]
        if len(recent) < 2:
            return 1500.0  # Default estimate
        # Use last 10 confirmed matches
        recent = recent[-10:]
        total_chars = recent[-1][1] - recent[0][1]
        total_pages = recent[-1][0] - recent[0][0]
        return max(500.0, total_chars / max(1, total_pages))
    
    def expected_position(self, page_num: int) -> int:
        """Estimate where page_num's content should start."""
        last_pos = self.last_confirmed_position
        # Estimate: last confirmed end + (pages_gap * chars_per_page)
        # Find the page number of the last confirmed match
        last_page = 0
        for pn, off, conf, ml in self.matches:
            if conf >= 0.40:
                last_page = pn
        pages_gap = page_num - last_page
        return last_pos + int(pages_gap * self.chars_per_page)
    
    def record(self, page_num: int, pg_end: int, confidence: float, match_length: int):
        self.matches.append((page_num, pg_end, confidence, match_length))
    
    def search_window(self, page_num: int, pg_text_length: int) -> tuple[int, int]:
        expected = self.expected_position(page_num)
        backward_margin = 2000
        forward_margin = 8000
        start = max(0, expected - backward_margin)
        end = min(pg_text_length, expected + forward_margin)
        return (start, end)
```

**Key property:** The tracker self-calibrates. After a few confirmed matches, `chars_per_page` converges to the actual rate for the current book. No manual tuning needed per book.

## Chapter Tracker: Content-Derived, Not Heading-Derived

### Current behavior (WRONG)
1. Detect heading on scan page → match to PG chapter list → set search window → hope for the best

### New behavior
1. Detect heading on scan page → match to PG chapter list → use as Phase 1 search window (hint only)
2. After a match succeeds in ANY phase → infer current chapter from match position:
   ```python
   # After successful match at position pg_start:
   for i, ch in enumerate(chapters):
       if ch.offset <= pg_start < ch.end_offset:
           current_chapter_idx = i
           break
   ```
3. Heading detection is only used to narrow the Phase 1 window. It NEVER overrides a confirmed match's position.

### Heading detection still useful for Phase 1
When the heading detection and sequential tracker agree (windows overlap), Phase 1 with chapter constraints is fast and accurate. The disagreement case is what causes failures — and Phase 2/3 catch those.

### Handling "chapter detected but no content match within chapter"
This is the "Midnight, Forecastle" case. Phase 1 tries the chapter window, RETAS finds no anchors (because the content isn't there), score < 0.50 → fall through to Phase 2. Phase 2 uses sequential neighborhood, finds the content at the correct offset. Done.

## Backward Repair Pass

After the forward pass, run a second pass to fill remaining gaps:

```python
def repair_pass(
    results: list[Optional[VisionAlignmentResult]],
    transcriptions: list[PageTranscription],
    pg_text: str,
    pg_paragraphs: list[str],
    tracker: SequentialTracker,
) -> list[Optional[VisionAlignmentResult]]:
    """Fill gaps using bilateral constraints from neighboring matches."""
    repaired = list(results)
    changed = True
    while changed:
        changed = False
        for i, result in enumerate(repaired):
            if result is not None:
                continue
            # Find nearest confirmed match before and after
            before = None
            after = None
            for j in range(i - 1, -1, -1):
                if repaired[j] is not None:
                    before = repaired[j]
                    break
            for j in range(i + 1, len(repaired)):
                if repaired[j] is not None:
                    after = repaired[j]
                    break
            
            if before and after:
                # Tight window between two known positions
                window_start = max(0, before.alignment.pg_end - 500)
                window_end = min(len(pg_text), after.alignment.pg_start + 500)
            elif before:
                # Only before — search forward from last match
                window_start = max(0, before.alignment.pg_end - 500)
                window_end = min(len(pg_text), window_start + 10000)
            elif after:
                # Only after — search backward from next match
                window_end = after.alignment.pg_start + 500
                window_start = max(0, window_end - 10000)
            else:
                continue
            
            result = self.align_transcription_to_pg(
                transcription=transcriptions[i],
                pg_text=pg_text,
                pg_paragraphs=pg_paragraphs,
                scan_page=transcriptions[i].page_num,
                search_start=window_start,
                search_end=window_end,
            )
            if result and result.best_score >= 0.35:
                repaired[i] = result
                tracker.record(transcriptions[i].page_num, 
                             result.alignment.pg_end, 0.35,
                             result.alignment.pg_end - result.alignment.pg_start)
                changed = True
    
    return repaired
```

The `while changed` loop handles cascading repairs: filling one gap may enable filling the next.

## Logging Changes

All page-level events logged at **INFO** level. No silent failures.

```python
# Phase 1 attempt
logger.info(f"Page {num}: Phase 1 (chapter) [{ch_start}:{ch_end}]")

# Phase 2 attempt (only if Phase 1 failed)
logger.info(f"Page {num}: Phase 2 (sequential) [{seq_start}:{seq_end}]")

# Phase 3 attempt (only if Phase 2 failed)
logger.info(f"Page {num}: Phase 3 (global) [0:{len(pg_text)}]")

# Success
logger.info(f"Page {num}: matched PG [{pg_start}:{pg_end}] score={score:.2f} conf={confidence_label}")

# True failure (all phases exhausted)
logger.info(f"Page {num}: NO MATCH in any phase")
```

## Changes to `align_transcription_to_pg`

**Minimal changes.** This method already supports `search_start` and `search_end` parameters. The three-phase widening is implemented in `align_all_pages`, which calls `align_transcription_to_pg` with different search windows.

The only change to `align_transcription_to_pg`: add an optional `min_score` parameter (default 0.35) so the caller can enforce phase-specific thresholds:

```python
def align_transcription_to_pg(
    self,
    transcription: PageTranscription,
    pg_text: str,
    pg_paragraphs: list[str],
    scan_page: int = 0,
    search_start: int = 0,
    search_end: int = -1,
    min_score: float = 0.35,  # NEW: minimum score to accept
) -> VisionAlignmentResult | None:
```

If `best_score < min_score` at the end of the method, return `None` instead of the result.

## Changes to `align_all_pages` — Full Rewrite

The method body changes significantly. Here's the pseudocode:

```python
def align_all_pages(self, transcriptions, pg_text, pg_paragraphs, chapters=None):
    results = [None] * len(transcriptions)
    tracker = SequentialTracker()
    
    body_offset = find_body_start(pg_text)
    
    # Parse chapters (existing logic)
    real_chapters = filter_real_chapters(chapters) if chapters else []
    current_chapter_idx = find_first_chapter_at_or_after(real_chapters, body_offset)
    
    # ── Forward pass: three-phase alignment ──
    for i, trans in enumerate(transcriptions):
        page_num = trans.page_num
        
        # Skip duplicates (existing logic)
        if is_duplicate(trans, results):
            continue
        
        # Get Phase 1 window (chapter-constrained)
        heading = self._detect_scan_chapter(trans.transcription)
        ch_window = None
        if heading and real_chapters and current_chapter_idx >= 0:
            match_idx = self._match_chapter_heading(heading, real_chapters, current_chapter_idx)
            if match_idx is not None and match_idx >= current_chapter_idx - 2:
                ch = real_chapters[match_idx]
                ch_window = (ch.offset, ch.end_offset)
                current_chapter_idx = match_idx
                logger.info(f"Page {page_num}: Phase 1 (chapter '{heading}') [{ch.offset}:{ch.end_offset}]")
        
        # Phase 1: Chapter-constrained
        if ch_window:
            result = self.align_transcription_to_pg(
                transcription=trans, pg_text=pg_text, pg_paragraphs=pg_paragraphs,
                scan_page=page_num, search_start=ch_window[0], search_end=ch_window[1],
                min_score=0.50,
            )
            if result:
                results[i] = result
                tracker.record(page_num, result.alignment.pg_end, 0.50, 
                             result.alignment.pg_end - result.alignment.pg_start)
                # Update chapter from match position (content-derived)
                self._update_chapter_from_position(
                    result.alignment.pg_start, real_chapters, current_chapter_idx
                )
                logger.info(f"Page {page_num}: Phase 1 match [{result.alignment.pg_start}:{result.alignment.pg_end}] "
                          f"score={result.best_score:.2f} HIGH")
                continue
        
        # Phase 2: Sequential neighborhood
        seq_window = tracker.search_window(page_num, len(pg_text))
        logger.info(f"Page {page_num}: Phase 2 (sequential) [{seq_window[0]}:{seq_window[1]}]")
        result = self.align_transcription_to_pg(
            transcription=trans, pg_text=pg_text, pg_paragraphs=pg_paragraphs,
            scan_page=page_num, search_start=seq_window[0], search_end=seq_window[1],
            min_score=0.40,
        )
        if result:
            results[i] = result
            tracker.record(page_num, result.alignment.pg_end, 0.40,
                         result.alignment.pg_end - result.alignment.pg_start)
            self._update_chapter_from_position(
                result.alignment.pg_start, real_chapters, current_chapter_idx
            )
            logger.info(f"Page {page_num}: Phase 2 match [{result.alignment.pg_start}:{result.alignment.pg_end}] "
                      f"score={result.best_score:.2f} MEDIUM")
            continue
        
        # Phase 3: Global search
        logger.info(f"Page {page_num}: Phase 3 (global) [0:{len(pg_text)}]")
        result = self.align_transcription_to_pg(
            transcription=trans, pg_text=pg_text, pg_paragraphs=pg_paragraphs,
            scan_page=page_num, search_start=0, search_end=len(pg_text),
            min_score=0.35,
        )
        if result:
            results[i] = result
            tracker.record(page_num, result.alignment.pg_end, 0.35,
                         result.alignment.pg_end - result.alignment.pg_start)
            self._update_chapter_from_position(
                result.alignment.pg_start, real_chapters, current_chapter_idx
            )
            logger.info(f"Page {page_num}: Phase 3 match [{result.alignment.pg_start}:{result.alignment.pg_end}] "
                      f"score={result.best_score:.2f} LOW")
            continue
        
        # True failure
        logger.info(f"Page {page_num}: NO MATCH in any phase")
    
    # ── Backward repair pass ──
    results = self._repair_pass(results, transcriptions, pg_text, pg_paragraphs, tracker)
    
    # Filter out None results
    return [r for r in results if r is not None]
```

## New Helper Methods

### `_update_chapter_from_position(pg_start, chapters, current_idx)`
```python
def _update_chapter_from_position(self, pg_start, chapters, current_idx):
    """Derive current chapter from match position instead of heading."""
    if not chapters:
        return
    for i, ch in enumerate(chapters):
        if ch.offset <= pg_start < ch.end_offset:
            if i != current_idx:
                logger.debug(
                    f"  Chapter tracker corrected: {current_idx + 1} → {i + 1} "
                    f"({ch.title}) from match position {pg_start}"
                )
            return i
    return current_idx
```

Note: This returns the new index but does NOT update `current_chapter_idx` in `align_all_pages`. The caller must do: `current_chapter_idx = self._update_chapter_from_position(...)`.

## Files to Modify

1. **`src/gerrata/aligner/vision_aligner.py`**
   - Add `SequentialTracker` dataclass (before `VisionAligner` class)
   - Add `min_score` parameter to `align_transcription_to_pg()`
   - Rewrite `align_all_pages()` with three-phase fallback
   - Add `_update_chapter_from_position()` helper
   - Add `_repair_pass()` method to `VisionAligner`
   - Change all `logger.debug(f"Page ... no match found")` to `logger.info(f"Page ... NO MATCH in any phase")`

2. **No changes to tests needed for initial implementation** — existing tests test `align_transcription_to_pg` in isolation (no chapter constraints). New behavior is in `align_all_pages` which has no unit tests yet.

## Success Criteria

- **Coverage:** ≥ 300/340 pages matched on Moby Dick (88%+)
- **Accuracy:** ≤ 55 errors (maintain or improve v5's 49 errors)
- **No silent failures:** Every page produces an INFO-level log line
- **Generalizable:** No Moby-Dick-specific logic (no hardcoded page numbers, chapter names, or scan IDs)
- **Performance:** Total alignment time ≤ 2x current (Phase 3 global search only runs for ~50-100 pages)

## Constants

```python
CHAPTER_THRESHOLD = 0.50    # Min score for chapter-constrained match
SEQUENTIAL_THRESHOLD = 0.40  # Min score for sequential neighborhood match  
GLOBAL_THRESHOLD = 0.35      # Min score for global search (current baseline)
SEQUENTIAL_BACKWARD_MARGIN = 2000   # Chars to look back from expected position
SEQUENTIAL_FORWARD_MARGIN = 8000    # Chars to look forward from expected position
DEFAULT_CHARS_PER_PAGE = 1500       # Fallback chars/page estimate
MIN_CONFIDENCE_FOR_TRACKER = 0.40   # Only HIGH/MEDIUM matches update tracker
```

## What NOT to Change

- RETAS, GSA, n-gram, brute-force cascade inside `align_transcription_to_pg` — leave as-is
- `_detect_scan_chapter()` and `_match_chapter_heading()` — leave as-is
- `find_body_start()`, `strip_paratext()`, `normalize_for_matching()` — leave as-is
- Duplicate page detection logic — keep as-is
- All other methods on `VisionAligner` — leave as-is
