"""Shared job lifecycle exceptions."""


class JobCancelled(RuntimeError):
    """Raised when cooperative job execution is cancelled."""
