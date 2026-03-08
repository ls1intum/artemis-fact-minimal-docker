"""
Error hint generation for input-output using exact matching.
"""
import copy
import re
from bisect import bisect_left, bisect_right
from difflib import SequenceMatcher
from typing import Optional, Tuple, List

import numpy as np

from fact._util import strip_trailing_whitespace


class TagError(ValueError):
    """
    Raised when an invalid tag is used
    """
    tag: str

    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag


class _DiffFraction:
    """
    Represents a fraction of a diff.
    """
    tag: str
    actual_start: int
    actual_end: int
    expected_start: int
    expected_end: int
    block_id: int

    def __init__(self, fraction: Tuple[str, int, int, int, int]) -> None:
        """
        :param fraction: Tuple (tag, i1, i2, j1, j2) describing how to turn a fraction of an
        actual string into an expected string.
            - tag - describing the operation ('delete', 'equal', 'insert', 'replace')
            - i1 - start index of the actual string
            - i2 - end index of the actual string
            - j1 - start index of the actual string
            - j2 - end index of the actual string
        """
        self.tag = fraction[0]
        self.actual_start = fraction[1]
        self.actual_end = fraction[2]
        self.expected_start = fraction[3]
        self.expected_end = fraction[4]
        self.block_id = -1

    def _actual_range(self, actual: str) -> str:
        """
        Returns the considered fraction of the actual string.

        :param actual: The actual string
        """
        return actual[self.actual_start:self.actual_end]

    def _expected_range(self, expected: str) -> str:
        """
        Returns the considered fraction of the expected string.

        :param expected: The expected string
        """
        return expected[self.expected_start:self.expected_end]

    def diff(self, actual: str, expected: str) -> str:
        """
        Returns a diff describing how to turn the actual string into the expected string for the
        considered fraction.

        :param actual: The actual string
        :param expected: The expected string
        """
        if self.tag == 'equal':
            return self._actual_range(actual)
        if self.tag == 'delete':
            return f"[-{self._actual_range(actual)}]"
        if self.tag == 'insert':
            return f"[+{self._expected_range(expected)}]"
        if self.tag == 'replace':
            return f"[{self._actual_range(actual)}->{self._expected_range(expected)}]"
        raise TagError(self.tag)

    def diff_hint(self, actual: str, hint: str) -> str:
        """
        Returns a string describing how to turn the actual string into the expected string for the
        considered fraction using a hint.

        :param actual: The actual string
        :param hint: The hint
        """
        if self.tag == 'equal':
            return self._actual_range(actual)
        return f"[{self._actual_range(actual)}=>{hint}]"

    def truncate_end(self, end: int) -> None:
        """
        Truncates the expected end. If expected and actual are equal the actual end is set
        accordingly.

        :param end: The truncated end
        """
        if end < self.expected_end:
            self.expected_end = end
        if self.tag == 'equal':
            self.actual_end = self.actual_start + (self.expected_end - self.expected_start)

    def move_start(self, other) -> None:
        """
        Moves the expected and actual start based on the other fraction such
        that this fraction starts after the other fraction.

        :param other: The other fraction
        """
        self.expected_start = other.expected_end
        self.actual_start = other.actual_end

    def merge(self, other) -> None:
        """
        Merges two parts. It is assumed that the this fraction precedes the other fraction.

        :param other: The other fraction
        """
        self.tag = 'merged'
        self.actual_end = other.actual_end
        self.expected_end = other.expected_end


class _DiffFractionBlock:
    """
    Utility class to split a diff fraction into multiple parts such that a fraction comprises
    exactly one part from the expected string.
    """
    diff_fraction: _DiffFraction
    breakpoints: List[int]
    blocks: List[int]

    def __init__(self, fraction: Tuple[str, int, int, int, int], breakpoints: List[int]) -> None:
        """

        :param fraction: Tuple (tag, i1, i2, j1, j2) describing how to turn a fraction of an
        actual string into an expected string.
        :param breakpoints: A list of ints describing the position of the last index of each
        fraction of the expected string.
        """
        self.diff_fraction = _DiffFraction(fraction)

        self.breakpoints = breakpoints
        start_idx = bisect_right(breakpoints, self.diff_fraction.expected_start)
        end_idx = bisect_left(breakpoints, self.diff_fraction.expected_end)
        self.blocks = list(range(start_idx, end_idx + 1)) if start_idx <= end_idx else [end_idx]

    def split_fraction(self) -> List[_DiffFraction]:
        """
        Splits a diff fraction such that 'replace'-parts are replaced with 'delete'- and
        'insert'-parts and each fraction comprises exactly one part from the expected string based
        on the breakpoints.

        :returns: A list of diff fractions where each fraction is mapped to exactly one part of the
        expected string.
        """
        parts = []
        if self.diff_fraction.tag == 'replace' and len(self.blocks) >= 1:
            parts += [self._split_replace_fraction()]
        parts += self._split_fraction_per_block()
        return parts

    def _split_replace_fraction(self) -> _DiffFraction:
        """
        Splits a diff fraction with the tag 'replace' into two diff parts with the tags
        'delete' and 'insert'. The 'delete'-fraction is returned.
        """
        fraction = copy.deepcopy(self.diff_fraction)
        fraction.tag = 'delete'
        fraction.block_id = self.blocks[0]
        fraction.expected_end = fraction.expected_start
        self.diff_fraction.tag = 'insert'
        self.diff_fraction.actual_start = self.diff_fraction.actual_end
        return fraction

    def _split_fraction_per_block(self) -> List[_DiffFraction]:
        """
        Splits a diff fraction such that each fraction comprises exactly one part from the expected
        string.

        :returns: A list of diff fractions where each fraction is mapped to exactly one part of the
        expected string.
        """
        if len(self.blocks) == 1:
            self.diff_fraction.block_id = self.blocks[0]
            return [self.diff_fraction]

        parts = []
        fraction = copy.deepcopy(self.diff_fraction)
        for block in self.blocks:
            new_fraction = self._slice_sequence(block, fraction)
            parts.append(new_fraction)
            fraction.move_start(new_fraction)
        return parts

    def _slice_sequence(self, block: int, fraction: _DiffFraction) -> _DiffFraction:
        """
        Creates a new slice from a given fraction. The new fraction starts at the same position as
        the given fraction and is truncated based on the block.

        :param block: The block used to truncated the end of the new slice
        :param fraction: The fraction used to create the new slice
        :returns: The new fraction
        """
        new_fraction = copy.deepcopy(fraction)
        new_fraction.truncate_end(self.breakpoints[block])
        new_fraction.block_id = block
        return new_fraction


def _inline_diff_base(obtained: str, transformed: str, expected: str) -> str:
    """
    Returns a diff to convert the obtained string to expected string.

    :param obtained: The obtained output
    :param transformed: The transformation of the obtained output (e.g. lowercased)
    :param expected: The expected output
    :return: The diff to convert obtained to expected
    """
    sequence_matcher = SequenceMatcher(None, transformed, expected)
    parts = sequence_matcher.get_opcodes()
    diffs = [_DiffFraction(fraction).diff(obtained, expected) for fraction in parts]
    return ''.join(diffs)


def _inline_diff_hint(obtained: str, transformed: str,
                      expected: List[Tuple[str, str]]) -> str:
    """
    Returns a string describing how to turn the obtained string into the expected string using
    diffs and hints.

    :param obtained: The obtained output
    :param transformed: The transformation of the obtained output (e.g. lowercased)
    :param expected: The expected output
    :return: The string describing how to convert obtained to expected
    """
    parts = []
    breakpoints = np.cumsum([len(x[0]) for x in expected])
    expected_str = ''.join(exp[0] for exp in expected)
    sequence_matcher = SequenceMatcher(None, transformed, expected_str)
    opcodes = sequence_matcher.get_opcodes()
    for fraction in opcodes:
        diff_block = _DiffFractionBlock(fraction, breakpoints)
        parts += diff_block.split_fraction()
    msg = [exp[1] for exp in expected]
    parts = _merge_hint_parts(parts, msg)
    parts = _join_diff_parts(parts, obtained, expected_str, msg)
    return parts


def _merge_hint_parts(parts: List[_DiffFraction], msg: List[str]) -> List[_DiffFraction]:
    """
    Merges all hint parts within the same block.

    :param parts: The parts
    :param msg: The hints per block
    :returns: The parts after merging hint parts within the same block
    """
    out = []
    if len(parts) == 0:
        return out
    prev = parts[0]
    out.append(prev)
    for fraction in parts[1:]:
        if msg[fraction.block_id] != '' and prev.block_id == fraction.block_id:
            prev.merge(fraction)
            out[-1] = prev
        else:
            out.append(fraction)
            prev = fraction
    return out


def _join_diff_parts(parts: List[_DiffFraction], obtained: str, expected: str,
                     msg: Optional[List[str]] = None) -> str:
    """
    Creates string for each part and concatenates the individual parts. The resulting string
    describes how the obtained output can be transformed into the expected string.

    :param parts: The parts
    :param obtained: The obtained output
    :param expected: The expected output
    :param msg: The hints per part
    """
    diffs = []
    for fraction in parts:
        if msg and msg[fraction.block_id] != '':
            diff = fraction.diff_hint(obtained, msg[fraction.block_id])
        else:
            diff = fraction.diff(obtained, expected)
        diffs.append(diff)
    return ''.join(diffs)


def line_rstrip_whitespaces(expected: List[Tuple[str, str]]):
    """
    Strips trailing whitespaces
    """
    remove = True
    for i in range(len(expected) - 1, -1, -1):
        if remove:
            expected[i] = (expected[i][0].rstrip(' \t'), expected[i][1])
        line = expected[i][0]
        remove = re.match("^[ \t]*\n", line) is not None
        striped_line = strip_trailing_whitespace(line)
        expected[i] = (striped_line, expected[i][1])
    return expected


def io_error_msg_exact(obtained: str, expected: List[Tuple[str, str]], lower: bool = False,
                       rstrip: bool = False, line_rstrip: bool = False) -> str:
    """
    Returns an error hint describing how to convert the obtained string into expected string.

    :param obtained: The obtained output.
    :param expected: A list. Each list element comprises of two elements:
      - 1. element: Expected output
      - 2. element: Optional hint for this part of the output
    :param lower: If true, all cased characters in obtained and expected are converted to lowercase.
    :param rstrip: If true, all trailing whitespace characters in obtained and expected are removed.
    :param line_rstrip: If true, all trailing whitespaces and tabs are removed for each line.
    :return: The diff to convert obtained to expected
    """
    if line_rstrip:
        obtained = strip_trailing_whitespace(obtained)
        expected = line_rstrip_whitespaces(expected)

    transformed = obtained
    if lower:
        transformed = transformed.lower()
        expected = [(exp[0].lower(), exp[1]) for exp in expected]

    if rstrip:
        transformed = transformed.rstrip()
        for i in range(len(expected) - 1, -1, -1):
            if not expected[i][0].isspace():
                expected[i] = (expected[i][0].rstrip(), expected[i][1])
                break
            expected.pop()
    if all(x[1] == '' for x in expected):
        return _inline_diff_base(obtained, transformed, ''.join(exp[0] for exp in expected))
    return _inline_diff_hint(obtained, transformed, expected)
