"""
Collection of possible configuration errors.
"""

from enum import Enum

__PLEASE_REPORT = 'Please, report this issue to your instructor!'


class ErrorCodes(Enum):
    """
    Definition of error codes
    """
    STRUCTURAL_TEST_MISSING_MAIN_FILE = 1
    STRUCTURAL_TEST_MAIN_FILE_NOT_FOUND = 2
    STRUCTURAL_TEST_MISSING_FUNCTION_NAME = 3
    GREY_BOX_TEST = 4
    IO_TEST = 5
    IO_TEST_CODE_INJECTION = 6
    ABSTRACT_TEST_MISSING_WORKING_DIRECTORY = 7
    ABSTRACT_TEST_MISSING_MAKEFILE = 8
    FATAL_ERROR = 9
    CONFIGURATION_ERROR = 10
    OCLINT_ERROR = 11
    OCLINT_COMPILATION_ERROR = 12


def error_msg_instructor_test_config(error_type: ErrorCodes) -> str:
    """
    Configuration error

    :param error_type: error code
    :return: The error message
    """
    return f'Error (Code: {error_type.value}): Invalid test configuration! {__PLEASE_REPORT}'


def error_msg_instructor_make(error_type: ErrorCodes, target: str) -> str:
    """
    Invalid Makefile configuration

    :param error_type: error code
    :param target: Makefile target
    :return: The error message
    """
    return f"Error (Code: {error_type.value}): Make for target '{target}' failed! {__PLEASE_REPORT}"


def error_msg_internal_error_code(test_name: str, error_type: ErrorCodes) -> str:
    """
    Unexpected internal error with error code
    :param test_name: test name for which the internal error occurred
    :param error_type: error code
    :return:
    """
    return f"Error (Code: {error_type.value}): Test '{test_name}' had an internal error! " \
           f"{__PLEASE_REPORT}"


def error_msg_test_definition_file_not_found(test_name: str) -> str:
    """
    Test definition file not found.

    :param test_name: test name for which the test definition could not be found.
    :return: The error message
    """
    return f"Error: Test definition file for '{test_name}' not found! {__PLEASE_REPORT}"


def error_msg_io_test_syntax_error(test_name: str) -> str:
    """
    TestIO definition file contains syntax error(s).

    :param test_name: test name for which the TestIO definition file contains syntax error(s).
    :return: The error message
    """
    return f"Error: Test definition file for '{test_name}' contains syntax error(s)! " \
           f"{__PLEASE_REPORT}"


def error_msg_exit_code(exitcode: int) -> str:
    """
    Returns an error message for an exit code.

    :param exitcode: The exitcode
    :return: The error message
    """
    exitcode = abs(exitcode)
    hint = ''
    if exitcode == 8:
        hint = '\nThis exit code might indicate that a fatal arithmetic error occurred ' \
               '(e.g., division by zero).'
    elif exitcode == 11:
        hint = '\nThis exit code might indicate that your program tries to read and write outside' \
               ' the memory that is allocated for it.'

    return f"Error occurred during execution! Exit code: {exitcode}{hint}"
