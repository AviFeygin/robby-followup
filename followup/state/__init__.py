"""State, signals and baselines. Pure functions over rows."""
from .baselines import Baseline, compute_baselines
from .projector import QuoteState, project
from .signals import Signals, Windows, compute_signals

__all__ = ["Baseline", "compute_baselines", "QuoteState", "project", "Signals", "Windows", "compute_signals"]
