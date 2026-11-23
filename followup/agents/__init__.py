"""Writer agent: turns one decision into message text. It can do nothing else."""
from .base import Writer, WriterInput, fmt_date, fmt_money
from .template import TemplateWriter

__all__ = ["Writer", "WriterInput", "TemplateWriter", "fmt_date", "fmt_money"]
