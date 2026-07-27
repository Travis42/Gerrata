"""Edition verification for pre-pipeline edition matching.

Approaches:
  A — Metadata matching (fast, deterministic)
  B — Line-break fingerprinting (slower, structural)
"""

from book_projects.gerrata.src.gerrata.edition.verifier import EditionVerifier
from book_projects.gerrata.src.gerrata.edition.metadata import MetadataMatcher
from book_projects.gerrata.src.gerrata.edition.linebreaks import LineBreakFingerprinter

__all__ = ["EditionVerifier", "MetadataMatcher", "LineBreakFingerprinter"]
