"""Pure input-edge helpers for the Windows Dynamic Island.

Separated from Qt so held-button and outside-click behaviour can be tested
without a GUI or a live Windows pointer device.
"""
from __future__ import annotations


def has_new_button_press(current: tuple[bool, ...], previous: tuple[bool, ...]) -> bool:
    """True only for a released -> pressed transition of the same device set.

    Missing/unavailable native button state must never trigger a collapse.
    """
    return (bool(current) and len(current) == len(previous)
            and any(down and not before for down, before in zip(current, previous)))


def should_collapse_from_outside_press(*, expanded: bool, scrubbing: bool,
                                       pointer_inside: bool) -> bool:
    """Only a press outside an open, non-scrubbing island should close it."""
    return expanded and not scrubbing and not pointer_inside
