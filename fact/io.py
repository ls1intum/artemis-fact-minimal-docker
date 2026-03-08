"""
Parsing of the DSL for input-output tests
"""
import contextlib
import io
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from multiprocessing import Pipe
from multiprocessing.context import Process
from pathlib import Path
from typing import Optional, Tuple, List, Dict

from textx import metamodel_from_str

from fact._util import unescape, strip_trailing_whitespace, replace_non_printable_ascii
from fact.io_hint_exact import io_error_msg_exact

_LINE_WIDTH = 86
_TITLE_WIDTH = 30


def cname(cls) -> str:
    """
    Returns the class name

    :param cls: Class
    :return: The class name
    """
    return cls.__class__.__name__


class IOReplacement:
    """
    Configuration of a variable substitution where the value of a variable is replaced.
    """
    pattern: str
    replace: str
    hint: str
    num_matches: int

    def __init__(self, parent, pattern: str, replace: str, hint: str, num_matches: int) -> None:
        """

        :param parent: used by textx; do not use!
        :param pattern: regex pattern
        :param replace: replacement
        :param hint: hint, if the regex could not be found
        :param num_matches: number of expected matches of the provided regex
        """
        super().__init__()
        self.parent = parent
        self.pattern = pattern
        self.replace = replace
        self.hint = hint
        self.num_matches = num_matches


class IOSubstitution:
    """
    Configuration of a variable substitution where the value of a variable is replaced.
    """
    variable: str
    value: str
    hint: str
    num_matches: int

    def __init__(self, parent, variable: str, value: str, hint: str, num_matches: int) -> None:
        """

        :param parent: used by textx; do not use!
        :param variable: variable name
        :param value: new value of the variable
        :param hint: hint, if the regex could not be found
        :param num_matches: number of expected matches of the provided regex
        """

        super().__init__()
        self.parent = parent
        self.variable = variable
        self.value = value
        self.hint = hint
        self.num_matches = num_matches


class IOScriptExecutionError(Exception):
    """
    Raised when a script in a IO-Test does not terminate successfully.
    """


@contextlib.contextmanager
def _capture_stdout():
    new = io.StringIO()
    old = sys.stdout
    sys.stdout = new
    try:
        yield new
    finally:
        sys.stdout = old


def __exec_fun(source: str, pipe):
    p_output, p_input = pipe
    p_output.close()
    with _capture_stdout() as stdout:
        # pylint: disable=exec-used
        exec(source)
        # pylint: enable=exec-used
    p_input.send(stdout.getvalue())
    p_input.close()


def _exec_matched_code(match) -> str:
    p_output, p_input = Pipe()
    process = Process(target=__exec_fun, args=(match.group(1), (p_output, p_input),))
    process.start()
    p_input.close()
    process.join()
    exitcode = process.exitcode
    process.close()
    result = ''
    try:
        result = p_output.recv()
    except EOFError:
        pass
    p_output.close()
    if exitcode != 0:
        raise IOScriptExecutionError
    return result


def _exec_code(escape_sequence: str, text: str) -> str:
    esc = re.escape(escape_sequence)
    regex = esc + r'(.*)' + esc
    return re.subn(regex, _exec_matched_code, text)[0]


class IOTestSettings(ABC):
    """
    An abstract io test setting that every io test setting has to inherit from.
    """
    show_input: bool = False  #: Should the test input be shown in the error message?
    show_output: bool = True  #: Should the obtained output be shown?
    escape_sequence: Optional[str] = None
    show_substitution: bool = True  #: Should the substituted code be shown in the error message?
    printable_ascii: bool = False  #: Should the output contain only printable ascii characters?
    """
    The escape fraction. Code between two escape parts is executed.
    The code and the two escape parts are replaced with the resulting output on stdout.
    """
    hint: Optional[str] = None
    """
    A hint which may help students if they encounter an error in this test
    """


class IOTestSettingsRegex(IOTestSettings):
    """
    Settings of an io test with regex matching
    """
    show_error: bool = True  #: Should the obtained output on stderr be shown?


class IOTestSettingsExact(IOTestSettings):
    """
    Settings of an io test with exact matching
    """
    show_expected: bool = False  #: Should the expected output be shown?
    show_diff: bool = True  #: Should a diff of the expected and obtained outputs be shown?
    ignore_cases: bool = False  #: Should the cases be ignored?
    rstrip: bool = False  #: Should all whitespace characters at the end be ignored?
    line_rstrip: bool = False  #: Should all whitespaces & tabs at the end of each line be ignored?


class IOTestConfig(ABC):
    """
    An abstract io test configuration that each io test configuration has to inherit from
    """
    arguments: List[str]
    stdin: str
    return_code: List[int]
    settings: IOTestSettings

    replacements: List[IOReplacement]
    substitutions: List[IOSubstitution]

    def __init__(self, stdin: List[str], args: List[str], return_code: List[int],
                 replacements: List[IOReplacement], substitutions: List[IOSubstitution],
                 settings: IOTestSettings) -> None:
        """

        :param stdin: The input on stdin for the io-test
        :param args: The commandline parameters passed to the executable
        :param return_code: The expected return codes
        :param replacements: The replacements that have to be performed before io-testing
        :param substitutions: The substitutions that have to be performed before io-testing
        :param settings: The settings of the io-test
        """
        super().__init__()
        self.stdin = '\n'.join(stdin)
        self.arguments = args
        self.return_code = return_code
        self.replacements = replacements
        self.substitutions = substitutions
        self.settings = settings
        if self.settings.escape_sequence:
            self.stdin = self._exec_code(self.stdin)
            self.arguments = [self._exec_code(arg) for arg in args]

    def check_return_value(self, obtained_return_value: int) -> bool:
        """
        Returns whether the obtained return value matches any of the expected return codes

        :param obtained_return_value: The obtained return_value
        :return: True, return value matches any of the expected return codes
        """
        return obtained_return_value in self.return_code

    @abstractmethod
    def check_stdout(self, obtained: str) -> bool:
        """
        Returns whether the obtained output matches the expected output on stdout

        :param obtained: The obtained output on stdout
        :return: True, if the obtained output matches the expected output
        """

    @abstractmethod
    def check_stderr(self, obtained: str) -> bool:
        """
        Returns whether the obtained output matches the expected output on stderr

        :param obtained: The obtained output on stderr
        :return: True, if the obtained output matches the expected output
        """

    @abstractmethod
    def test_results(self, output: Optional[subprocess.CompletedProcess],
                     substitution_description: List[str]):
        """
        Returns the test result of an io-test.

        :param output: The completed process
        :param substitution_description: Description of substitutions.
        :return: IOTestResults
        """

    def modifies_code(self) -> bool:
        """
        Returns whether the test configuration changes the source because of replacement or
        substitution.

        :return: True, if the code is modified.
        """
        return len(self.replacements) > 0 or len(self.substitutions) > 0

    def _exec_code(self, output: str):
        return _exec_code(self.settings.escape_sequence, output)


class IOTestConfigRegex(IOTestConfig):
    """
    Configuration of an input-output test with regex matching
    """

    stdout: str
    stderr: str
    settings: IOTestSettingsRegex

    def __init__(self, stdin: List[str], stdout: List[str], stderr: List[str], args: List[str],
                 return_code: List[int], replacements: List[IOReplacement],
                 substitutions: List[IOSubstitution], settings: IOTestSettingsRegex) -> None:
        """

        :param stdin: The input on stdin for the io-test
        :param stdout: The expected output on stdout
        :param stderr: The expected output on stderr
        :param args: The commandline parameters passed to the executable
        :param return_code: The expected return codes
        :param replacements: The replacements that have to be performed before io-testing
        :param substitutions: The substitutions that have to be performed before io-testing
        :param settings: The settings of the io-test
        """
        super().__init__(stdin, args, return_code, replacements, substitutions, settings)
        self.stdout = ''.join(stdout)
        self.stderr = ''.join(stderr)
        if self.settings.escape_sequence:
            self.stdout = self._exec_code(self.stdout)
            self.stderr = self._exec_code(self.stderr)

    def check_stdout(self, obtained: str) -> bool:
        return bool(re.fullmatch(self.stdout, obtained))

    def check_stderr(self, obtained: str) -> bool:
        return bool(re.fullmatch(self.stderr, obtained))

    def test_results(self, output: Optional[subprocess.CompletedProcess],
                     substitution_description: List[str]):
        return IOTestResultsRegex(self, output, substitution_description)


class IOTestConfigExact(IOTestConfig):
    """
    Configuration of an input-output test with regex matching
    """

    stdout: List[Tuple[str, str]]
    stderr: List[Tuple[str, str]]
    stdout_mod: str
    stderr_mod: str
    settings: IOTestSettingsExact

    def __init__(self, stdin: List[str], stdout: List[Tuple[str, str]],
                 stderr: List[Tuple[str, str]], args: List[str], return_code: List[int],
                 replacements: List[IOReplacement], substitutions: List[IOSubstitution],
                 settings: IOTestSettingsExact) -> None:
        """

        :param stdin: The input on stdin for the io-test
        :param stdout: The expected output on stdout
        :param stderr: The expected output on stderr
        :param args: The commandline parameters passed to the executable
        :param return_code: The expected return codes
        :param replacements: The replacements that have to be performed before io-testing
        :param substitutions: The substitutions that have to be performed before io-testing
        :param settings: The settings of the io-test
        """
        super().__init__(stdin, args, return_code, replacements, substitutions, settings)
        self.stdout = stdout
        self.stderr = stderr
        if settings.escape_sequence:
            self.stdout = self.__exec_code(self.stdout)
            self.stderr = self.__exec_code(self.stderr)
        self.stdout_mod = self.__modify_expected_output(self.stdout)
        self.stderr_mod = self.__modify_expected_output(self.stderr)

    def __modify_expected_output(self, output: List[Tuple[str, str]]) -> str:
        transformed = ''.join([out[0] for out in output])
        return self._transform_expected_output(transformed)

    def _transform_expected_output(self, output: str):
        if self.settings.ignore_cases:
            output = output.lower()
        if self.settings.line_rstrip:
            output = strip_trailing_whitespace(output)
        if self.settings.rstrip:
            output = output.rstrip()
        return output

    def _check_output(self, output: str, obtained: str) -> bool:
        obtained = self._transform_expected_output(obtained)
        output = re.escape(output)
        return bool(re.fullmatch(output, obtained))

    def __exec_code(self, output: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        return [(_exec_code(self.settings.escape_sequence, out[0]), out[1]) for out in output]

    def check_stdout(self, obtained: str) -> bool:
        return self._check_output(self.stdout_mod, obtained)

    def check_stderr(self, obtained: str) -> bool:

        return self._check_output(self.stderr_mod, obtained)

    def test_results(self, output: Optional[subprocess.CompletedProcess],
                     substitution_description: List[str]):
        return IOTestResultsExact(self, output, substitution_description)


class IOTestResults(ABC):
    """
    An abstract io test result that each io test result has to inherit from
    """
    test_return_value: bool
    test_stdout: bool
    test_stderr: bool
    test: IOTestConfig
    substitution_description: List[str]

    no_hint_msg = 'No hint available! Please, read the exercise description very carefully!'

    def __init__(self, test: IOTestConfig, output: Optional[subprocess.CompletedProcess],
                 substitution_description: List[str]) -> None:
        """

        :param test: Test configuration
        :param output: Test output
        """
        if output is None:
            self.test_return_value = False
            self.test_stdout = False
            self.test_stderr = False
            self.output = None
        else:
            self.test_return_value = test.check_return_value(output.returncode)
            self.test_stdout = test.check_stdout(output.stdout)
            self.test_stderr = test.check_stderr(output.stderr)
            self.output = output
        self.test = test
        self.substitution_description = substitution_description

    @staticmethod
    def _status_test_result(success: bool):
        return 'Correct' if success else 'Wrong'

    @staticmethod
    def _info_msg(msg_title: str, msg: str, alternative_msg: str = ''):
        msg_title += ' (none)' if not msg else ''
        title = msg_title + '\n' + '=' * len(msg_title) + '\n'
        msg_body = msg if msg else alternative_msg
        return title + msg_body + '\n'

    @staticmethod
    def _test_title(title='Test case'):
        title_sep = '-' * _LINE_WIDTH + '\n'
        return title.center(_LINE_WIDTH, ' ') + '\n' + title_sep

    def _test_input(self):
        param = ' '.join('"' + i + '"' for i in self.test.arguments)
        command_line = self._info_msg('Commandline parameters', param)
        stdin_title = self._info_msg('Input', self.test.stdin)
        return f'{command_line}\n{stdin_title}'

    def _status_test(self):
        status_test_cases = []

        len_return_code = len(self.test.return_code)
        if len_return_code == 1:
            description = f"{self._status_test_result(self.test_return_value)} return code! " \
                          f"Expected: '{self.test.return_code[0]}' " \
                          f"Obtained: '{abs(self.output.returncode)}'"
            status_test_cases.append(description)
        elif len_return_code > 1:
            return_codes = ', '.join([f"'{return_code}'" for return_code in self.test.return_code])
            description = f"{self._status_test_result(self.test_return_value)} return code! " \
                          f"Expected one of: [{return_codes}] " \
                          f"Obtained: '{abs(self.output.returncode)}'"
            status_test_cases.append(description)

        description = f"{self._status_test_result(self.test_stdout)} output on stdout!"
        status_test_cases.append(description)

        description = f"{self._status_test_result(self.test_stderr)} output on stderr!"
        status_test_cases.append(description)
        return self._info_msg('Status', '\n'.join(status_test_cases)) + '\n'

    def _changes_code(self) -> bool:
        return len(self.test.substitutions) > 0 or len(self.test.replacements) > 0

    def is_successful(self) -> bool:
        """
        Checks whether exit code, stdout and stderr are as expected.

        :return: True, if the test was successful
        """
        return self.test_return_value and self.test_stdout and self.test_stderr

    def timeout_msg(self, exec_timeout: int):
        """
        Returns a timeout error message

        :return: The error message
        """

        status_msg = f'Timeout: The execution of your program was canceled since it did not ' \
                     f'finish after {exec_timeout} seconds! This might indicate that there is ' \
                     f'some unexpected behavior (e.g., an endless loop) or that your program is' \
                     f' very slow!'

        return self.__generate_unexpected_error_msg(status_msg)

    def unicode_decode_msg(self, output: str):
        """
        Returns the error message for UnicodeDecodeError when reading the stdout and stderr output

        :param output: Erroneous output
        :return: The error message
        """

        status_msg = 'ERROR: Your program generates output containing invalid characters that ' \
                     'are outside of the ASCII range (below 0 or above 127)! ' \
                     'The invalid characters are represented with �.'

        output = replace_non_printable_ascii(output, '', '�')
        return self.__generate_unexpected_error_msg(status_msg, ("Erroneous output", output))

    def ascii_msg(self, output: str, stream: str):
        """
        Returns the error message if the output contains non printable ascii characters.
        All non printable ascii characters in output are replaced with a special character (�).

        :param output: Erroneous output
        :param stream: Stream on which the erroneous output occurred
        :return: The error message
        """

        status_msg = f'ERROR: Your output on {stream} contains non printable ASCII characters! ' \
                     f'The invalid characters are represented with �.'
        output = replace_non_printable_ascii(output)
        return self.__generate_unexpected_error_msg(status_msg, ("Erroneous output", output))

    def __generate_unexpected_error_msg(self, status_msg, error_description=None):
        title = self._test_title()
        status = self._info_msg('Status', status_msg) + '\n'
        error_msg_parts = [title, status]
        if error_description:
            error_description = self._info_msg(error_description[0], error_description[1]) + '\n'
            error_msg_parts.append(error_description)
        if self.test.settings.show_input:
            error_msg_parts.append(self._test_input())
        hint = self._info_msg('Hint', self.test.settings.hint, self.no_hint_msg)
        error_msg_parts.append(hint)
        return '\n'.join(error_msg_parts) + '\n\n\n'

    def _tested_code(self, source_file: Path) -> str:
        return self._info_msg('Tested code', source_file.read_text()) + '\n'

    @abstractmethod
    def error_msg(self, source_file: Path) -> str:
        """
        Returns the error message for the io test

        :param source_file: The path to source_file
        :return: Error message
        """


class IOTestResultsRegex(IOTestResults):
    """
    Test result of an io-test using regex matching.
    """
    test: IOTestConfigRegex

    def __init__(self, test: IOTestConfigRegex,
                 output: Optional[subprocess.CompletedProcess],
                 substitution_description: List[str]) -> None:
        super().__init__(test, output, substitution_description)

    def error_msg(self, source_file: Path) -> str:
        error_msg_parts = [self._test_title(), self._status_test()]
        if self.test.settings.show_input:
            error_msg_parts.append(self._test_input())
        if self.substitution_description:
            error_msg_parts.append(
                self._info_msg('Substitutions', '\n'.join(self.substitution_description)))
        if self.test.settings.show_output and self.test_stdout is False:
            error_msg_parts.append(self._info_msg('Obtained output on stdout', self.output.stdout))
        if self.test.settings.show_error and self.test_stderr is False:
            error_msg_parts.append(self._info_msg('Obtained output on stderr', self.output.stderr))
        if self.test.settings.show_substitution and self._changes_code():
            error_msg_parts.append(self._tested_code(source_file))
        error_msg_parts.append(self._info_msg('Hint', self.test.settings.hint, self.no_hint_msg))
        return '\n'.join(error_msg_parts) + '\n\n\n'


class IOTestResultsExact(IOTestResults):
    """
    Test result of an io-test using exact matching.
    """
    test: IOTestConfigExact

    def __init__(self, test: IOTestConfigExact,
                 output: Optional[subprocess.CompletedProcess],
                 substitution_description: List[str]) -> None:
        super().__init__(test, output, substitution_description)

    def _error_hint(self, obtained: str, expected: List[Tuple[str, Optional[str]]]) -> str:
        return io_error_msg_exact(obtained, expected, self.test.settings.ignore_cases,
                                  self.test.settings.rstrip, self.test.settings.line_rstrip)

    def _error_description_stream(self, obtained_output: str,
                                  expected_output: List[Tuple[str, str]], stream: str) -> List[str]:
        description = []
        if self.test.settings.show_output:
            description.append(self._info_msg(f'Obtained output on {stream}', obtained_output))
        if self.test.settings.show_expected:
            expected_stdout = ''.join([x[0] for x in expected_output])
            description.append(self._info_msg(f'Expected output on {stream}', expected_stdout))
        if self.test.settings.show_diff:
            description.append(self._info_msg(f'Hint {stream}',
                                              self._error_hint(obtained_output, expected_output)))
        return description

    def error_msg(self, source_file: Path) -> str:
        error_msg_parts = [self._test_title(), self._status_test()]
        if self.test.settings.show_input:
            error_msg_parts.append(self._test_input())

        if self.substitution_description:
            error_msg_parts.append(
                self._info_msg('Substitutions', '\n'.join(self.substitution_description)))

        if self.test_stdout is False:
            error_msg_parts += self._error_description_stream(self.output.stdout, self.test.stdout,
                                                              'stdout')

        if self.test_stderr is False:
            error_msg_parts += self._error_description_stream(self.output.stderr, self.test.stderr,
                                                              'stderr')
        if self.test.settings.show_substitution and self._changes_code():
            error_msg_parts.append(self._tested_code(source_file))

        error_msg_parts.append(self._info_msg('Hint', self.test.settings.hint, self.no_hint_msg))
        return '\n'.join(error_msg_parts) + '\n\n\n'


_GRAMMAR = """
Model: tests*=TestTypes;
TestTypes: TestRegex | TestExact;
TestRegex: begin=BeginRegex replacement*=IOReplacement substitution*=IOSubstitution arguments=TestArguments? statements*=StatementRegex delimiter=Delimiter;
TestExact: begin=BeginExact replacement*=IOReplacement substitution*=IOSubstitution arguments=TestArguments? statements*=StatementExact delimiter=Delimiter;
IOReplacement: 'r>' pattern=STRING replace=STRING hint=STRING num_matches=INT;
IOSubstitution: 's>' variable=STRING value=STRING hint=STRING num_matches=INT;
BeginRegex: 'start> matching="regex"' testSettings*=TestSettingsRegex;
BeginExact: 'start> matching="exact"' testSettings*=TestSettingsExact;
TestSettingsRegex: TestSettingShowInput | TestSettingShowOutput | TestSettingShowError | TestSettingHint | TestSettingEscape | TestSettingSubstitution | TestSettingPrintableASCII;
TestSettingsExact: TestSettingShowInput | TestSettingShowExpected | TestSettingShowDiff | TestSettingShowOutput | TestSettingHint | TestSettingIgnoreCases | TestSettingRstrip | TestSettingEscape | TestSettingSubstitution | TestSettingPrintableASCII | TestSettingLineRstrip;
TestSettingShowInput: 'show_input=' value=BOOL;
TestSettingShowOutput: 'show_output=' value=BOOL;
TestSettingShowError: 'show_error=' value=BOOL;
TestSettingHint: 'hint=' value=STRING;
TestSettingShowExpected: 'show_expected=' value=BOOL;
TestSettingShowDiff: 'show_diff=' value=BOOL;
TestSettingIgnoreCases: 'ignore_cases=' value=BOOL;
TestSettingRstrip: 'rstrip=' value=BOOL;
TestSettingLineRstrip: 'line_rstrip=' value=BOOL;
TestSettingEscape: 'escape=' value=STRING;
TestSettingSubstitution: 'show_substitution=' value=BOOL;
TestSettingPrintableASCII: 'printable_ascii=' value=BOOL;
TestArguments: 'p>' args+=STRING;
StatementRegex: Input | Output | Error | Variable;
StatementExact: Input | OutputExact | ErrorExact | Variable;
Input: 'i>' value=STRING;
Output: 'o>' value=STRING;
Error: 'e>' value=STRING;
Variable: 'v>' var_name=STRING value=STRING;
OutputExact: 'o>' value=STRING hint=STRING?;
ErrorExact: 'e>' value=STRING hint=STRING?;
Delimiter: 'end>' delimiter_value*=INT[','];
"""

_mm = metamodel_from_str(_GRAMMAR, classes=[IOSubstitution, IOReplacement])


def _substitute_variables(text: str, variables: Dict[str, str]) -> str:
    for var_name, value in variables.items():
        text = text.replace(var_name, value)
    return text


class IOParser:
    """
    Parses an input-output test definition using the DSL
    """
    tests: List[IOTestConfig]

    def __init__(self, input_text: str) -> None:
        """
        :param input_text: input text to parse
        """
        self.tests = []
        self._parse_input_text(input_text)

    def _parse_input_text(self, input_text):
        model = _mm.model_from_str(input_text)
        for test in model.tests:
            args = test.arguments.args if test.arguments else []
            return_code = test.delimiter.delimiter_value if test.delimiter.delimiter_value else []
            if cname(test) == 'TestRegex':
                settings = IOParser._map_settings_regex(test)
                stdin, stdout, stderr = IOParser._map_statements_regex(test)
                io_config = IOTestConfigRegex(stdin, stdout, stderr, args, return_code,
                                              test.replacement, test.substitution, settings)
                self.tests.append(io_config)

            elif cname(test) == 'TestExact':
                settings = IOParser._map_settings_exact(test)
                stdin, stdout, stderr = IOParser._map_statements_exact(test)
                io_config = IOTestConfigExact(stdin, stdout, stderr, args, return_code,
                                              test.replacement, test.substitution, settings)

                self.tests.append(io_config)

    @staticmethod
    def _map_statements_regex(command):
        variables = {}
        stdin, stdout, stderr = [], [], []
        for stmt in command.statements:
            if cname(stmt) == 'Variable':
                variables[stmt.var_name] = stmt.value
                continue
            value = _substitute_variables(stmt.value, variables)
            if cname(stmt) == 'Input':
                stdin.append(value)
            elif cname(stmt) == 'Output':
                stdout.append(value)
            elif cname(stmt) == 'Error':
                stderr.append(value)
        return stdin, stdout, stderr

    @staticmethod
    def _map_statements_exact(command):
        variables = {}
        stdin, stdout, stderr = [], [], []
        for stmt in command.statements:
            if cname(stmt) == 'Variable':
                variables[stmt.var_name] = stmt.value
                continue
            value = _substitute_variables(stmt.value, variables)
            if cname(stmt) == 'Input':
                stdin.append(value)
                continue
            value = unescape(value)
            hint = unescape(stmt.hint)
            if cname(stmt) == 'OutputExact':
                stdout.append((value, hint))
            elif cname(stmt) == 'ErrorExact':
                stderr.append((value, hint))
        return stdin, stdout, stderr

    @staticmethod
    def _map_setting(item, settings):
        if cname(item) == 'TestSettingShowInput':
            settings.show_input = item.value
        elif cname(item) == 'TestSettingShowOutput':
            settings.show_output = item.value
        elif cname(item) == 'TestSettingHint':
            settings.hint = item.value
        elif cname(item) == 'TestSettingEscape':
            settings.escape_sequence = item.value
        elif cname(item) == 'TestSettingSubstitution':
            settings.show_substitution = item.value
        elif cname(item) == 'TestSettingPrintableASCII':
            settings.printable_ascii = item.value
        else:
            return False
        return True

    @staticmethod
    def _map_settings_regex(command) -> IOTestSettingsRegex:
        settings = IOTestSettingsRegex()
        if command.begin.testSettings:
            for item in command.begin.testSettings:
                if IOParser._map_setting(item, settings):
                    continue
                if cname(item) == 'TestSettingShowError':
                    settings.show_error = item.value
        return settings

    @staticmethod
    def _map_settings_exact(command) -> IOTestSettingsExact:
        settings = IOTestSettingsExact()
        if command.begin.testSettings:
            for item in command.begin.testSettings:
                if IOParser._map_setting(item, settings):
                    continue
                if cname(item) == 'TestSettingShowExpected':
                    settings.show_expected = item.value
                elif cname(item) == 'TestSettingIgnoreCases':
                    settings.ignore_cases = item.value
                elif cname(item) == 'TestSettingShowDiff':
                    settings.show_diff = item.value
                elif cname(item) == 'TestSettingRstrip':
                    settings.rstrip = item.value
                elif cname(item) == 'TestSettingLineRstrip':
                    settings.line_rstrip = item.value
        return settings
