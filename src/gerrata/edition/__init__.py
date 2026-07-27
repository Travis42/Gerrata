"""Edition verification for pre-pipeline edition matching.

Approaches:
  A — Metadata matching (fast, deterministic)
  B — Line-break fingerprinting (slower, structural)
"""

from gerrata.edition.verifier import EditionVerifier
from gerrata.edition.metadata import MetadataMatcher
from gerrata.edition.linebreaks import LineBreakFingerprinter

__all__ = ["EditionVerifier", "MetadataMatcher", "LineBreakFingerprinter"]
