"""
Structural test implementation
"""
import copy
import os
import pathlib
from typing import List, Set, Dict, Any, Optional, Generator

import networkx as nx
from clang.cindex import TranslationUnit, Index, CursorKind, TokenKind, Diagnostic, Cursor, Token, \
    SourceLocation

from fact._error import ErrorCodes, error_msg_instructor_test_config


def parse_c(string: str, tmp_file='/tmp/required_functions.c') -> TranslationUnit:
    """
    Parses a C-source string using clang

    :param string: C-code to parse
    :param tmp_file: Name of the in-memory file
    :return: Parsed source code as translation unit
    """
    idx = Index.create()
    translation_unit = idx.parse(tmp_file, args=['-std=c11'], unsaved_files=[(tmp_file, string)],
                                 options=TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
    return translation_unit


def parse_c_file(path: str, parsing_mode=TranslationUnit.PARSE_NONE) -> TranslationUnit:
    """
    Parses a C-source string using clang

    :param path: Path to the translation unit
    :param parsing_mode: The parsing mode
    :return: Parsed source code as translation unit
    """
    idx = Index.create()
    translation_unit = idx.parse(path, args=['-std=c11'], options=parsing_mode)
    return translation_unit


def annotation_cursors(translation_unit: TranslationUnit) -> Generator[Cursor, None, None]:
    """
    Returns a list of all cursors where an annotation attribute is found.

    :param translation_unit: Parsed translation unit
    :returns: The list of all annotation attribute cursors
    """
    for child in translation_unit.cursor.walk_preorder():
        if child.location.file is None or child.kind != CursorKind.ANNOTATE_ATTR:
            continue
        yield child


def comment_token(translation_unit: TranslationUnit) -> Generator[Token, None, None]:
    """
    Returns a list of all comment tokens.

    :param translation_unit: Parsed translation unit
    :returns: The list of all comment tokens
    """
    for token in translation_unit.cursor.get_tokens():
        # pylint: disable=no-member
        if token.kind != TokenKind.COMMENT:
            # pylint: enable=no-member
            continue
        yield token


def sourcecode_location(source_location: SourceLocation):
    """
    Returns a string describing the starting character of an element in the source code.

    :param source_location: The sourcecode location of the element
    :returns: The string
    """
    file = pathlib.Path(source_location.file.name).name
    line = source_location.line
    column = source_location.column
    return f"{file}:{line}:{column}"


_INPUT_FUNCTIONS = {'fgetc', 'fgets', 'fgetwc', 'fread', 'fscanf', 'fscanf_s', 'fwscanf',
                    'fwscanf_s', 'getc', 'getc_unlocked', 'getchar', 'getchar_unlocked', 'getdelim',
                    'getline', 'gets', 'gets_s', 'getw', 'getwc', 'getwchar', 'getwline', 'pread',
                    'read', 'readv', 'scanf', 'scanf_s', 'sscanf', 'sscanf_s', 'swscanf',
                    'swscanf_s', 'vfscanf', 'vfscanf_s', 'vfwscanf', 'vfwscanf_s', 'vscanf',
                    'vscanf_s', 'vsscanf', 'vsscanf_s', 'vswscanf', 'vswscanf_s', 'vwscanf',
                    'vwscanf_s', 'wscanf', 'wscanf_s'}
_OUTPUT_FUNCTIONS = {'ferror', 'fprintf', 'fprintf_s', 'fpurge', 'fputc', 'fputs', 'fputwc',
                     'fputws', 'fputws_l', 'fread', 'freopen', 'fropen', 'fwprintf', 'fwrite',
                     'perror', 'printf', 'printf_s', 'putc', 'putchar', 'puts', 'putw', 'putwc',
                     'putwchar', 'pwrite', 'vasprintf', 'vfprintf', 'vfprintf_s', 'vfwprintf',
                     'vfwprintf_s', 'vprintf', 'vprintf_s', 'vwprintf', 'vwprintf_s', 'wprintf',
                     'write', 'writev'}
_INSECURE_FUNCTIONS = {'fork', 'system', 'kill', 'killpg', 'execl', 'execle', 'execlp', 'execv',
                       'execvp', 'execvP', 'execve', 'execvpe', 'fexecve', 'vfork', 'clone',
                       'tkill', 'tgkill', 'execveat', 'pthread_create', 'thrd_create'}


class DiagnosticError(Exception):
    """
    Raised when a severe diagnostic error occurred during structural analysis.
    """
    error_message: str

    def __init__(self, error_message: str) -> None:
        super().__init__()
        self.error_message = error_message


class VariableDeclaration:
    """
    Representation of a C variable declaration
    """
    spelling: str
    variable_type: str
    location: str

    def __init__(self, spelling: str, variable_type: str, location: str) -> None:
        """

        :param spelling: variable name
        :param variable_type: data type of the variable
        :param location: location of the variable
        """
        super().__init__()
        self.spelling = spelling
        self.variable_type = variable_type
        self.location = location

    @staticmethod
    def create_from_parser(declaration):
        """
        Creates a variable declaration from the given declaration

        :param declaration: cursor to the variable declaration
        :return: The representation of the variable declaration
        """
        location = sourcecode_location(declaration.location)
        return VariableDeclaration(declaration.spelling, declaration.type.spelling, location)


class FunctionParameter:
    """
    Representation of a C function parameter
    """
    spelling: str
    variable_type: str

    def __init__(self, spelling: str, variable_type: str) -> None:
        """

        :param spelling: variable name
        :param variable_type: data type of the variable
        """
        super().__init__()
        self.spelling = spelling
        self.variable_type = variable_type

    @staticmethod
    def create_from_parser(declaration):
        """
        Creates a variable declaration from the given declaration

        :param declaration: cursor to the variable declaration
        :return: The representation of the variable declaration
        """
        return FunctionParameter(declaration.spelling, declaration.type.spelling)

    def __hash__(self):
        return hash((self.spelling, self.variable_type))

    def __eq__(self, o: object) -> bool:
        if isinstance(o, FunctionParameter):
            return self.spelling == o.spelling and self.variable_type == o.variable_type
        return NotImplemented


class FunctionBody:
    """
    Representation of a C function body
    """
    keywords: Set[str]
    function_calls: Set[str]
    variable_declarations: Set[FunctionParameter]
    calls: Set[str]

    def __init__(self) -> None:
        super().__init__()
        self.keywords = set()
        self.function_calls = set()
        self.variable_declarations = set()
        self.calls = set()

    def __add__(self, other):
        new = FunctionBody()
        new.keywords = self.keywords.union(other.keywords)
        new.function_calls = self.function_calls.union(other.function_calls)
        new.variable_declarations = self.variable_declarations.union(other.variable_declarations)
        return new


class FunctionPrototype:
    """
    Representation of a C function prototype
    """
    signature: str
    spelling: str
    return_type: str
    parameter_types: List[str]

    def __init__(self, spelling: str, return_type: str, signature: str) -> None:
        """

        :param spelling: function name
        :param return_type: return type of the function
        :param signature: signature of the function. i.e. name and parameter list.
        """
        super().__init__()
        self.spelling = spelling
        self.return_type = return_type
        self.parameter_types = []
        self.signature = signature

    def __str__(self) -> str:
        return f'{self.return_type} {self.signature}'

    def __eq__(self, o: object) -> bool:
        if isinstance(o, FunctionPrototype):
            return self.return_type == o.return_type and self.signature == o.signature
        return NotImplemented

    def __ne__(self, o: object) -> bool:
        return not self == o

    def __key(self):
        return self.return_type, self.signature

    def __hash__(self):
        return hash(self.__key())


class StructuralDefinition:
    """
    Specifies the expected code structure
    """
    expected_keywords: Set[str]
    disallowed_keywords: Set[str]
    allowed_function_calls: Set[str]
    disallowed_function_calls: Set[str]
    expected_function_calls: Set[str]
    expected_variable_declarations: Set[FunctionParameter]
    recursion: bool
    recursion_defined: bool

    def __init__(self, config: Dict[str, Any]) -> None:
        """

        :param config: Structural test configuration
        """
        super().__init__()

        set_allowed_fc = set(config.get('allowed_function_calls', []))
        set_disallowed_fc = set(config.get('disallowed_function_calls', []))
        set_expected_fc = set(config.get('expected_function_calls', []))

        self.allowed_function_calls = set()
        self.disallowed_function_calls = set()

        if config.get('insecure') is False:
            self.disallowed_function_calls = self.disallowed_function_calls.union(
                _INSECURE_FUNCTIONS)

        stdin = config.get('input')
        if stdin is False:
            self.disallowed_function_calls = self.disallowed_function_calls.union(
                _INPUT_FUNCTIONS.difference(set_allowed_fc).difference(set_expected_fc))
        elif stdin or 'input' in config:
            self.allowed_function_calls = self.allowed_function_calls.union(
                _INPUT_FUNCTIONS.difference(set_disallowed_fc))

        stdout = config.get('output')
        if stdout is False:
            self.disallowed_function_calls = self.disallowed_function_calls.union(
                _OUTPUT_FUNCTIONS.difference(set_allowed_fc).difference(set_expected_fc))
        elif stdout or 'output' in config:
            self.allowed_function_calls = self.allowed_function_calls.union(
                _OUTPUT_FUNCTIONS.difference(set_disallowed_fc))

        self.expected_keywords = set(config.get('expected_keywords', []))
        self.disallowed_keywords = set(config.get('disallowed_keywords', []))
        self.allowed_function_calls = self.allowed_function_calls.union(set_allowed_fc)
        self.disallowed_function_calls = self.disallowed_function_calls.union(set_disallowed_fc)
        self.recursion_defined = 'recursion' in config
        self.recursion = config.get('recursion')

        self.expected_variable_declarations = set()
        if 'expected_variable_declarations' in config:
            translation_unit = parse_c('\n'.join(config['expected_variable_declarations']))
            for child in translation_unit.cursor.walk_preorder():
                if child.kind == CursorKind.VAR_DECL:
                    self.expected_variable_declarations.add(
                        FunctionParameter.create_from_parser(child))

        self.expected_function_calls = set_expected_fc

    def extend(self, global_def, local_def) -> None:
        """
        Extends the structural definition by the difference of the global and the local structural
        definition.

        :param global_def: Global structural definition
        :param local_def: Local (function) structural definition
        :return: None
        """

        StructuralDefinition._extend_set(
            self.expected_keywords, global_def.expected_keywords, local_def.disallowed_keywords)

        StructuralDefinition._extend_set(
            self.disallowed_keywords, global_def.disallowed_keywords, local_def.expected_keywords)

        StructuralDefinition._extend_set(
            self.allowed_function_calls, global_def.allowed_function_calls,
            local_def.disallowed_function_calls)

        StructuralDefinition._extend_set(
            self.disallowed_function_calls, global_def.disallowed_function_calls,
            local_def.allowed_function_calls.union(local_def.expected_function_calls))

        if local_def.recursion_defined:
            self.recursion = local_def.recursion
        else:
            self.recursion = global_def.recursion

        self.expected_variable_declarations = global_def.expected_variable_declarations.union(
            local_def.expected_variable_declarations)
        self.expected_function_calls = global_def.expected_function_calls.union(
            local_def.expected_function_calls)

    @staticmethod
    def _extend_set(merge: Set[str], global_def: Set[str], local_def: Set[str]):
        """
        Extends merge by the difference of global and local
        :param merge: Set that is extended
        :param global_def: Set with global definitions
        :param local_def: Set with local definitions
        :return: The e
        """
        global_without_local = global_def.difference(local_def)
        merged = merge.union(global_without_local)
        merge.update(merged)


class StructuralRequirements:
    """
    Collection of all structural definitions (global and functions) and global requirements
    """
    main_file: str
    compile_args: List[str]
    disallowed_includes: List[str]
    required_function_prots: List[FunctionPrototype]
    _translation_unit_req: StructuralDefinition
    _function_req: Dict[str, StructuralDefinition]
    global_variables: bool

    def __init__(self, config, translation_unit, sourcecode_directory) -> None:
        """

        :param config: Dict containing the test configuration
        :param translation_unit: Filename of the translation_unit
        :param sourcecode_directory: The directory in which the translation unit resides
        """
        super().__init__()
        self._function_req = {}
        self.required_function_prots = []

        file = pathlib.Path(sourcecode_directory) / translation_unit

        if not file.is_file() or file.suffix != '.c':
            raise IOError(
                error_msg_instructor_test_config(ErrorCodes.STRUCTURAL_TEST_MAIN_FILE_NOT_FOUND))
        self.main_file = str(file)

        if 'required_functions' in config:
            required_functions = '\n'.join(config['required_functions'])
            self._parse_required_functions(required_functions)

        self.compile_args = config.get('compile_args', [])
        self.disallowed_includes = config.get('disallowed_includes', [])
        self._translation_unit_req = StructuralDefinition(config)
        self.global_variables = config.get('global_variables', False)

        for file in config.get('functions', []):
            if 'function' not in file:
                raise IOError(error_msg_instructor_test_config(
                    ErrorCodes.STRUCTURAL_TEST_MISSING_FUNCTION_NAME))
            self._function_req[file['function']] = StructuralDefinition(file)

    def _parse_required_functions(self, required_functions: str) -> None:
        """
        Parses a string of required function prototypes and sets the required prototypes
        accordingly.

        :param required_functions: The required function prototypes
        :return: None
        """
        temporary_file = os.getcwd() + '/required_functions.c'
        translation_unit = parse_c(required_functions, temporary_file)
        fun = None
        for child in translation_unit.cursor.get_children():
            if str(child.location.file) != temporary_file:
                continue
            if child.kind != CursorKind.FUNCTION_DECL:
                continue
            for func_decl in child.walk_preorder():
                if func_decl.kind == CursorKind.FUNCTION_DECL:
                    fun = FunctionPrototype(func_decl.spelling, func_decl.result_type.spelling,
                                            func_decl.displayname)
                    self.required_function_prots.append(fun)
                elif func_decl.kind == CursorKind.PARM_DECL:
                    fun.parameter_types.append(func_decl.type.spelling)

    def structural_def_fun(self, function_name) -> StructuralDefinition:
        """
        Returns the structural definition for a given function name.

        :param function_name: name of the function
        :return: The expected structural definition
        """
        fun_req = self._function_req.get(function_name)
        if not fun_req:
            return self._translation_unit_req
        return StructuralRequirements._merge_def(self._translation_unit_req, fun_req)

    @staticmethod
    def _merge_def(global_def: StructuralDefinition, local_def: StructuralDefinition):
        """
        Returns the merged structural definition of the global and local scope

        :param global_def: Global structural definition
        :param local_def: Local (function) structural definition
        :return: The merged definition
        """
        merged = copy.deepcopy(local_def)
        merged.extend(global_def, local_def)
        return merged


class Function:
    """
    Representation of a C function containing the function prototype and function body.
    """
    prototype: FunctionPrototype
    body: FunctionBody
    _substitute: Optional[int]
    _inlined_body: FunctionBody

    def __init__(self, prototype: FunctionPrototype, body: FunctionBody) -> None:
        """

        :param prototype: function prototype
        :param body: function body
        """
        super().__init__()
        self.prototype = prototype
        self.body = body
        self._substitute = None
        self._inlined_body = self.body

    def __hash__(self):
        return hash(self.prototype.spelling)

    def __eq__(self, o: object) -> bool:
        if isinstance(o, Function):
            return self.prototype.spelling == o.prototype.spelling
        return NotImplemented

    def evaluate_function_structure(self, fun_def: StructuralDefinition,
                                    implemented_functions: Set[str],
                                    call_graph: Dict[str, Set[str]], contracted: bool) -> List[str]:
        """
        Evaluates if the function is conform to the structural definition

        :param fun_def: Local (function) structural definition
        :param implemented_functions: Function names implemented in the program
        :param call_graph: Call graph of the program
        :param contracted: True, if the function is part of a strongly connected component
        :return: List of errors
        """
        errors = []

        external_functions = self._inlined_body.function_calls.difference(implemented_functions)

        errors += self._check_variable_declarations(fun_def)

        errors += self._check_keywords(fun_def)

        errors += self._check_function_calls(fun_def, external_functions)

        if fun_def.recursion is None:
            return errors

        errors += self._check_recursion(fun_def, call_graph, contracted)

        return errors

    def _check_recursion(self, fun_def: StructuralDefinition, call_graph: Dict[str, Set[str]],
                         contracted: bool) -> List[str]:
        """
        Checks whether the recursion requirements are satisfied.

        :param fun_def: Local (function) structural definition
        :param call_graph: Call graph of the program
        :param contracted: True, if the function is part of a strongly connected component
        :return: List of errors
        """
        errors = []
        is_recursive = self._is_recursive(call_graph, contracted)

        if fun_def.recursion is True and is_recursive is False:
            msg = f"Error: You are supposed to use recursion in the function " \
                  f"'{self.prototype.spelling}'!"
            errors.append(msg)
        elif fun_def.recursion is False and is_recursive is True:
            msg = f"Error: You are not supposed to use recursion in the function " \
                  f"'{self.prototype.spelling}'!"
            errors.append(msg)
        return errors

    def _is_recursive(self, call_graph, contracted):
        """
        Returns whether the function is a recursive function.

        :param call_graph: Call graph of the program
        :param contracted: True, if the function is part of a strongly connected component
        :return: True, if recursive
        """
        return self._substitute is not None and (self.prototype.spelling in call_graph.get(
            self.prototype.spelling, set()) or contracted)

    def _check_keywords(self, fun_def: StructuralDefinition) -> List[str]:
        """
         Checks whether the keyword requirements are satisfied.

        :param fun_def: Local (function) structural definition
        :return: List of errors
        """
        errors = []
        expected_keywords = fun_def.expected_keywords.difference(self._inlined_body.keywords)
        for keyword in expected_keywords:
            msg = f"Error: You are supposed to use the keyword '{keyword}' in the function " \
                  f"'{self.prototype.spelling}'!"
            errors.append(msg)

        disallowed_keywords = fun_def.disallowed_keywords.intersection(
            self._inlined_body.keywords)
        for keyword in disallowed_keywords:
            msg = f"Error: You are not supposed to use the keyword '{keyword}' in the function " \
                  f"'{self.prototype.spelling}'!"
            errors.append(msg)
        return errors

    def _check_function_calls(self, fun_def: StructuralDefinition,
                              external_functions: Set[str]) -> List[str]:
        """
        Checks whether the function call requirements are satisfied.

        :param fun_def: Local (function) structural definition
        :param external_functions: Function names which are not implemented in the program
        :return: List of errors
        """
        errors = []
        expected_function_calls = fun_def.expected_function_calls.difference(
            external_functions)
        for function in expected_function_calls:
            msg = f"Error: You are supposed to call the function '{function}' in the function " \
                  f"'{self.prototype.spelling}'!"
            errors.append(msg)
        disallowed_functions = fun_def.disallowed_function_calls.intersection(
            external_functions)
        for functions in disallowed_functions:
            msg = f"Error: You are not supposed to call the function '{functions}' in the " \
                  f"function '{self.prototype.spelling}'!"
            errors.append(msg)
        return errors

    def _check_variable_declarations(self, fun_def: StructuralDefinition) -> List[str]:
        """
        Checks whether the variable declaration requirements are satisfied.

        :param fun_def: Local (function) structural definition
        :return: List of errors
        """
        errors = []
        expected_variable_declarations = fun_def.expected_variable_declarations.difference(
            self._inlined_body.variable_declarations)
        for var_decl in expected_variable_declarations:
            msg = f"Error: You are supposed to declare the variable '{var_decl.spelling}' with " \
                  f"type '{var_decl.variable_type}' in the function '{self.prototype.spelling}'!"
            errors.append(msg)
        return errors

    def inline_function(self, condensation, inlined_functions: Dict[int, FunctionBody]) -> None:
        """
        Sets the inlined function body if necessary.

        :param condensation: Condensed call graph
        :param inlined_functions: Inlined function bodies
        :return: None
        """
        if self.body.calls:
            self._substitute = condensation.graph['mapping'][self.prototype.spelling]
            self._inlined_body = inlined_functions[self._substitute]

    def is_contracted(self, mapping: Dict[int, str]):
        """
        Returns whether the function is part of a contracted node of the condensed call graph

        :param mapping: Mapping from the condensed call graph to the call graph
        :return: True, if  function is in a strongly connected component
        """
        return self._substitute in mapping and len(mapping[self._substitute]) > 1


class StructuralTest:
    """
    Analyzes the code structure of C code and checks whether it's structure satisfies the
    requirements defined in a yaml-file.
    """
    req: StructuralRequirements
    _tu: TranslationUnit
    _includes: Set[str]
    _functions: Set[Function]
    _global_variables: List[VariableDeclaration]

    def __init__(self, config: Dict[str, Any], translation_unit: str,
                 working_directory: str) -> None:
        """

        :param translation_unit: Filename of the translation_unit
        :param config: Dict containing the test configuration
        :param working_directory: The directory in which the translation unit resides
        """
        super().__init__()
        self._includes = set()
        self.req = StructuralRequirements(config, translation_unit, working_directory)
        self._functions = set()
        self._global_variables = []

    def run_test(self) -> List[str]:
        """
        Executes the structural test

        :return: List of errors
        """

        idx = Index.create()
        self._tu = idx.parse(self.req.main_file, args=self.req.compile_args,
                             options=TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)

        self._check_diagnostic()

        self.traverse_ast()

        errors = self._check_disallowed_includes()

        errors += self._check_function_structure()

        errors += self._check_required_functions()

        errors += self._check_global_variables()

        return errors

    @staticmethod
    def _severe_diagnostic_error(diag: Diagnostic):
        return diag.severity in (Diagnostic.Error, Diagnostic.Fatal)

    @staticmethod
    def _diagnostic_error_message(diag: Diagnostic):
        location = diag.location
        file = pathlib.Path(location.file.name).name
        return f'- {file}:{location.line}:{location.column}: {diag.spelling}'

    def _check_diagnostic(self):
        severe_errors = [diag for diag in self._tu.diagnostics if
                         self._severe_diagnostic_error(diag)]
        if len(severe_errors) > 0:
            error_msg = '\n'.join([self._diagnostic_error_message(diag) for diag in severe_errors])
            raise DiagnosticError(error_msg)

    def _check_function_structure(self) -> List[str]:
        """
        Checks whether the implemented functions adhere to the structural requirements.

        :return: List of errors
        """
        call_graph = {f.prototype.spelling: f.body.calls for f in self._functions}
        graph = nx.from_dict_of_lists(call_graph, create_using=nx.DiGraph)
        condensation = nx.condensation(graph)
        mapping = nx.get_node_attributes(condensation, 'members')
        function_dict = {f.prototype.spelling: f for f in self._functions}
        implemented_functions = set(function_dict.keys())
        errors = []
        inlined_functions = self.inline_functions(condensation, mapping, function_dict)
        for fun in self._functions:
            fun_body_req = self.req.structural_def_fun(fun.prototype.spelling)
            fun.inline_function(condensation, inlined_functions)
            contracted = fun.is_contracted(mapping)
            errors += fun.evaluate_function_structure(fun_body_req, implemented_functions,
                                                      call_graph, contracted)
        return errors

    def _check_disallowed_includes(self) -> List[str]:
        """
        Checks whether disallowed includes are used.

        :return: List of errors
        """
        errors = []
        for include in self._includes:
            if include in self.req.disallowed_includes:
                errors.append(f"Error: You are not supposed to include '{include}'!")
        return errors

    def _check_required_functions(self) -> List[str]:
        """
        Checks whether all required functions are implemented

        :return: List of errors
        """
        errors = []
        set_func_prots = {f.prototype for f in self._functions}
        for fun_name in self.req.required_function_prots:
            if fun_name not in set_func_prots:
                errors.append(f"Error: The required function '{fun_name}' is not implemented!")
        return errors

    def _check_global_variables(self) -> List[str]:
        """
        Checks whether the global variables requirements are satisfied.

        :return: List of errors
        """
        errors = []
        if self.req.global_variables is True and len(self._global_variables) == 0:
            msg = "Error: You are supposed to use global variables in this program!"
            errors.append(msg)
        elif self.req.global_variables is False and len(self._global_variables) > 0:
            global_variables = '\n'.join(
                [f"{v.location}: global variable '{v.spelling}'!" for v in self._global_variables])
            msg = f"Error: You are not supposed to use global variables in this program! " \
                  f"The following global variables were found:\n{global_variables}"
            errors.append(msg)
        return errors

    @staticmethod
    def inline_functions(condensation, mapping,
                         functions: Dict[str, Function]) -> Dict[Any, FunctionBody]:
        """
        Inlines all function bodies

        :param condensation: Condensed call graph
        :param mapping: Mapping from the condensed call graph to the call graph
        :param functions: Dict of all user-defined functions (function name -> function)
        :return: Inlined function bodies (function name -> function body)
        """
        function_inline = {}
        for vertex in condensation:
            tmp = FunctionBody()
            for fun in mapping[vertex]:
                if fun not in functions:
                    continue
                tmp += functions[fun].body
            for descendants in nx.descendants(condensation, vertex):
                for fun in mapping[descendants]:
                    if fun not in functions:
                        continue
                    tmp += functions[fun].body
            function_inline[vertex] = tmp
        return function_inline

    def traverse_ast(self) -> None:
        """
        Traverses the abstract syntax tree of the C program.

        :return: None
        """
        working_dir = str(pathlib.Path(self.req.main_file).parent)
        for child in self._tu.cursor.get_children():
            if child.location.file is None:
                continue
            if not child.location.file.name.startswith(working_dir):
                continue
            if child.kind == CursorKind.INCLUSION_DIRECTIVE:
                self._includes.add(child.spelling)
            elif child.kind == CursorKind.FUNCTION_DECL:
                fun = self.traverse_function_decl(child)
                if fun is None:
                    continue
                self._functions.add(fun)
            elif child.kind == CursorKind.VAR_DECL:
                variable_declaration = VariableDeclaration.create_from_parser(child)
                self._global_variables.append(variable_declaration)

    def traverse_function_decl(self, fun_decl) -> Optional[Function]:
        """
        Traverses a function declaration

        :param fun_decl: cursor to the function declaration
        :return: The function representation
        """
        prototype = FunctionPrototype(fun_decl.spelling, fun_decl.result_type.spelling,
                                      fun_decl.displayname)
        body = FunctionBody()
        is_prototype = 1
        for statement in fun_decl.walk_preorder():
            if statement.kind == CursorKind.PARM_DECL:
                prototype.parameter_types.append(statement.type.spelling)
            elif statement.kind == CursorKind.COMPOUND_STMT:
                is_prototype = 0
            elif statement.kind == CursorKind.CALL_EXPR:
                body.function_calls.add(statement.spelling)
                body.calls.add(statement.spelling)
            elif statement.kind == CursorKind.WHILE_STMT:
                body.keywords.add('while')
            elif statement.kind == CursorKind.DO_STMT:
                body.keywords.add('do')
            elif statement.kind == CursorKind.VAR_DECL:
                body.variable_declarations.add(
                    FunctionParameter.create_from_parser(statement))
        # elif ch.kind == CursorKind.BINARY_OPERATOR or ch.kind == CursorKind.UNARY_OPERATOR or
        # ch.kind == CursorKind.CONDITIONAL_OPERATOR: self._operators.add(ch.spelling) # see:
        # https://reviews.llvm.org/D10833?id=39158#change-vBa6Es1Tcb5q
        if is_prototype:
            return None
        body.keywords = body.keywords.union(self.extract_keywords(fun_decl))
        return Function(prototype, body)

    def extract_keywords(self, fun_decl) -> Set[str]:
        """
        Extracts all keywords except for while and do in a function

        :param fun_decl: cursor to the function declaration
        :return: Set of keywords
        """
        keywords = set()
        for token in self._tu.get_tokens(extent=fun_decl.extent):
            # pylint: disable=no-member
            if token.kind == TokenKind.KEYWORD:
                # pylint: enable=no-member
                if token.spelling in ['while', 'do']:
                    continue
                keywords.add(token.spelling)
        return keywords
