"""
Infrastructure for test case registration and reporting.
"""
import copy
import json
import logging
import os
import pathlib
from datetime import timedelta
from os import makedirs
from typing import Dict, Any, Optional, Union
from xml.etree import ElementTree as Et

import jsonschema
import ruamel.yaml

from fact._util import shorten_text, MAX_OUTPUT_CHAR
from fact.test_cases import TestCompile, TestIO, TestCodeStructure, TestGreyBox, TestStatus, \
    AbstractTest, TestGreyBoxC, ConfigurationError, TestOclint

_LINE_WIDTH = 80
_FACT_VERSION_STRING = "FACT-0.0.5"


def validate_test_config(config_file) -> bool:
    """
    Returns whether the test configuration is valid and all prerequisites are
    met (e.g., makefiles are present).

    :return: True, if the test configuration is valid.
    """
    file_contents = _load_config(config_file)

    schema_path = pathlib.Path(os.path.dirname(__file__)) / 'schemas' / 'config.schema.json'
    schema = json.loads(schema_path.read_text())

    validator = jsonschema.Draft7Validator(schema)
    validation_errors = list(validator.iter_errors(file_contents))
    if validation_errors:
        for error in validation_errors:
            logging.error(error)
        return False
    return True


def _load_config(config_file: Union[str, pathlib.Path]):
    """
    Loads a yaml- config file.

    :param config_file: The yml-file
    :return: The contents of the yml-file as dict
    """
    config = pathlib.Path(config_file)
    if not config.is_file():
        raise ConfigurationError(f"The config file '{config_file}' is not present!")
    yaml = ruamel.yaml.YAML(typ='safe')
    conf_dict = yaml.load(config)
    return conf_dict


class NotExecutedError(Exception):
    """
    Raised if the results are checked before they are computed.
    """


class TestCase:
    """
    A single test case (e.g., test compilation or IO).
    """
    __test__ = False

    stdout: str
    stderr: str
    tester_output: str

    name: str
    time: timedelta
    result: TestStatus
    message: str

    def __init__(self, name: str) -> None:
        """
        :param name: Test name
        """
        self.name = name

        self.stdout: str = ""
        self.stderr: str = ""
        self.tester_output: str = ""
        self.time: timedelta = timedelta()
        self.result: TestStatus = TestStatus.SUCCESS
        self.message: str = ""

    def to_xml(self, suite: Et.Element, max_chars_per_output: int = MAX_OUTPUT_CHAR) -> None:
        """
        Adds the results of the test to a XML test suite

        :param suite: XML representation of the test suite
        :param max_chars_per_output: Maximal numbers of characters for stdout and stderr of the test
                                     output
        :return:
        """
        case: Et.Element = Et.SubElement(suite, "testcase")
        case.set("name", self.name)
        case.set("time", str(self.time.total_seconds()))

        if self.result != TestStatus.SUCCESS:
            result: Et.Element = Et.SubElement(case, str(self.result.value))
            result.set("message", self.message)
            result.text = shorten_text(self.message, max_chars_per_output)

        if self.stdout:
            stdout: Et.Element = Et.SubElement(case, "system-out")
            stdout.text = shorten_text(self.stdout, max_chars_per_output) + "\n"
        if self.stderr:
            stderr: Et.Element = Et.SubElement(case, "system-err")
            stderr.text = shorten_text(self.stderr, max_chars_per_output) + "\n"


class TestSuite:
    """
    Test suite comprising multiple test cases.
    """
    __test__ = False

    cases: Dict[str, TestCase]

    name: str
    tests: int
    failures: int
    errors: int
    skipped: int
    successful: int
    time: timedelta

    def __init__(self, name: str) -> None:
        """
        :param name: Test suite name
        """
        self.name = name

        self.cases: Dict[str, TestCase] = {}
        self.tests: int = 0
        self.failures: int = 0
        self.errors: int = 0
        self.skipped: int = 0
        self.successful: int = 0
        self.time: timedelta = timedelta()

    def add_case(self, case: TestCase) -> None:
        """
        Adds a test case to the test suite.

        :param case: Test case to add
        :return: None
        """
        self.cases[case.name] = case
        self.tests += 1
        self.time += case.time

        if case.result == TestStatus.ERROR:
            self.errors += 1
        elif case.result == TestStatus.FAILURE:
            self.failures += 1
        elif case.result == TestStatus.SKIPPED:
            self.skipped += 1
        else:
            self.successful += 1

    def to_xml(self) -> Et.Element:
        """

        :return: Results of the test suite as a XML element
        """
        suite: Et.Element = Et.Element("testsuite")
        suite.set("name", self.name)
        suite.set("tests", str(self.tests))
        suite.set("failures", str(self.failures))
        suite.set("errors", str(self.errors))
        suite.set("skipped", str(self.skipped))
        suite.set("time", str(self.time.total_seconds()))

        for _, case in self.cases.items():
            case.to_xml(suite)
        return suite

    def get_test_cases(self) -> Dict[str, TestCase]:
        """
        Returns the test cases
        :return: Dict of str -> TestCase: Maps the test case name to a TestCase
        """
        return self.cases


class Tester:
    """
    Test runner used to add, execute tests and export the test results.
    """
    __test__ = False

    name: str
    suite: TestSuite
    tests: Dict[str, Any]
    executed: bool

    def __init__(self, name: str = _FACT_VERSION_STRING, logging_level=logging.DEBUG):
        """
        :param name: Name of the tester
        """
        self.name = name
        self.suite = TestSuite(name)
        self.tests = {}
        self.logging_level = logging_level
        self.executed = False

    @classmethod
    def from_config(cls, config_file: Union[str, pathlib.Path], name: str = _FACT_VERSION_STRING,
                    logging_level=logging.DEBUG):
        """
        Configure a tester with a given yaml-file.

        :param config_file: The yml-file
        :param name: The name of the tester
        :param logging_level: Debugging level used for printing on stdout
        :return: The Tester
        """
        tester = cls(name, logging_level)
        conf_dict = _load_config(config_file)

        tester._map_config_to_test_types(conf_dict)
        return tester

    @classmethod
    def from_dict(cls, conf_dict: Dict[str, Any], makefile_directory: Optional[str] = None,
                  sourcecode_directory: Optional[str] = None, name: str = _FACT_VERSION_STRING,
                  logging_level=logging.DEBUG):
        """
        Configure a tester with a given dict.

        :param conf_dict: Dict containing the test configuration
        :param makefile_directory: The directory in which the Makefile resides
        :param sourcecode_directory: The directory in which the translation unit resides
        :param name: The name of the tester
        :param logging_level: Debugging level used for printing on stdout
        :return: The Tester
        """
        tester = cls(name, logging_level)
        tester._map_config_to_test_types(conf_dict, makefile_directory, sourcecode_directory)
        return tester

    def _map_config_to_test_types(self, config: Dict[str, Any],
                                  makefile_directory: Optional[str] = None,
                                  sourcecode_directory: Optional[str] = None):
        translation_unit = config.get('translation_unit', None)
        for test_config in config['tests']:
            test_config_cpy = self._setup_test_config(test_config, makefile_directory,
                                                      sourcecode_directory, translation_unit)
            test = self._map_config_to_test(test_config_cpy)
            self.add_test(test)

    @staticmethod
    def _setup_test_config(test_config, makefile_directory, sourcecode_directory, translation_unit):
        test_config_cpy = copy.copy(test_config)
        if 'translation_unit' not in test_config_cpy and translation_unit is not None:
            test_config_cpy['translation_unit'] = translation_unit
        if makefile_directory is not None:
            test_config_cpy['makefile_directory'] = makefile_directory
        if sourcecode_directory is not None:
            test_config_cpy['sourcecode_directory'] = sourcecode_directory
        return test_config_cpy

    @staticmethod
    def _map_config_to_test(test_config: Dict[str, Any]) -> AbstractTest:
        test_type = test_config['type']
        if test_type == 'compile':
            test = TestCompile.from_config(test_config)
        elif test_type == 'io':
            test = TestIO.from_config(test_config)
        elif test_type == 'structural':
            test = TestCodeStructure.from_config(test_config)
        elif test_type == 'grey_box':
            test = TestGreyBox.from_config(test_config)
        elif test_type == 'grey_box_c':
            test = TestGreyBoxC.from_config(test_config)
        elif test_type == 'oclint':
            test = TestOclint.from_config(test_config)
        else:
            raise ConfigurationError(f"Unknown test type '{test_type}'!")
        return test

    def run(self) -> None:
        """
        Starts the tester and runs all tests added via "add_test(test: AbstractTest)".
        """

        logging.basicConfig(level=self.logging_level,
                            format='[%(asctime)s %(filename)s] %(message)s')
        logging.info("Running: %s", self.name)

        test_results: Dict[str, TestStatus] = {}

        for name, test in self.tests.items():
            logging.info(test.start_msg())
            case = TestCase(test.test_name)

            if not self.__check_test_requirements(test, test_results):
                Tester.__skip_test(case, test)
                self.suite.add_case(case)
                continue

            test.start(case)
            self.suite.add_case(case)

            logging.info("Finished test case '%s' in %s seconds.", name,
                         test.case.time.total_seconds())

            test_results[name] = test.case.result
        self.__print_result()
        self.executed = True

    @staticmethod
    def __skip_test(case, test):
        logging.info(
            "Skipping test case '%s' not all requirements (%s) are fulfilled",
            test.test_name,
            str(test.requirements))
        case.block_id = f"Test was skipped! The test requires other test cases to succeed first " \
                        f"({', '.join(test.requirements)})."
        case.result = TestStatus.SKIPPED
        case.time = timedelta()

    @staticmethod
    def __check_test_requirements(test: AbstractTest, test_results: Dict[str, TestStatus]) -> bool:
        """
        Checks if all requirements of the current test (e.g., other test cases were successful) are
        fulfilled.

        :param test_results: Test results of previous tests
        :return: True, if all requirements are fulfilled
        """

        for req in test.requirements:
            if req not in test_results or test_results[req] is not TestStatus.SUCCESS:
                return False
        return True

    def add_test(self, test: Any) -> None:
        """
        Adds a new test that will be run once "run()" is invoked.

        :raise NameError: If the test_name of the provided test has already been added.
        :param test: Test to add
        :return: None
        """

        if test.test_name in self.tests:
            raise NameError(
                f"Test '{test.test_name}' already registered. Test names should be unique!")
        self.tests[test.test_name] = test

    def __print_result(self) -> None:
        """
        Logs some test statistics.

        :return: None
        """
        logging.info(" Result ".center(80, "="))
        logging.info(
            "%s finished %s test cases in %s seconds.", self.name, len(self.tests),
            self.suite.time.total_seconds())
        logging.info("SUCCESS: %s", self.suite.successful)
        logging.info("FAILED: %s", self.suite.failures)
        logging.info("ERROR: %s", self.suite.errors)
        logging.info("SKIPPED: %s", self.suite.skipped)
        logging.info("".center(80, "="))

    def export_result(self, output_path: str = "../test-reports/tests-results.xml") -> None:
        # pylint: disable=line-too-long
        """
        Exports the test results into a JUnit format and stores it at the given output_path.
        The JUnit format is based on [#]_.

        :param output_path: Path used to store the export
        :return: None

        .. [#] https://github.com/junit-team/junit5/blob/master/platform-tests/src/test/resources/jenkins-junit.xsd
        """
        # pylint: enable=line-too-long

        suite_xml: Et.Element = self.suite.to_xml()
        tree: Et.ElementTree = Et.ElementTree(suite_xml)
        makedirs(pathlib.Path(output_path).parent, exist_ok=True)
        tree.write(output_path, xml_declaration=True)

    def successful(self) -> Optional[bool]:
        """
        Returns whether all tests were executed successfully.

        :return: True, if all tests could be executed successfully. If the tests were not yet
                 executed None is returned.
        """
        if not self.executed:
            raise NotExecutedError
        return self.suite.successful == len(self.tests)
