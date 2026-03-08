"""
Utility functions and decorators for grey box testing using python.
"""
import ctypes
import os
import select
from io import StringIO
from multiprocessing import Process, Pipe
from typing import List, Any, TextIO

import numpy as np
from numpy.ctypeslib import ndpointer

from fact._util import shorten_text, MAX_OUTPUT_CHAR, run_process, contains_non_printable_ascii, \
    replace_non_printable_ascii

__SIZEOF_INT = ctypes.sizeof(ctypes.c_int(1))

c_int_pp = ndpointer(dtype=np.intp, ndim=1, flags='C_CONTIGUOUS')


def c_pointer(pointer) -> str:
    """
    Returns a hexadecimal string representation of a C-pointer.

    :param pointer: The pointer
    :returns: The string representation
    """
    return hex(pointer) if pointer else 'NULL'


def c_char_array_to_string(array: List[Any], null_terminated: bool = False) -> str:
    """
    Returns a string representation of the characters in the array.
    A brace-enclosed list is used to represent the characters.
    The characters are shown as single-byte integer character constants (e.g., ``'a'``).
    Non printable ascii characters are replaced with ``'�'``.

    :param array: The array
    :param null_terminated: Should a null termination be added?
    :returns: The string representation of the array
    """
    printable = [replace_non_printable_ascii(element) for element in array]
    if null_terminated:
        printable.append("\\0")
    str_array = "{" + ', '.join(f"'{element}'" for element in printable) + "}"
    if '�' in str_array:
        str_array += "\nNote that non printable characters are marked as '�'!"
    return str_array


def c_array_to_string(array: List[Any]):
    """
    Returns a string representation of the values in the array.
    A brace-enclosed list is used to represent the values.

    :param array: The array
    :returns: The string representation of the array
    """
    return "{" + ', '.join(f"{element}" for element in array) + "}"


def c_pointer_array_to_string(array: List[Any]):
    """
    Returns a string representation of the pointers in the array.
    A brace-enclosed list is used to represent the values.

    :param array: The array
    :returns: The string representation of the array
    """
    return c_array_to_string([c_pointer(elemente) for elemente in array])


def char_arr_c(length: int):
    """
    Creates a ctypes char-array with the given length.

    :param length: The length of the array
    :returns: The char-array
    """
    return (ctypes.c_char * length)()


def char_arr_p2c(array: str):
    """
    Creates a ctypes char-array with the appropriate length. The provided string is used to fill the
    created array. Note that the created array is null terminated!

    :param array: The string comprising the elements of the new array.
    :returns: The char-array
    """
    chars = [ord(number) for number in array]
    chars.append(0)
    return (ctypes.c_char * len(chars))(*chars)


def int_arr_c(length):
    """
    Creates a ctypes int-array with the given length.

    :param length: The length of the array
    :returns: The int-array
    """
    return (ctypes.c_int * length)()


def int_arr_p2c(array: List[int]):
    """
    Creates a ctypes int-array with the appropriate length. The provided array is used to fill the
    created array.

    :param array: The array comprising the elements which should be copied to the new array.
    :returns: The int-array
    """
    return (ctypes.c_int * len(array))(*array)


def char_arr_c2p(array) -> List[Any]:
    """
    Creates a list based on the values from a ctypes char-array.

    :param array: The ctypes array comprising the elements which should be copied to the new list.
    :returns: The list
    """
    return [element.decode("ascii") for element in array][:-1]


def int_arr_c2p(array) -> List[Any]:
    """
    Creates a list based on the values from a ctypes array.

    :param array: The ctypes array comprising the elements which should be copied to the new list.
    :returns: The list
    """
    return list(array)


def int_pp_from_2d(array: np.ndarray, cols: int):
    """
    Creates a ctypes multidimensional int-array based on the values from a numpy-array.

    :param array: The data of the multidimensional array
    :param cols: Number of columns
    :returns: The pointer to the multidimensional array
    """
    offset = np.arange(array.shape[0]) * cols * __SIZEOF_INT
    return (array.__array_interface__['data'][0] + offset).astype(np.intp)


class GreyBoxTimeoutError(TimeoutError):
    """
    Raised when a timeout expired during a grey box test.
    """
    function_call_details: str

    def __init__(self, function_call_details: str, *args: object) -> None:
        super().__init__(*args)
        self.function_call_details = function_call_details


def __exec_fun(pipe, function, self, args, kwargs):
    p_output, p_input = pipe
    p_output.close()
    result = function(self, *args, **kwargs)
    p_input.send(result)
    p_input.close()


def test_case(name):
    """
    Decorator for grey box testing. Every test case should use this decorator and specify the
    tested function or procedure name.

    :param name: The name of the tested C function or procedure name
    """

    def decorator(function):
        def wrapper(self, *args, **kwargs):
            p_output, p_input = Pipe()
            process = Process(target=__exec_fun,
                              args=((p_output, p_input), function, self, args, kwargs))

            try:
                exitcode, result = run_process(process, p_input, p_output)
            except TimeoutError as exception:
                raise GreyBoxTimeoutError(
                    self.function_call_details(name, list(args))) from exception
            if exitcode != 0:
                self.exit_failure_message(name, exitcode, list(args))
            if result is not None:
                self.add_error(name, result)

            return result

        return wrapper

    return decorator


def __add_note(notes, caption, result):
    result_str = str(result) if result else ''
    text = shorten_text(result_str, MAX_OUTPUT_CHAR)
    newline = '\n' if text.count('\n') >= 1 else ''
    notes.append(f"{caption}: {newline}'{text}'")


def create_error_hint(actual=None, expected=None, show_expected=False, hint=None) -> str:
    """
    Creates an error hint.

    :param actual: The actual result
    :param expected: The expected result
    :param show_expected: Should the actual and expected results be shown?
    :param hint: An additional hint
    :returns: The error hint
    """
    if not any((show_expected, hint)):
        return ''
    notes = []
    if hint is not None:
        notes.append(hint)
    if show_expected:
        __add_note(notes, "Expected", expected)
        __add_note(notes, "Actual  ", actual)
    return '\n'.join(notes)


class NonAsciiCharacter(Exception):
    """
    Raised when the captured output contains non ascii characters.
    All non printable ascii characters in output are replaced with the special character �.
    """
    output: str
    stream: str

    def __init__(self, output: str, stream: str, *args: object) -> None:
        super().__init__(*args)
        self.output = output
        self.stream = stream

    def error_message_students(self, function_name: str) -> str:
        """
        Returns the default error message if a captured output contains non ascii characters.

        :param function_name: Name of the output generating function
        :return: The error message
        """
        return f'The function {function_name} generated output on {self.stream} containing ' \
               f'invalid characters that are outside of the ASCII range (below 0 or above 127)! ' \
               f'The invalid characters are represented with �.\n' \
               f'Obtained output on stdout:\n{self.output}'


class NonPrintableAsciiCharacter(Exception):
    """
    Raised when the captured output contains non printable ascii characters.
    All non printable ascii characters in output are replaced with the special character �.
    """
    output: str
    stream: str

    def __init__(self, output: str, stream: str, *args: object) -> None:
        super().__init__(*args)
        self.output = output
        self.stream = stream

    def error_message_students(self, function_name: str) -> str:
        """
        Returns the default error message if a captured output contains non printable ascii
        characters.

        :param function_name: Name of the output generating function
        :return: The error message
        """
        return f'The function {function_name} generated output on {self.stream} ' \
               f'containing non printable ASCII characters! ' \
               f'The invalid characters are represented with �.\n' \
               f'Obtained output on stdout:\n{self.output}'


class CaptureStream:
    """
    Used to capture output on a stream. Please ensure that all data is written on the captured
    stream before capturing is stopped (e.g., flush the stream).

    Note that this class is based on https://stackoverflow.com/a/29834357.
    """
    original_stream: TextIO
    original_fileno: int
    pipe_out: int
    pipe_in: int
    captured_text: str
    new_fileno: int
    stream_name: str

    def __init__(self, stream: TextIO, stream_name: str):
        """

        :params stream: Text stream for which the output should be captured.
        :params stream_name: The name of the captured stream used for user friendly error messages
        """
        self.original_stream = stream
        self.original_fileno = self.original_stream.fileno()
        self.pipe_out, self.pipe_in = os.pipe()
        self.captured_text = ""
        self.new_fileno = 0
        self.stream_name = stream_name

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """
        Start capturing all data writen on the stream.
        """
        self.captured_text = ""
        self.new_fileno = os.dup(self.original_fileno)
        os.dup2(self.pipe_in, self.original_fileno)
        os.close(self.pipe_in)

    def stop(self):
        """
        Stop capturing data.
        """
        try:
            self.captured_text = self.__read_output(self.pipe_out, self.original_stream)
        finally:
            os.close(self.pipe_out)
            os.dup2(self.new_fileno, self.original_fileno)
            os.close(self.new_fileno)

    def __read_output(self, out, stream) -> str:
        captured_text = StringIO()
        error = False
        while select.select([out], (), (), 0)[0]:
            try:
                char = os.read(out, 1).decode(stream.encoding)
            except UnicodeDecodeError:
                char = '�'
                error = True
            captured_text.write(char)
        text = captured_text.getvalue()
        if error:
            raise NonAsciiCharacter(text, self.stream_name)
        if contains_non_printable_ascii(text):
            text = replace_non_printable_ascii(text)
            raise NonPrintableAsciiCharacter(text, self.stream_name)
        return text

    def get_data(self) -> str:
        """
        Returns the captured data

        :return: The captured data or empty string, if no data was captured.
        """
        return self.captured_text
