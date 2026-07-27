"""Reporter package — generate errata reports."""

from book_projects.gerrata.src.gerrata.reporter.generator import ReportGenerator
from book_projects.gerrata.src.gerrata.reporter.substantive import SubstantiveErrataGenerator

__all__ = ["ReportGenerator", "SubstantiveErrataGenerator"]
