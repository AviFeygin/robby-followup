"""Learning system. Only the decision log is built. The model is designed in docs/DESIGN.md."""
from .decision_log import record, read

__all__ = ["record", "read"]
