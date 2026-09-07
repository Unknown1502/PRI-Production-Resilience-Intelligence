"""PRI — Production Resilience Intelligence.

Top-level package.  Import sub-packages explicitly; nothing is re-exported here
to keep startup cost minimal and avoid circular imports.
"""

__all__ = ["__version__"]

#: Reported by ``/health`` alongside the deployed git sha.
__version__ = "0.1.0"
