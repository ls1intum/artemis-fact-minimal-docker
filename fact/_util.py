"""
Utility functions.
"""
import ast
import re
import string
from multiprocessing import Process
from multiprocessing.connection import Connection
from typing import Tuple, Optional

MAX_OUTPUT_CHAR = 16384


def shorten_text(text: str, max_num_chars: int):
    """
    Shortens the given text to a maximum number of chars.
    If there are more chars than specified in max_num_chars,
    it will append: ``\\n[{} characters were truncated...]``.
    The truncation note is not accounted in max_num_chars!

    :param text: text to shorten
    :param max_num_chars: maximal number of characters
    :return: The shortened text
    """
    if len(text) <= max_num_chars:
        return text
    max_num_chars = max(max_num_chars, 0)
    short: str = f"\n[{len(text) - max_num_chars} characters were truncated...]"
    return f"{text[:max_num_chars]}{short}"


def is_literal(text: str):
    """
    Checks if a given string is a single literal in C.

    :param text: string to be checked
    :return: True, if string is a literal
    """
    if any(elem in r"<>=!&|~;:,*/^[]{}()%?_" for elem in text):
        return False
    if re.fullmatch(r"'.'", text):
        return True
    if re.fullmatch(r'[+-]?[ \t]*([0-9]+|[0-9]*\.)\d*([eE][+-]?[0-9]*)?[fFlLuU]*?', text):
        return True
    if re.fullmatch(r'[+-]?[ \t]*0[bBxX][0-9a-fA-F]*[uUlL]*', text):
        return True
    return False


def contains_non_printable_ascii(text: str) -> bool:
    """
    Checks if a given string contains a non printable ascii character.

    :param text: string to be checked
    :return: True, if string contains non printable ascii character.
    """
    return len(set(text).difference(set(string.printable))) > 0


def unescape(text: str) -> str:
    """
    Unescapes a backslash-escaped string.

    :param text: The backslash escaped string
    :returns: The unescaped string
    """
    if text == '':
        return text
    return ast.literal_eval(f'"{text}"')


def strip_trailing_whitespace(text: str) -> str:
    """
    For each line all trailing whitespaces and tabs are removed. Newlines are preserved.

    :param text: The string to strip trailing whitespaces and tabs in each line
    :returns: The stripped string
    """
    return re.subn("[ \t]+\n", "\n", text)[0]


def replace_non_printable_ascii(text: str, replacement: str = '�', skip: str = '') -> str:
    """
    Replaces all non printable ascii characters with a given replacement.
    The characters given in skip are not replaced.

    :param text: The processed string
    :param replacement: Non ascii characters are replaced with this string.
    :param skip: The non ascii characters that are not replaced.
    :returns: The string in which all non printable ascii characters were replaced.
    """
    ascii_chars = set(string.printable + skip)
    return ''.join([c if c in ascii_chars else replacement for c in text])


def run_process(process: Process, p_input: Connection, p_output: Connection) -> \
        Tuple[int, Optional[str]]:
    """
    Runs a process which uses a pipe for interprocess communication. Returns the exit code and
    the resulting string read from the pipe.

    :param process: The process to be executed.
    :param p_input: The write-end of the pipe.
    :param p_output: The read-end of the pipe.
    :raises TimeoutError: If a timeout occurs
    """
    process.start()
    p_input.close()
    result = None
    try:
        result = p_output.recv()
    except EOFError:
        pass
    except TimeoutError as ex:
        process.kill()
        raise TimeoutError from ex
    p_output.close()
    process.join()
    exitcode = process.exitcode
    process.close()
    return exitcode, result
