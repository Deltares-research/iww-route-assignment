"""Choose which queued vessels share a chamber.

Vessels are taken in queue order and placed with a 2D packer until the next one no longer
fits. The first vessel in the queue always enters, also when it is larger than the chamber.
It then passes alone, so a queue can never block on a vessel that fits nowhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from operator import attrgetter

from packer2d import Item, NotEnoughSpaceError, OutOfBoundsError, pack

PACKER_DEPTH = 3
"""Quadtree depth of the packer. A deeper tree packs tighter and runs slower."""


def select_vessels(
    dimensions: Sequence[tuple[float, float]],
    chamber_length_m: float,
    chamber_width_m: float,
) -> list[int]:
    """Return the queue positions that enter the chamber together, in queue order.

    ``dimensions`` holds (length, beam) in metres per queued vessel. The result always holds
    position 0 when the queue is not empty. Only the leading vessels that fit are returned: a
    vessel that does not fit stops the selection, so later vessels never overtake it.
    """
    if not dimensions:
        return []
    first_length, first_beam = dimensions[0]
    if first_length > chamber_length_m or first_beam > chamber_width_m:
        return [0]

    # Vessels beyond the chamber floor area cannot fit anyway, so do not offer them.
    candidates: list[int] = []
    area = 0.0
    for position, (length, beam) in enumerate(dimensions):
        area += length * beam
        if position > 0 and area > chamber_length_m * chamber_width_m:
            break
        candidates.append(position)
    if len(candidates) == 1:
        return [0]

    items = [Item(dimensions[position], data=position) for position in candidates]
    unplaced = [item.rect for item in items]
    try:
        pack(
            items,
            (chamber_length_m, chamber_width_m),
            insert_order=attrgetter("data"),
            max_depth=PACKER_DEPTH,
            grow_dir="nowhere",
        )
    except (NotEnoughSpaceError, OutOfBoundsError, AttributeError, ValueError):
        # packer2d raises AttributeError for "no space" with grow_dir="nowhere" because of a
        # bug in its error path. Items placed before the failure keep a new rect.
        pass
    selected: list[int] = []
    for item, initial in zip(items, unplaced, strict=True):
        if item.rect is initial:
            break
        selected.append(int(item.data))
    return selected or [0]
