"""Reporter package — generate errata reports."""

from gerrata.reporter.generator import ReportGenerator
from gerrata.reporter.substantive import SubstantiveErrataGenerator

__all__ = ["ReportGenerator", "SubstantiveErrataGenerator"]
