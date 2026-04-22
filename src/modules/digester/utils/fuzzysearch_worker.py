from typing import List

from fuzzysearch import find_near_matches  # type: ignore


class MatchObject:
    """
    Helper class to represent a fuzzy match with start, end, and distance attributes.
    It is used because the fuzzysearch library is not properly typed
    """

    def __init__(self, start_pos: int, end_pos: int, dist: int):
        self.start = start_pos
        self.end = end_pos
        self.dist = dist


def fuzzy_search_worker(
    text: str, pattern: str, start_pos: int, max_errors: int, end_pos: int = -1
) -> List[MatchObject]:
    text_relevant = text[start_pos:end_pos] if end_pos != -1 else text[start_pos:]
    # We need to offset the match positions by start_pos to get correct indices in the original text
    return [
        MatchObject(m.start + start_pos, m.end + start_pos, m.dist)
        for m in find_near_matches(pattern, text_relevant, max_l_dist=max_errors)
    ]
