# Edition Comparison Specification

**Project:** `/root/projects/gerrata/`
**Created:** 2026-05-29
**Status:** Spec

---

## 1. Objective

Enable Gerrata to compare two (or more) scanned book editions against each other, producing a scholarly collation-style report of textual variants. This extends Gerrata from "PG text vs scan" to "any text vs any text."

**Core insight:** Gerrata's pipeline is already generic — align, diff, filter, verify. The only thing hardcoded to PG is the input source. This spec adds edition-to-edition as a first-class input mode.

---

## 2. New Concepts

### 2.1 Variant vs Error

The fundamental semantic shift:

- **Error:** PG text is wrong, scan is correct. One truth, one defect.
- **Variant:** Edition A says X, Edition B says Y. Both may be intentional. Neither is necessarily "wrong."

This changes the framing of every downstream stage. We're not auditing — we're collating.

### 2.2 Variant Categories

```
VARIANT_CATEGORIES = {
    "textual_variant",       # Substantive wording difference (word changes, additions, deletions)
    "punctuation_variant",   # Comma, period, dash, quote differences
    "spelling_change",       # "connexion" → "connection", "shew" → "show"
    "normalization",         # Whitespace, hyphenation, linebreak differences
    "missing_content",      # Text present in A, absent in B
    "added_content",         # Text present in B, absent in A
    "formatting_variant",    # Italic, bold, smallcaps differences (if detectable)
    "linebreak_variant",    # Same words, different lineation only
}
```

### 2.3 Variant Significance

Not all variants matter equally. A significance score helps readers focus:

| Significance | Description | Example |
|---|---|---|
| **Major** | Changes meaning, characterization, plot | "He said nothing" vs "He said everything" |
| **Moderate** | Changes style, rhythm, emphasis | Comma placement, word order |
| **Minor** | Changes spelling, formatting, normalization | "connexion" → "connection" |
| **Trivial** | Changes whitespace, lineation only | Different paragraph breaks |

---

## 3. Architecture

### 3.1 Pipeline Overview

```
Edition A (scan or text)  ──transcribe──→  Transcription A
                                              │
                                    ┌─────────┴─────────┐
                                    │  Cross-Alignment  │
                                    │  (anchor aligner) │
                                    └─────────┬─────────┘
                                              │
Edition B (scan or text)  ──transcribe──→  Transcription B
                                              │
                                    ┌─────────┴─────────┐
                                    │   Word-Level Diff │
                                    │  (text_diff.py)   │
                                    └─────────┬─────────┘
                                              │
                                    ┌─────────┴─────────┐
                                    │  Variant Classifier│
                                    │  (NEW)            │
                                    └─────────┬─────────┘
                                              │
                                    ┌─────────┴─────────┐
                                    │  Significance Score│
                                    │  (NEW)            │
                                    └─────────┬─────────┘
                                              │
                                    ┌─────────┴─────────┐
                                    │  Comparison Report│
                                    │  (NEW)            │
                                    └─────────┬─────────┘
                                              │
                                    ┌─────────┴─────────┐
                                    │  Export Formats   │
                                    │  JSON, Markdown,  │
                                    │  TEI-XML          │
                                    └───────────────────┘
```

### 3.2 Input Modes

The comparison accepts three input combinations:

**Mode 1: Scan vs Scan** — Both editions from IA scans
```bash
gerrata compare-editions \
  --edition-a scan:2021.148755.Nostromo \
  --edition-b scan:2021.148755.2ndEd \
  --output ./reports/nostromo-1st-vs-2nd/
```

**Mode 2: Scan vs Text** — One scan, one local text file (PG text, plaintext, etc.)
```bash
gerrata compare-editions \
  --edition-a scan:2021.148755.Nostromo \
  --edition-b text:/path/to/clean-text.txt \
  --output ./reports/
```

**Mode 3: Text vs Text** — Two local text files
```bash
gerrata compare-editions \
  --edition-a text:/path/edition1.txt \
  --edition-b text:/path/edition2.txt \
  --output ./reports/
```

### 3.3 Input Source Parsing

Each `--edition-*` arg is parsed as `<type>:<identifier>`:

```
scan:<ia_identifier>    → Fetch JP2 zip from IA, extract pages, transcribe with vision
scan:<path-to-jp2-zip>  → Extract pages from local zip, transcribe
pages:<directory>       → Use pre-extracted page images, transcribe
transcribed:<json-file> → Load previously cached transcriptions (skip transcription)
text:<file-path>        → Load local text file directly (split into "pages" by paragraph count)
```

The `text:` mode needs synthetic "page" boundaries. Strategy: split into chunks of ~2000 chars at paragraph boundaries. These aren't real pages, but the aligner works on any chunked text.

---

## 4. New Components

### 4.1 Cross-Aligner (`aligner/cross_aligner.py`)

The existing `GlobalAnchorAligner` aligns page transcriptions against a base text. For edition comparison, we need to align two sets of transcriptions against each other.

**Approach:** Use edition A's full transcribed text as the "base text" and edition B's page transcriptions as the "pages." The existing global anchor aligner handles this directly — no algorithm changes needed.

**New wrapper function:**
```python
def align_editions(
    transcriptions_a: list[PageTranscription],
    transcriptions_b: list[PageTranscription],
    min_phrase_words: int = 8,
    min_score: float = 0.35,
) -> list[EditionAlignment]:
    """
    Align two editions' transcriptions against each other.
    
    Returns list of (edition_a_range, edition_b_range, confidence) tuples,
    mapping regions of edition A to corresponding regions of edition B.
    """
```

**What about structural differences?** If edition B has a new chapter or appendix, the aligner will simply fail to match those pages — they'll appear as "unmatched" in the report, which is itself a finding (added content).

**Estimated size:** ~80 lines (wrapper + models).

### 4.2 Variant Classifier (`checker/variant_classifier.py`)

After diffing, classify each variant by type and significance.

```python
@dataclass
class TextualVariant:
    edition_a_text: str
    edition_b_text: str
    edition_a_offset: int
    edition_b_offset: int
    edition_a_page: int
    edition_b_page: int
    category: VariantCategory
    significance: VariantSignificance  # MAJOR, MODERATE, MINOR, TRIVIAL
    confidence: float
    context: str  # Surrounding text for context
    
class VariantClassifier:
    def classify(self, diff: DiffResult) -> TextualVariant:
        """Classify a single diff result into a variant type."""
        
    def classify_batch(self, diffs: list[DiffResult]) -> list[TextualVariant]:
        """Classify multiple diffs."""
```

**Classification logic:**
1. If only whitespace/punctuation differs → `normalization` or `punctuation_variant`
2. If words differ but meaning is similar (spelling change) → `spelling_change`
3. If words are added/deleted with no replacement → `missing_content` / `added_content`
4. If words are replaced → `textual_variant`
5. Significance scoring: use word count of change + whether the change affects known function words vs content words

**US/UK spelling aware:** Already have `checker/us_uk_spelling.py` — reuse to classify British↔American spelling changes.

**Estimated size:** ~200 lines.

### 4.3 Edition Comparison Reporter (`reporter/comparison_reporter.py`)

```python
class ComparisonReporter:
    def generate(
        self,
        variant: ComparisonReport,
        output_dir: Path,
        formats: list[str] = ["json", "markdown"],
    ) -> dict[str, Path]:
        """Generate comparison reports in multiple formats."""
```

**JSON output** (`edition_comparison.json`):
```json
{
  "edition_a": {
    "source": "scan:2021.148755.Nostromo",
    "title": "Nostromo",
    "publisher": "J.M. Dent & Co.",
    "year": "1904",
    "total_chars": 987197,
    "pages": 452
  },
  "edition_b": {
    "source": "scan:2021.148755.2ndEd",
    "title": "Nostromo",
    "publisher": "J.M. Dent & Co.",
    "year": "1918",
    "total_chars": 995234,
    "pages": 458
  },
  "alignment": {
    "pages_matched": 430,
    "coverage_pct": 95,
    "avg_confidence": 0.82
  },
  "summary": {
    "total_variants": 1247,
    "by_category": {
      "textual_variant": 89,
      "punctuation_variant": 412,
      "spelling_change": 156,
      "normalization": 534,
      "missing_content": 23,
      "added_content": 33
    },
    "by_significance": {
      "major": 12,
      "moderate": 77,
      "minor": 891,
      "trivial": 267
    }
  },
  "variants": [
    {
      "category": "textual_variant",
      "significance": "major",
      "confidence": 0.92,
      "edition_a": {"text": "He was a man of no consequence", "page": 142, "offset": 45231},
      "edition_b": {"text": "He was a man of some consequence", "page": 145, "offset": 46102},
      "context": "...who could tell that Giorgio Viola was a man of no consequence in..."
    }
  ]
}
```

**Markdown output** (`edition_comparison.md`):
```markdown
# Edition Comparison: Nostromo

## Editions
| | Edition A | Edition B |
|---|---|---|
| Source | IA: 2021.148755.Nostromo | IA: 2021.148755.2ndEd |
| Year | 1904 | 1918 |
| Pages | 452 | 458 |
| Chars | 987,197 | 995,234 |

## Alignment
- Pages matched: 430/458 (94%)
- Average confidence: 82%

## Summary
- **1,247 total variants** found
- **12 major** | 77 moderate | 891 minor | 267 trivial

## Major Variants

### 1. Page 142 → 145 (confidence: 92%)
- **A:** "He was a man of no consequence"
- **B:** "He was a man of some consequence"
- Context: "...who could tell that Giorgio Viola was a man of no consequence in..."

## All Variants by Chapter
| Chapter | A Pages | B Pages | Major | Moderate | Minor | Trivial |
|---|---|---|---|---|---|---|
| Part 1, Ch 1 | 14-30 | 14-31 | 1 | 3 | 42 | 11 |
| Part 1, Ch 2 | 31-48 | 32-50 | 0 | 2 | 38 | 9 |
...
```

**TEI-XML output** (`edition_comparison.xml`):
For scholarly use. TEI (Text Encoding Initiative) has a standard `<app>` (apparatus) element for critical editions:
```xml
<app>
  <rdg wit="#edA">He was a man of no consequence</rdg>
  <rdg wit="#edB">He was a man of some consequence</rdg>
</app>
```
This makes Gerrata output usable in digital humanities workflows.

**Estimated size:** ~300 lines.

### 4.4 CLI Integration (`cli.py` additions)

New subcommand:
```bash
gerrata compare-editions \
  --edition-a scan:<ia_id_or_zip_path> \
  --edition-b scan:<ia_id_or_zip_path> | text:<file_path> | pages:<dir> | transcribed:<json> \
  [--edition-a-label "1st Edition (1904)"] \
  [--edition-b-label "2nd Edition (1918)"] \
  [--page-range A_START-A_END] \
  [--page-range-b B_START-B_END] \
  [--significance major|moderate|all] \
  [--output ./reports/] \
  [--formats json,markdown,tei] \
  [--verbose]
```

**Resume support:** Same pattern as main pipeline — `--resume-from transcriptions|alignments|variants|pre-report`.

**Estimated changes to cli.py:** ~150 lines.

---

## 5. Implementation Plan

### Phase 1: Core (scan vs scan, single pair)

1. **`models.py` additions** (~30 lines)
   - `TextualVariant`, `VariantCategory`, `VariantSignificance`, `EditionAlignment`, `ComparisonReport`

2. **`aligner/cross_aligner.py`** (~80 lines)
   - `align_editions()` wrapper around `GlobalAnchorAligner`

3. **`checker/variant_classifier.py`** (~200 lines)
   - `VariantClassifier` with rule-based classification
   - Significance scoring heuristic

4. **`reporter/comparison_reporter.py`** (~300 lines)
   - JSON + Markdown output
   - Chapter-level grouping
   - Summary statistics

5. **`cli.py` additions** (~150 lines)
   - `compare-editions` subcommand
   - Input source parsing (`scan:`, `text:`, `pages:`, `transcribed:`)
   - Full pipeline orchestration

6. **Tests** (~150 lines)
   - Test variant classification rules
   - Test cross-alignment with synthetic data
   - Test report generation

**Total Phase 1: ~910 lines.**

### Phase 2: Enhanced Classification

7. **LLM-assisted variant significance** (~80 lines)
   - Use vision model to classify ambiguous variants
   - "Does this change alter the meaning?" — yes/no/moderate
   - Only for `textual_variant` category (not punctuation/normalization)

8. **Spelling change detection** (~60 lines)
   - Integrate `us_uk_spelling.py` for British/American variant tracking
   - Historical spelling dictionaries (19th century → modern)
   - Flag "authorial archaisms" vs "publisher modernization"

9. **Structural variant detection** (~100 lines)
   - Chapter reordering
   - Added/removed prefaces, appendices
   - Paragraph boundary changes

**Total Phase 2: ~240 lines.**

### Phase 3: Scholarly Features

10. **TEI-XML output** (~120 lines)
    - Proper TEI apparatus criticus format
    - Sigla for edition identifiers
    - Usable in digital humanities tools (e.g., CollateX, Juxta)

11. **Multi-edition comparison** (~150 lines)
    - `gerrata compare-editions --edition-a scan:... --edition-b scan:... --edition-c scan:...`
    - Base-against-all alignment (one "base" edition, compare others to it)
    - Matrix view: which variants appear in which editions

12. **HTML diff viewer** (~100 lines)
    - Inline diff with color coding by variant type
    - Side-by-side view
    - Page-by-page navigation

**Total Phase 3: ~370 lines.**

---

## 6. Reuse from Existing Code

| Component | Reuse Level | Notes |
|---|---|---|
| `GlobalAnchorAligner` | **Direct reuse** | Already aligns arbitrary text to page chunks |
| `TextDiffChecker` | **Direct reuse** | Already diffs any two aligned text passages |
| `VisionTranscriber` | **Direct reuse** | Already generic, no PG dependency |
| `ScanFetcher` | **Direct reuse** | Already handles JP2 zips |
| `FalsePositiveFilter` | **Modified reuse** | Needs edition-aware filtering (what's an FP in errata may be a real variant in collation) |
| `ProgrammaticVerifier` | **Modified reuse** | Confidence scoring works for variants too |
| `us_uk_spelling.py` | **Direct reuse** | Classify British/American spelling changes |
| `gap_detector.py` | **Modified reuse** | Detect content present in one edition but not other |

---

## 7. Open Questions

1. **How to handle chapter-level structural differences?** If edition B renumbers chapters or moves content, page-level alignment will have large gaps. Need a higher-level structural alignment pass before page-level alignment.

2. **Metadata source for edition identification?** IA scans often lack structured publisher/year metadata. The edition verifier already does some metadata extraction — can we reuse that?

3. **Caching strategy for multi-edition?** If comparing 3 editions, we transcribe each once and reuse. Need a transcription cache keyed by scan ID.

4. **How to present variants that span page boundaries?** A textual change that starts on page 142 and continues on page 143 needs careful handling. Current diff checker works within aligned passages — this should naturally work, but needs testing.

---

## 8. Success Criteria

Phase 1 complete when:

- [ ] `gerrata compare-editions scan:A scan:B` produces a JSON + Markdown report
- [ ] `gerrata compare-editions scan:A text:file` works
- [ ] `gerrata compare-editions text:A text:B` works
- [ ] Variant classification correctly identifies at least: textual, punctuation, spelling, normalization
- [ ] Significance scoring matches manual judgment on 10 sample variants
- [ ] Resume from intermediate stages works
- [ ] Tests cover variant classifier, cross-aligner, and reporter

Phase 2 complete when:

- [ ] US/UK spelling changes correctly classified
- [ ] LLM significance classification available as opt-in
- [ ] Structural variants (added/removed chapters) detected and reported

Phase 3 complete when:

- [ ] TEI-XML output valid against TEI schema
- [ ] Multi-edition comparison (3+ editions) works
- [ ] HTML diff viewer renders correctly in browser
