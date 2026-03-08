"""
This module contains the different test cases which can be used for this C testing framework.
Note that each test case must inherit from AbstractTest!
"""
import ctypes
import importlib.machinery
import importlib.util
import json
import logging
import pathlib
import re
import shutil
import subprocess
from abc import abstractmethod, ABC
from contextlib import contextmanager
from ctypes import CDLL, cdll
from datetime import datetime
from enum import Enum, unique
from multiprocessing import Process, Pipe
from os import access, R_OK, W_OK
from os.path import join
from signal import signal, alarm, SIGALRM, SIG_IGN
from subprocess import TimeoutExpired
from typing import Optional, List, Dict, Tuple, Any, Union, Generator

from clang.cindex import Cursor, Token
from textx import TextXSyntaxError

from fact._error import error_msg_internal_error_code, \
    ErrorCodes, error_msg_instructor_make, error_msg_test_definition_file_not_found, \
    error_msg_io_test_syntax_error, error_msg_exit_code, error_msg_instructor_test_config
from fact._structural import StructuralTest, DiagnosticError, annotation_cursors, \
    comment_token, sourcecode_location, parse_c_file
from fact._util import is_literal, contains_non_printable_ascii, run_process
from fact.c_util import char_arr_p2c, char_arr_c, GreyBoxTimeoutError
from fact.io import IOParser, IOTestConfig, IOScriptExecutionError

_DEFAULT_DIRECTORY = '../assignment/'
_DEFAULT_MAKEFILE_NAME = 'Makefile'
_DEFAULT_EXEC_TIMEOUT = 5
_DEFAULT_MAKE_TIMEOUT = 5
_DEFAULT_TRANSLATION_UNIT = 'main.c'


class ConfigurationError(Exception):
    """
    Raised when a test configuration is erroneous.
    """


class SubstitutionException(Exception):
    """
    Raised when a substitution fails.
    """
    errors: List[str]

    def __init__(self, errors) -> None:
        super().__init__()
        self.errors = errors


class Replacement:
    """
    Represents a replacement of a literal assigned to a variable.
    """

    def __init__(self, lineno: int, old_value: str) -> None:
        """

        :param lineno: The line number in which the replacement is conducted.
        :param old_value: The value which was changed by the replacement.
        """
        self.lineno = lineno
        self.old_value = old_value

    def __eq__(self, o: object) -> bool:
        if isinstance(o, Replacement):
            return self.lineno == o.lineno and self.old_value == o.old_value
        return False

    def __hash__(self) -> int:
        return (self.lineno, self.old_value).__hash__()

    def __str__(self) -> str:
        return f"Replacement({self.lineno}, {self.old_value})"


class _SubstituteVariable:
    def __init__(self, value):
        self.value = value
        self.replacements = []

    def __call__(self, match):
        if not match.group(1):
            return match.group(0)
        if is_literal(match.group(3)):
            lineno = match.string.count('\n', 0, match.start()) + 1
            self.replacements.append(Replacement(lineno, match.group(3)))
            return match.group(2) + self.value + match.group(4)
        return match.group(0)


def apply_substitution(variable: str, value: str, text: str) -> Tuple[str, List[Replacement]]:
    """
    Searches in the string for matches. If a match was found, the original
    value of the variable initialization is substituted accordingly.

    Can be used to substitute all scalar basic types (e.g., ``bool``, ``char``, ``short``,
     ``int``, ``long``, ``float``, ``double``).

    :param variable: variable name
    :param value: new value of the variable
    :param text: string searched for matches. If a match was found, the original
                   value of the variable initialization is substituted accordingly
    :return: String after substitution and list of replacements

    """
    replace = _SubstituteVariable(value)
    regex = fr'"(?:\\"|[^"])*"|((?<!\w)({variable}\s*=\s*)([^\"]*?)([ \t]*[,;)]))'
    substitution = re.subn(regex, replace, text)

    return substitution[0], replace.replacements


class TestFailedError(Exception):
    """
    Raised when a test failed.
    """
    __test__ = False


@unique
class TestStatus(Enum):
    """
    Models the four states - skipped, error, failure, and success - of a test.
    """
    __test__ = False

    SKIPPED = "skipped"
    ERROR = "error"
    FAILURE = "failure"
    SUCCESS = "success"


class SourcecodeRunner:
    """
    Runner for building and executing C programs.
    """
    makefile_directory: Union[str, pathlib.Path]
    sourcecode_directory: str
    make_target: str
    makefile_name: str
    make_timeout_sec: int
    exec_timeout_sec: int

    def __init__(self, make_target: str, makefile_directory: Union[str, pathlib.Path],
                 makefile_name: str = _DEFAULT_MAKEFILE_NAME,
                 sourcecode_directory: Optional[str] = None,
                 make_timeout_sec: int = None, exec_timeout_sec: int = None) -> None:
        """
        :param make_target: Target name of the Makefile rule to be used
        :param makefile_name: Filename of the Makefile
        :param makefile_directory: The directory in which the Makefile resides
        :param sourcecode_directory: The directory in which the sourcecode resides.
        :param make_timeout_sec: Compilation timeout in seconds
        :param exec_timeout_sec: Execution timeout in seconds
        """

        self.makefile_directory = makefile_directory
        self.make_target = make_target
        self.makefile_name = makefile_name
        self.make_timeout_sec = make_timeout_sec
        self.exec_timeout_sec = exec_timeout_sec

        if sourcecode_directory is None:
            self.sourcecode_directory = makefile_directory
        else:
            self.sourcecode_directory = sourcecode_directory

    def validate_test(self):
        """
        Checks whether the makefile_directory is present and is a directory.

        :return: True, if the path is a directory
        """
        if not pathlib.Path(self.makefile_directory).is_dir():
            raise ConfigurationError

    def build_executable(self, target: Optional[str] = None) -> subprocess.CompletedProcess:
        """
        Compiles the C program

        :param target: Target name of the Makefile rule to be used
        :return: Output
        :raises subprocess.CalledProcessError: If the process exits with a non-zero exit code.
        """

        if target is None:
            target = self.make_target

        args = ['make', '-f', self.makefile_name, '-B', target]

        if not pathlib.Path(self.makefile_directory).samefile(
                pathlib.Path(self.sourcecode_directory)):
            shutil.copy(pathlib.Path(self.makefile_directory) / self.makefile_name,
                        pathlib.Path(self.sourcecode_directory) / self.makefile_name)

        return subprocess.run(args, capture_output=True, text=True, cwd=self.sourcecode_directory,
                              timeout=self.make_timeout_sec, check=True)

    def run_executable(self, arguments: List[str], stdin: str) -> subprocess.CompletedProcess:
        """
        Executes the C program with the provided arguments and input on stdin

        :param arguments: Commandline arguments
        :param stdin: Input on stdin
        :return: Output
        :raises subprocess.CalledProcessError: If the process exits with a non-zero exit code.
        """
        args = ['./' + self.make_target]
        if arguments:
            args.extend(arguments)
        return subprocess.run(args, capture_output=True, input=stdin, text=True, check=False,
                              cwd=self.sourcecode_directory, timeout=self.exec_timeout_sec)

    def status_msg_timeout(self) -> str:
        """
        Returns a status message describing the make and execution timeout of this test runner.

        :returns: The status message
        """
        make_t = self.make_timeout_sec
        exec_t = self.exec_timeout_sec
        if make_t and exec_t:
            timeout_msg = f'{make_t} seconds make timeout and {exec_t} seconds execution'
        elif not make_t and exec_t:
            timeout_msg = f'no make timeout and {exec_t} seconds execution'
        elif make_t and not exec_t:
            timeout_msg = f'{make_t} seconds make timeout and no execution'
        else:
            timeout_msg = 'no'
        return timeout_msg

    def unexpected_error_msg_make(self, target: Optional[str] = None) -> str:
        """
        Returns an error message describing that an unexpected error occurred during the Makefile
        execution.

        :param target: Target name of the Makefile rule to be used
        :returns: The error message
        """
        if target is None:
            target = self.make_target
        return error_msg_instructor_make(ErrorCodes.IO_TEST, target)

    @classmethod
    def from_config(cls, test_config: Dict[str, Any], default_make_target: Optional[str] = None):
        """
        Configure a source code runner with a given yaml-file.

        :param test_config: File name of the yaml-file containing the configuration
        :param default_make_target: The default target for the make file. Used as a fallback if
         no target is specified in the configuration.
        """
        if default_make_target is None:
            translation_unit = test_config.get('translation_unit', _DEFAULT_TRANSLATION_UNIT)
            default_make_target = pathlib.Path(translation_unit).stem
        make_target = test_config.get('make_target', default_make_target)
        makefile_name = test_config.get('makefile_name', _DEFAULT_MAKEFILE_NAME)
        makefile_directory = test_config.get('makefile_directory', _DEFAULT_DIRECTORY)
        sourcecode_directory = test_config.get('sourcecode_directory', makefile_directory)
        make_timeout_sec = test_config.get('make_timeout', _DEFAULT_MAKE_TIMEOUT)
        exec_timeout_sec = test_config.get('exec_timeout', _DEFAULT_EXEC_TIMEOUT)
        return cls(make_target, makefile_directory, makefile_name, sourcecode_directory,
                   make_timeout_sec, exec_timeout_sec)


class AbstractTest(ABC):
    """
    A abstract test that every test has to inherit from.
    """

    test_name: str
    sourcecode_runner: SourcecodeRunner
    requirements: List[str]

    def __init__(self, test_name: str, sourcecode_runner: SourcecodeRunner,
                 requirements: List[str] = None):
        """

        :param test_name: An unique test case name.
        :param sourcecode_runner: A runner to build an executable and execute it
        :param requirements:  A list of test cases names that have to pass for this test to be run.
        """

        self.test_name = test_name
        self.sourcecode_runner = sourcecode_runner
        self.requirements = [] if requirements is None else requirements
        self.case = None

    def start(self, case):
        """
        Starts the test run.

        :param case: The test case where this test should get added to.
        :return: None
        """

        self.case = case

        start_time: datetime = datetime.now()
        try:
            self.sourcecode_runner.validate_test()
            self._run_test()
        except TestFailedError:
            logging.info("'%s' failed.", self.test_name)
        except ConfigurationError as exception:
            self.__mark_as_failed(error_msg_internal_error_code(self.test_name,
                                                                ErrorCodes.CONFIGURATION_ERROR))
            logging.exception(exception)
        except (TimeoutExpired, TimeoutError) as exception:
            self._timeout()
            logging.exception(exception)
        except FileNotFoundError as exception:
            self.__mark_as_failed(error_msg_test_definition_file_not_found(self.test_name))
            logging.exception(exception)
        except TextXSyntaxError as exception:
            self.__mark_as_failed(error_msg_io_test_syntax_error(self.test_name))
            logging.exception(exception)
        except Exception as exception:  # pylint: disable=broad-except
            error_msg = error_msg_internal_error_code(self.test_name, ErrorCodes.FATAL_ERROR)
            self.__mark_as_failed(error_msg)
            logging.exception(exception)

        self.case.time = datetime.now() - start_time

    def _fail(self, msg: str) -> None:
        """
        Marks the current test as failed with the given message.

        :param msg: Error message
        :return: None

        :raises TestFailedError: When called
        """
        self.__mark_as_failed(msg)
        raise TestFailedError(f"{self.test_name} failed.")

    def __mark_as_failed(self, msg: str) -> None:
        """
        Marks the current test case as failed.

        :param msg: Error message
        :return:
        """
        self.case.message = msg
        self.case.result = TestStatus.FAILURE
        logging.info("Test '%s' failed with: %s", self.test_name, msg)

    def _timeout(self, msg: str = '') -> None:
        """
        Marks the current test as failed with the given optional message.
        Should be called once a test timeout occurred.

        :param msg: Timeout error message
        :return: None
        """
        description = ''
        if msg:
            description = f' ({msg})'
        self.__mark_as_failed(f"Error: timeout{description}")

    @abstractmethod
    def _run_test(self) -> None:
        """
        Implement your test in the overridden method.
        """

    def start_msg(self) -> str:
        """
        Returns a startup message for this test

        :return: The message
        """
        info_msg = f"Running test case '{self.test_name}' with "
        timeout_msg = self.sourcecode_runner.status_msg_timeout()
        return info_msg + timeout_msg + ' timeout...'


class AbstractTimeoutTest(AbstractTest):
    """
    Abstract test for test cases that are not run via the subprocess module
    """

    def _run_test(self) -> None:
        if self.sourcecode_runner.exec_timeout_sec > 0:
            with self.__timeout(self.sourcecode_runner.exec_timeout_sec):
                self._run_with_timeout()
        else:
            self._run_with_timeout()

    @abstractmethod
    def _run_with_timeout(self) -> None:
        """
        Implement your test in the overridden method.

        :return: None
        """

    @contextmanager
    def __timeout(self, timeout_sec: int) -> Generator:
        """
        Context manager used to implement timeouts. This implementation is based on [#]_

        :param timeout_sec:
        :return: None

        .. [#]  https://www.jujens.eu/posts/en/2018/Jun/02/python-timeout-function/
        """
        signal(SIGALRM, self.__raise_timeout)
        alarm(timeout_sec)
        try:
            yield
        finally:
            signal(SIGALRM, SIG_IGN)

    @staticmethod
    def __raise_timeout(sig_num: int, frame):  # pylint: disable=unused-argument
        """
        Timeout handler

        :param sig_num: Signal number
        :param frame: Current stack frame
        :return: None

        :raise: TimeoutError
        """
        raise TimeoutError


class TestCodeStructure(AbstractTimeoutTest):
    """
    Test runner for structural tests. Analyzes the code structure of a program and checks whether
    the structure satisfies the requirement.
    """
    __test__ = False

    translation_unit: str
    config: Dict[str, Any]

    def __init__(self, test_name: str, translation_unit: str, config: Dict[str, Any],
                 sourcecode_runner: SourcecodeRunner, requirements: List[str] = None):
        """

        :param test_name: An unique test case name.
        :param translation_unit: path to translation unit
        :param config: Dict containing the test configuration
        :param sourcecode_runner: A runner to build an executable and execute it
        :param requirements: A list of test cases names that have to pass for this test to be run.
        """
        super().__init__(test_name, sourcecode_runner, requirements)

        self.translation_unit = translation_unit
        self.config = config

    def _run_with_timeout(self):
        test = StructuralTest(self.config, self.translation_unit,
                              self.sourcecode_runner.sourcecode_directory)
        errors = []
        try:
            errors = test.run_test()
        except DiagnosticError as exception:
            self._fail(
                f'The compilation of your program was not successful:\n\n{exception.error_message}')

        if len(errors) > 0:
            error_messages = '\n'.join(sorted(errors))
            msg = f"The structure of your program is not as expected:\n\n{error_messages}\n"
            self._fail(msg)

    @classmethod
    def from_config(cls, test_config: Dict[str, Any]):
        """
        Configure a test runner for structural tests with a given yaml-file.

        :param test_config: File name of the yaml-file containing test configuration
        """

        sourcecode_runner = SourcecodeRunner.from_config(test_config)
        test_name = test_config.get('name', 'TestCodeStructure')
        translation_unit = test_config.get('translation_unit', _DEFAULT_TRANSLATION_UNIT)
        requirements = test_config.get('requirements', [])

        return cls(test_name, translation_unit, test_config, sourcecode_runner, requirements)


class TestIO(AbstractTest):
    """
    Test runner for input-output tests. For IO-tests the following steps are performed:

    #. If substitution is present, apply the substitution to the source code
    #. Recompile if necessary
    #. Run program with the defined commandline arguments and the input on stdin
    #. Compare the exit code and match the regexes for stdout and stderr
    #. Report results if an error occurred
    """
    __test__ = False

    filename_io_test: str
    c_file_path: pathlib.Path
    original_content: str
    replacement_content: Optional[str]

    def __init__(self, test_name: str, c_file: str, filename_io_test: str,
                 sourcecode_runner: SourcecodeRunner, requirements: List[str] = None):
        """

        :param test_name: An unique test case name.
        :param c_file: Filename of the C source code
        :param filename_io_test: File name of the DSL conform txt-file containing the io test
        definition
        :param sourcecode_runner: A runner to build an executable and execute it
        :param requirements: A list of test cases names that have to pass for this test to be run.

        """
        super().__init__(test_name, sourcecode_runner, requirements)
        self.filename_io_test = filename_io_test
        self.c_file_path = pathlib.Path(self.sourcecode_runner.sourcecode_directory) / c_file
        self.replacement_content = None
        self.original_content = ''

    def _run_test(self) -> None:
        try:
            io_parser = IOParser(pathlib.Path(self.filename_io_test).read_text(encoding='utf-8'))
        except IOScriptExecutionError:
            self._fail(error_msg_instructor_test_config(ErrorCodes.IO_TEST_CODE_INJECTION))
            return

        modifies_code = any(test.modifies_code() for test in io_parser.tests)

        if modifies_code:
            self._check_translation_unit(self.c_file_path)
            self.original_content = self.c_file_path.read_text()

        num_tests = len(io_parser.tests)
        errors: List[str] = [''] * num_tests

        self._test_original_code(errors, io_parser)

        if modifies_code:
            self._test_substituted_code(errors, io_parser)

        errors = [x for x in errors if x is not None]

        if errors:
            num_errors = len(errors)
            case_cases = 'case' + ('s' if num_errors > 1 else '')
            error_description = '\n\n'.join(errors)
            msg = f'Error: The output of your program does not match the expected output. In ' \
                  f'total {num_errors} test {case_cases} failed:\n\n\n{error_description}'
            self._fail(msg)

    def _check_translation_unit(self, translation_unit: pathlib.Path) -> None:
        """
        Checks whether the translation unit is present, it is readable and writeable.
        If any of those requirements is not satisfied, the test fails.

        :param translation_unit: path to translation unit
        :return: None
        """
        if not translation_unit.is_file():
            self._fail(f"Error: The file '{translation_unit}' is not present!")
        if not access(translation_unit, R_OK):
            self._fail(f"Error: The file '{translation_unit}' is not readable!")
        if not access(translation_unit, W_OK):
            self._fail(f"Error: The file '{translation_unit}' is not writeable!")

    def _test_substituted_code(self, errors, io_parser) -> None:
        """
        Runs all io tests with preceding replacement and substitution. Note that replacement happens
        before substitution!

        :param errors: List of errors
        :param io_parser: Parsed io test definition
        :return: None
        """
        with self.recompile():
            for idx, test in enumerate(io_parser.tests):
                substitution_description = []
                if not test.modifies_code():
                    continue
                error = self.remove_comments()
                if error:
                    errors[idx] = error
                    continue
                replacement_errors = self.valid_replacement(test)
                try:
                    substitution_description += self.valid_substitution(test)
                except SubstitutionException as ex:
                    errors[idx] = '\n'.join(replacement_errors + ex.errors)
                    continue
                if replacement_errors:
                    errors[idx] = '\n'.join(replacement_errors)
                    continue
                with self.replace():
                    try:
                        self.sourcecode_runner.build_executable()
                    except subprocess.CalledProcessError as error:
                        errors[idx] = self.sourcecode_runner.unexpected_error_msg_make()
                        logging.error(error.stderr)
                        continue
                    errors[idx] = self.execute_io_test(test, substitution_description)

    def remove_comments(self) -> Optional[str]:
        """
        Removes all comments in the source code.

        :returns: An error message, if an error occurs.
        """
        args = ["gcc", "-fpreprocessed", "-dD", "-E", "-P", self.c_file_path.name]
        try:
            output = subprocess.run(args, capture_output=True, text=True, check=True,
                                    cwd=self.sourcecode_runner.sourcecode_directory,
                                    timeout=self.sourcecode_runner.make_timeout_sec)
        except subprocess.CalledProcessError as error:
            logging.error(error.stderr)
            return error.stderr
        self.replacement_content = output.stdout
        return None

    def _test_original_code(self, errors, io_parser) -> int:
        """
        Runs all io tests without a replacement in the source code

        :param errors: List of errors
        :param io_parser: Parsed io test definition
        :return: Number of tests executed
        """
        num_original = 0
        for idx, test in enumerate(io_parser.tests):
            if test.modifies_code():
                continue
            num_original += 1
            errors[idx] = self.execute_io_test(test, [])
        return num_original

    def execute_io_test(self, test: IOTestConfig,
                        substitution_description: List[str]) -> Optional[str]:
        """
        Executes an io test

        :param test: Test case
        :param substitution_description: Description of substitutions.
        :return: An error if the test case failed
        """
        try:
            output = self.sourcecode_runner.run_executable(test.arguments, test.stdin)
        except TimeoutExpired:
            test_result = test.test_results(None, substitution_description)
            return test_result.timeout_msg(self.sourcecode_runner.exec_timeout_sec)
        except UnicodeDecodeError as ex:
            test_result = test.test_results(None, substitution_description)
            return test_result.unicode_decode_msg(ex.args[1].decode('ascii', 'replace'))

        if test.settings.printable_ascii:
            if contains_non_printable_ascii(output.stdout):
                test_result = test.test_results(None, substitution_description)
                return test_result.ascii_msg(output.stdout, 'stdout')
            if contains_non_printable_ascii(output.stderr):
                test_result = test.test_results(None, substitution_description)
                return test_result.ascii_msg(output.stderr, 'stderr')

        test_result = test.test_results(output, substitution_description)
        if test_result.is_successful():
            return None
        return test_result.error_msg(self.c_file_path)

    def valid_replacement(self, test: IOTestConfig) -> List[str]:
        """
        Check if the number of replacements matches with the definition and applies the replacement
        if no error was found

        :param test: Test case
        :return: List of error messages
        """
        errors = []
        content = self.replacement_content
        for replacement in test.replacements:
            regex = re.compile(replacement.pattern)
            (content, matches) = re.subn(regex, replacement.replace, content)
            if matches != replacement.num_matches:
                errors.append(replacement.hint)
        self.replacement_content = content
        return errors

    def valid_substitution(self, test: IOTestConfig) -> List[str]:
        """
        Check if the number of substitutions matches with the definition and applies the
        substitution if no error was found

        :param test: Test case
        :return: List of error messages
        """

        errors = []
        replacement_info = []
        content = self.replacement_content
        for substitution in test.substitutions:
            (content, replacements) = apply_substitution(substitution.variable, substitution.value,
                                                         content)
            if len(replacements) != substitution.num_matches:
                errors.append(substitution.hint)
            replacement_info += [
                f"- In line {r.lineno} of the tested code the value {r.old_value} assigned to "
                f"{substitution.variable} has been substituted with {substitution.value}."
                for r in replacements]
        self.replacement_content = content
        if errors:
            raise SubstitutionException(errors)
        return replacement_info

    @contextmanager
    def recompile(self):
        """
        Triggers a recompilation after execution

        :return: None
        """
        try:
            yield
        finally:
            self.c_file_path.write_text(self.original_content)
            self.c_file_path.touch(exist_ok=True)
            self.sourcecode_runner.build_executable()

    @contextmanager
    def replace(self):
        """
        Manages insertion of the replacement and recovery of the original code.

        :return: None
        """
        self.c_file_path.write_text(self.replacement_content)
        self.c_file_path.touch(exist_ok=True)
        try:
            yield
        finally:
            self.c_file_path.write_text(self.original_content)
            self.c_file_path.touch(exist_ok=True)

    @classmethod
    def from_config(cls, test_config: Dict[str, Any]):
        """
        Configure a test runner for input-output tests with a given yaml-file.

        :param test_config: File name of the yaml-file containing test configuration
        """
        test_name = test_config.get('name', 'TestIO')
        translation_unit = test_config.get('translation_unit', _DEFAULT_TRANSLATION_UNIT)
        c_file = test_config.get('c_file', translation_unit)
        tu_stem = pathlib.Path(translation_unit).stem
        filename_io_test = test_config.get('io_test_config', tu_stem + '_io.txt')
        sourcecode_runner = SourcecodeRunner.from_config(test_config)
        requirements = test_config.get('requirements', [])

        return cls(test_name, c_file, filename_io_test, sourcecode_runner, requirements)


class GreyBoxTestRunner(ABC):
    """
    Loads a shared library and executes tests implemented in the function run.

    Note that each grey box test must inherit from GreyBoxTestRunner!
    The execution, verification, and error reporting is left to the user.
    """
    library_path: str
    errors: Dict[str, List[str]]

    def __init__(self, library_path: str) -> None:
        """

        :param library_path: Path to the shared library
        """
        super().__init__()
        self.library = CDLL(library_path)
        self.errors = {}

    @abstractmethod
    def run(self) -> None:
        """
        Runs the grey box test. Implement your test in the overridden method.

        :return: None
        """

    def add_error(self, function_name: str, hint: Optional[str] = None) -> None:
        """
        Adds an error. Call this method to report an error in the grey box test

        :param function_name: The name of the erroneous function
        :param hint: A hint that should be shown as part of the error message
        :return: None
        """
        self.errors.setdefault(function_name, []).append(hint)

    def exit_failure_message(self, function_name: str, exitcode: int, args: List[any]):
        """
        Adds an error message when the execution of the tested function does not terminate
        successfully.

        :param function_name: The name of the tested function
        :param exitcode: The exitcode of the process executing the test function
        :param args: The arguments passed to the tested function
        :return: None
        """
        error = f'{error_msg_exit_code(exitcode)}\n' \
                f'{self.function_call_details(function_name, args)}'
        self.add_error(function_name, error)

    @abstractmethod
    def function_call_details(self, function_name: str, args: List[any]) -> str:
        """
        Returns a description of the function call for the tested function.

        :param function_name: The name of the tested function
        :param args: The arguments passed to the tested function
        :return: The description of the function call
        """

    def test_failed(self) -> bool:
        """
        Returns if a test failed.

        :return: True, if any test failed.
        """
        return len(self.errors) > 0


class TestGreyBox(AbstractTimeoutTest):
    """
    Test runner for grey-box tests. Executes tests defined in a GreyBoxTestRunner.
    """
    __test__ = False

    errors: Dict[str, List[str]]
    library_name: str
    unit_test: bool
    max_errors: int

    def __init__(self, test_name: str, library_name: str,
                 test_case, sourcecode_runner: SourcecodeRunner, requirements: List[str] = None,
                 unit_test: bool = True, max_errors: int = 0):
        """

        :param test_name:  An unique test case name.
        :param library_name: Name of the shared library
        :param test_case: Class name of GreyBoxTestRunner implementation
        :param sourcecode_runner: A runner to build an executable and execute it
        :param requirements: A list of test cases names that have to pass for this test to be run.
        :param unit_test: Is this test a unit test? Otherwise the error messaging is slightly
        adjusted for a procedural test
        :param max_errors: Maximal number of error feedbacks shown per function.
        If smaller than zero, all error feedbacks are shown.
        """
        super().__init__(test_name, sourcecode_runner, requirements)

        self.errors = {}
        self.library_name = library_name
        self.test_case = test_case
        self.unit_test = unit_test
        self.max_errors = max_errors

    def _run_with_timeout(self):
        try:
            self.sourcecode_runner.build_executable()
        except subprocess.CalledProcessError:
            self._fail(self.sourcecode_runner.unexpected_error_msg_make())

        test_case = self.test_case(join('.', self.sourcecode_runner.sourcecode_directory,
                                        self.library_name))

        try:
            test_case.run()
        except GreyBoxTimeoutError as exception:
            self._fail_timeout(exception.function_call_details)

        if test_case.test_failed():
            feedback = self._error_feedback(test_case.errors)
            self._fail(feedback)

    def _fail_timeout(self, last_function_call: str) -> None:
        """
        Marks the test as failed due to a timeout.

        :last_function_call: A description of the last function call
        """
        feedback = f'Timeout: The execution of your program was canceled since it did not ' \
                   f'finish after {self.sourcecode_runner.exec_timeout_sec} seconds! This ' \
                   f'might indicate that there is some unexpected behavior ' \
                   f'(e.g., an endless loop) or that your program is very slow!'
        if last_function_call:
            feedback = f'{feedback}\nLast executed test:\n{last_function_call}'
        self._fail(feedback)

    def _error_feedback(self, errors) -> str:
        """
        Returns the error feedback for a grey box test.

        :param errors: Maps to each function or procedure a list of error descriptions.
        """
        unique_errors = {}
        num_total_errors = 0
        for error, descriptions in errors.items():
            num_errors = len(descriptions)
            num_total_errors += num_errors
            trunc = self.max_errors > 0
            desc_trunc = descriptions[:self.max_errors] if trunc else descriptions
            desc_unique = list(dict.fromkeys(desc_trunc))
            if trunc and num_errors > self.max_errors:
                results = 'results' if self.max_errors > 1 else 'result'
                truncation_hint = f"In total {num_errors} tests for '{error}' failed. " \
                                  f"Only {self.max_errors} test {results} are shown!"
                desc_unique.append(truncation_hint)
            unique_errors[error] = desc_unique

        error_header = self._error_header(unique_errors, num_total_errors)
        function_hints = self._error_hints(unique_errors)
        return f'Error: {error_header}\n\n{function_hints}'

    def _error_header(self, unique_errors: Dict[str, List[str]], num_total_errors: int):
        """
        Returns the header of the error message.

        :param unique_errors: Maps to each function or procedure a list of error descriptions.
        The list does not include duplicates.
        :param num_total_errors: The total number of errors including duplicates
        """
        num_unique_errors = len(unique_errors)
        erroneous_functions = ', '.join([f"'{x}'" for x in unique_errors])
        if self.unit_test is True:
            functions = 'functions' if num_unique_errors > 1 else 'function'
            cases = 'cases' if num_total_errors > 1 else 'case'
            error_header = f"Unexpected result for {functions} {erroneous_functions}! " \
                           f"In total {num_total_errors} test {cases} failed:"
        else:
            tests = 'tests' if num_unique_errors > 1 else 'test'
            error_header = f"Procedural {tests} {erroneous_functions} failed!"
        return error_header

    @staticmethod
    def _error_hints(unique_errors: Dict[str, List[str]]) -> str:
        """
        Returns a string describing the errors per function or procedure.

        :param unique_errors: Maps to each function or procedure a list of error descriptions.
        The list does not include duplicates.
        """
        function_hints = []
        for fun, hints in unique_errors.items():
            hint_content = [hint for hint in hints if hint]
            if not hint_content:
                hint_content = ["There are no additional hints. Please read the exercise "
                                "description very carefully!"]
            hint_content = '\n\n'.join(hint_content)
            msg = f"'{fun}':\n{'=' * (len(fun) + 3)}\n{hint_content}"
            function_hints.append(msg)
        function_hints = '\n\n\n'.join(function_hints)
        return function_hints

    @classmethod
    def from_config(cls, test_config: Dict[str, Any]):
        """
        Configure a test runner for python grey-box tests with a given yaml-file.

        :param test_config: File name of the yaml-file containing test configuration
        """
        translation_unit = test_config.get('translation_unit', _DEFAULT_TRANSLATION_UNIT)
        test_name = test_config.get('name', 'TestGreyBox')
        default_make_target = pathlib.Path(translation_unit).stem + '.so'
        library_name = test_config.get('library_name', default_make_target)
        requirements = test_config.get('requirements', [])
        sourcecode_runner = SourcecodeRunner.from_config(test_config, default_make_target)
        unit_test = test_config.get('unit_test', True)
        max_errors = test_config.get('max_errors', 0)
        class_name = test_config.get('class', 'GreyBoxTest')
        module_path = test_config.get('module_path', 'grey_box.py')
        my_class = cls._load_class(class_name, module_path)

        return cls(test_name, library_name, my_class, sourcecode_runner, requirements, unit_test,
                   max_errors)

    @staticmethod
    def _load_class(class_name: str, module_path: str):
        """
        Dynamically loads a class.

        :param class_name: The name of the class to be loaded
        :param module_path: The path to the module in which the class is implemented
        """
        module_path = pathlib.Path(module_path)
        loader = importlib.machinery.SourceFileLoader(module_path.stem, str(module_path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return getattr(module, class_name)


class TestCompile(AbstractTest):
    """
    Test runner for compilation tests. Compiles the C source code and check if the compilation was
    successful.
    """
    __test__ = False

    def _run_test(self):
        try:
            self.sourcecode_runner.build_executable()
        except subprocess.CalledProcessError as error:
            msg = f"Error: Compilation using '{error.stdout}' failed with:\n{error.stderr}"
            self._fail(msg)

    @classmethod
    def from_config(cls, test_config: Dict[str, Any]):
        """
        Configure a test runner for compilation tests with a given yaml-file.

        :param test_config: File name of the yaml-file containing test configuration
        """
        test_name = test_config.get('name', 'TestCompile')
        sourcecode_runner = SourcecodeRunner.from_config(test_config)
        requirements = test_config.get('requirements', [])

        return cls(test_name, sourcecode_runner, requirements)


class CTest:
    """
    Loads a shared library which executes tests.

    The execution, verification, and error reporting is left to the user. The shared library has
    to implement at least four function:

    - `void *fact_init(char *lib_name)`: Initializes the test run and loads the shared library
      compiled from the student solution
    - `int fact_tests(void *ptr)`: Tests the solution
    - `int fact_errors(void *ptr, char *error, size_t max_error_len)`: Reports errors
    - `int fact_free(void *ptr)`: Frees resources
    """
    shared_lib_student: str
    c_array_size: int
    makefile_directory: pathlib.Path

    def __init__(self, makefile_directory, shared_lib_test: str, shared_lib_student,
                 c_array_size: int = 8192) -> None:
        self.shared_lib_student = shared_lib_student
        self.makefile_directory = pathlib.Path(makefile_directory)
        self.c_array_size = c_array_size
        library = cdll.LoadLibrary(str(self.makefile_directory / shared_lib_test))

        self.init = library.fact_init
        self.init.argtypes = [ctypes.c_char_p]
        self.init.restype = ctypes.c_void_p

        self.tests = library.fact_tests
        self.tests.argtypes = [ctypes.c_void_p]
        self.tests.restype = ctypes.c_int

        self.errors = library.fact_errors
        self.errors.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        self.errors.restype = ctypes.c_int

        self.free = library.fact_free
        self.free.argtypes = [ctypes.c_void_p]
        self.free.restype = ctypes.c_int

    def run_tests(self) -> Optional[str]:
        """
        Executes the shared library to test the solution.

        :returns: An error message, if an error occurs.
        """
        obj = None
        c_test_lib_name = char_arr_p2c(str(self.makefile_directory / self.shared_lib_student))
        try:
            obj = self.init(c_test_lib_name)
            result = self.tests(obj)
            if result != 0:
                c_array = char_arr_c(self.c_array_size)
                errors = self.errors(obj, c_array, self.c_array_size)
                if errors != 0:
                    logging.info("Output was truncated!")
                p_array = c_array.value.decode()
                return str(p_array)
        finally:
            if obj:
                self.free(obj)
        return None


def _run_grey_box_c_process(pipe: Pipe, makefile_directory: Union[str, pathlib.Path],
                            library_name_test: str,
                            library_name_student: str, max_error_len: int):
    p_output, p_input = pipe
    p_output.close()
    ctest = CTest(makefile_directory, library_name_test, library_name_student, max_error_len)
    errors = ctest.run_tests()
    if errors is not None:
        p_input.send(errors)
    p_input.close()


class TestGreyBoxC(AbstractTimeoutTest):
    """
    Test runner for grey-box tests written in C.
    """
    __test__ = False

    library_name_student: str
    library_name_test: str
    make_target_test: str
    max_error_len: int

    def __init__(self, test_name: str, make_target_test: str, library_name_student: str,
                 library_name_test: str, sourcecode_runner: SourcecodeRunner,
                 requirements: List[str] = None, max_error_len: int = 8192):
        """

        :param test_name:  An unique test case name.
        :param make_target_test: Target name of the Makefile rule to be used for c tests
        :param library_name_student: Name of the students shared library
        :param library_name_test: Name of the c tests shared library
        :param sourcecode_runner: A runner to build an executable and execute it
        :param requirements: A list of test cases names that have to pass for this test to be run.
        """
        super().__init__(test_name, sourcecode_runner, requirements)
        self.library_name_student = library_name_student
        self.library_name_test = library_name_test
        self.make_target_test = make_target_test
        self.max_error_len = max_error_len

    def _run_with_timeout(self):
        try:
            self.sourcecode_runner.build_executable()
        except subprocess.CalledProcessError:
            self._fail(self.sourcecode_runner.unexpected_error_msg_make())

        try:
            self.sourcecode_runner.build_executable(self.make_target_test)
        except subprocess.CalledProcessError:
            self._fail(self.sourcecode_runner.unexpected_error_msg_make(self.make_target_test))

        p_output, p_input = Pipe()
        process = Process(target=_run_grey_box_c_process, args=(
            (p_output, p_input), self.sourcecode_runner.makefile_directory, self.library_name_test,
            self.library_name_student, self.max_error_len))
        exitcode, errors = run_process(process, p_input, p_output)

        if exitcode != 0:
            self._fail(error_msg_exit_code(exitcode))
        if errors is not None:
            self._fail(errors)

    @classmethod
    def from_config(cls, test_config: Dict[str, Any]):
        """
        Configure a test runner for C/C++ grey-box tests with a given yaml-file.

        :param test_config: File name of the yaml-file containing test configuration
        """
        translation_unit = test_config.get('translation_unit', _DEFAULT_TRANSLATION_UNIT)
        tu_stem = pathlib.Path(translation_unit).stem
        test_name = test_config.get('name', 'TestGreyBoxC')
        library_name_student = test_config.get('library_name_student', tu_stem + '.so')
        library_name_test = test_config.get('library_name_test', 'ctest.so')
        make_target_test = test_config.get('make_target_test', 'ctest.so')
        max_error_len = test_config.get('max_error_len', 8192)
        sourcecode_runner = SourcecodeRunner.from_config(test_config)
        requirements = test_config.get('requirements', [])

        return cls(test_name, make_target_test, library_name_student, library_name_test,
                   sourcecode_runner, requirements, max_error_len)


class TestOclint(AbstractTest):
    """
    Test runner for static code analysis using OCLint.
    """
    __test__ = False

    translation_unit: str
    suppress_line: bool
    suppress_range: bool
    disable_rules: List[str]
    apply_rules: List[str]

    def __init__(self, test_name: str, translation_unit: str,
                 sourcecode_runner: SourcecodeRunner, requirements: List[str] = None,
                 suppress_line: bool = False, suppress_range: bool = False,
                 disable_rules: Optional[List[str]] = None,
                 apply_rules: Optional[List[str]] = None):
        """

        :param test_name: An unique test case name.
        :param translation_unit: path to translation unit
        :param sourcecode_runner: A runner to build an executable and execute it
        :param requirements: A list of test cases names that have to pass for this test to be run.
        :param suppress_line: Are students allowed to suppress OCLint warnings using the !OCLint
        comment?
        :param suppress_range: Are students allowed to suppress OCLint warnings using Annotations?
        :param disable_rules: A list of rules which should be disabled.
        :param apply_rules: A list of rules which should considered.
        If non are provided all rules are used.
        """
        super().__init__(test_name, sourcecode_runner, requirements)

        self.translation_unit = translation_unit
        self.suppress_line = suppress_line
        self.suppress_range = suppress_range
        self.disable_rules = disable_rules
        self.apply_rules = apply_rules

    def _run_test(self) -> None:
        if not self.suppress_line or not self.suppress_range:
            self._check_suppressed_warnings()
        self._execute_oclint()

    def _check_suppressed_warnings(self) -> None:
        """
        Checks whether students tried to suppress oclint warnings even though they are not allowed
        to suppress warnings
        """
        program = pathlib.Path(self.sourcecode_runner.sourcecode_directory) / self.translation_unit
        translation_unit = parse_c_file(str(program))

        if not self.suppress_range:
            self._check_suppressed_range(translation_unit)
        if not self.suppress_line:
            self._check_suppressed_line(translation_unit)

    def _check_suppressed_range(self, translation_unit) -> None:
        """
        Checks whether students tried to suppress oclint warnings using an annotation.
        Fails if an oclint:suppress annotation is used.

        :param translation_unit: Parsed translation unit
        """
        oclint_annotations = []
        for annotation in annotation_cursors(translation_unit):
            if annotation.spelling.startswith('oclint:suppress'):
                oclint_annotations.append(annotation)
        if oclint_annotations:
            message = "You are not supposed to suppress OCLint warnings using annotations!"
            self._suppression_error(oclint_annotations, message)

    def _check_suppressed_line(self, translation_unit) -> None:
        """
        Checks whether students tried to suppress oclint warnings using a comment.
        Fails if !OCLint Comment is used.

        :param translation_unit: Parsed translation unit
        """
        oclint_comments = []
        regex = re.compile(r'^//!\s*oclint', re.IGNORECASE)
        for comment in comment_token(translation_unit):
            if regex.match(comment.spelling):
                oclint_comments.append(comment)
        if oclint_comments:
            message = "You are not supposed to suppress OCLint warnings using '//!OCLint'!"
            self._suppression_error(oclint_comments, message)

    def _suppression_error(self, elements: Union[List[Token], List[Cursor]], message: str) -> None:
        """
        Marks the test as failed due to the unexpected suppression of OCLint rules.

        :param elements: Unexpected elements
        :param message: An error message used for each unexpected element
        """
        error_locations = []
        for element in elements:
            location = sourcecode_location(element.location)
            error_location = f"- {location}: {message}"
            error_locations.append(error_location)
        error_location = '\n'.join(error_locations)
        error_msg = f'Error: Unexpected suppression of OCLint rules detected!\n\n{error_location}'
        self._fail(error_msg)

    def _execute_oclint(self):
        """
        Executes OCLint. Fails if OCLint detects potential problems.
        """
        args = ['oclint', '-report-type json']
        if self.apply_rules:
            for rule in self.apply_rules:
                args.append(f'-rule {rule}')
        if self.disable_rules:
            for rule in self.disable_rules:
                args.append(f'-disable-rule {rule}')
        args += [str(self.translation_unit), '-- -c -DNDEBUG']
        args_str = ' '.join(args)
        result = subprocess.run(args_str, capture_output=True, text=True, shell=True,
                                cwd=self.sourcecode_runner.sourcecode_directory,
                                timeout=self.sourcecode_runner.make_timeout_sec, check=False)
        try:
            result.check_returncode()
        except subprocess.CalledProcessError as error:
            if error.returncode in [1, 2, 3, 4]:
                logging.error(error.stderr)
                error_msg = error_msg_internal_error_code(self.test_name, ErrorCodes.OCLINT_ERROR)
                self._fail(error_msg)
            if error.returncode == 6:
                logging.error(error.stderr)
                error_msg = error_msg_internal_error_code(self.test_name,
                                                          ErrorCodes.OCLINT_COMPILATION_ERROR)
                self._fail(error_msg)
        oclint_result = json.loads(result.stdout)
        violations = oclint_result['violation']
        if violations:
            self._oclint_error(violations)

    def _oclint_error(self, violations: List[Dict[str, Any]]) -> None:
        """
        Marks the test as failed due to violations found by OCLint.

        :param violations: Violations found by OCLint
        """
        oclint_errors = []
        doc_url = 'https://docs.oclint.org/en/stable/rules'
        sorted_violations = sorted(
            violations,
            key=lambda v: (v['path'], v['startLine'], v['startColumn'], v['rule'], v['message']))
        for violation in sorted_violations:
            path = pathlib.Path(violation['path']).name
            line = violation['startLine']
            column = violation['startColumn']
            rule = violation['rule']
            message = violation['message']
            if message:
                message = f': {message}'
            fragment = ''.join(rule.split())
            rule_url = f"{doc_url}/{violation['category']}.html#{fragment}"
            error_message = f'- {path}:{line}:{column}: {rule}{message} ' \
                            f'(additional information: {rule_url})'
            oclint_errors.append(error_message)
        oclint_error = '\n'.join(oclint_errors)
        error_msg = f'Error: Static code analysis revealed that you could improve the ' \
                    f'quality of your source code. The followings problems were found:\n\n' \
                    f'{oclint_error}'
        self._fail(error_msg)

    @classmethod
    def from_config(cls, test_config: Dict[str, Any]):
        """
        Configure a test runner for compilation tests with a given yaml-file.

        :param test_config: File name of the yaml-file containing test configuration
        """
        test_name = test_config.get('name', 'TestCompile')
        sourcecode_runner = SourcecodeRunner.from_config(test_config)
        requirements = test_config.get('requirements', [])
        translation_unit = test_config.get('translation_unit', _DEFAULT_TRANSLATION_UNIT)

        suppress_line = test_config.get('suppress_line', False)
        suppress_range = test_config.get('suppress_range', False)
        disable_rules = test_config.get('disable_rules', [])
        apply_rules = test_config.get('apply_rules', [])

        return cls(test_name, translation_unit, sourcecode_runner, requirements, suppress_line,
                   suppress_range, disable_rules, apply_rules)
