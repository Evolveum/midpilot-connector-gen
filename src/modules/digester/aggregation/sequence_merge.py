# Copyright (C) 2010-2026 Evolveum and contributors
#
# Licensed under the EUPL-1.2 or later.

"""Shared cross-chunk merge helper for entities that carry supporting document sequences.

Used by the deduplication/aggregation passes (auth, attributes, ...) to combine the
``relevant_sequences`` of two entities without keeping duplicate evidence spans.
"""

from typing import Any, List, Protocol, Tuple


class SupportingSequence(Protocol):
    chunk_id: str
    start_sequence: str
    end_sequence: str


class HasRelevantSequences(Protocol):
    # Typed as ``List[Any]`` on purpose: callers hold entities whose sequence element type
    # varies (``DocSequenceItem`` vs ``DocProcessingSequenceItem``), and a concrete generic
    # would be rejected by list invariance. All elements still satisfy ``SupportingSequence``.
    relevant_sequences: List[Any]


def sequence_dedup_key(sequence: SupportingSequence) -> Tuple[str, str, str]:
    """Stable identity of a supporting sequence: (chunk_id, start_sequence, end_sequence)."""
    return (sequence.chunk_id, sequence.start_sequence, sequence.end_sequence)


def merge_relevant_sequences(target: HasRelevantSequences, source: HasRelevantSequences) -> None:
    """Append sequences from ``source`` into ``target``, skipping duplicates.

    Deduplication is by :func:`sequence_dedup_key`, so the same evidence span discovered
    in multiple chunks is only kept once. ``target`` is mutated in place.
    """
    existing_keys = {sequence_dedup_key(seq) for seq in target.relevant_sequences}
    for seq in source.relevant_sequences:
        key = sequence_dedup_key(seq)
        if key not in existing_keys:
            target.relevant_sequences.append(seq)
            existing_keys.add(key)
