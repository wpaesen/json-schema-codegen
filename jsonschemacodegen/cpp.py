import json
import abc
import stringcase
import os

from jacobsjinjatoo import templator
from . import schemawrappers

class ResolverBaseClass(abc.ABC):

    def cpp_resolve_namespace(self, ns: list, append='::') -> str:
        """ Figures out what a namespace should be for C++.
        @param ns is the full namespace of the object that should be resolved.  For example, if the object is:
            d::e::f::Goo, then the ns param should be ['d', 'e', 'f'] with 'Goo' being the object name.
        @param append is a string that will be appended to the result only if there is some part of a namespace left.
        @returns a string that should go before the object.  Using the described parameters, the result
            would be "f::"
        """
        assert(isinstance(ns, list))
        usings = self.cpp_get_usings()
        for n in ns:
            assert(isinstance(n, str)), "namespace is %s" % (ns)
        ret = ns
        nsString = "||".join(ns)
        for using in usings:
            if nsString.startswith("||".join(using)):
                abbr = ns[len(using):]
                if len(abbr) < len(ret):
                    ret = abbr
        if len(ret) > 0:
            return "::".join(ret)+append
        else:
            return ""

    def append_to_namespace(self, ns: list, appendage) -> list:
        assert(isinstance(ns, list)), "Namespace isn't list it is %s" % (ns)
        newNs = ns.copy()
        newNs.append(appendage)
        return newNs

    @abc.abstractmethod
    def cpp_get_filename_base(self, reference):
        pass

    @abc.abstractmethod
    def cpp_get_header(self, reference: str) -> str:
        """ Given a $ref reference from a schema, return the name/path of the header file that should be included for the declaration
        of the represented object.
        """
        pass

    @abc.abstractmethod
    def cpp_get_namespace(self, reference: str) -> list:
        """Given a reference and the current usings statements, return the namespace of the object pointed to by the reference.
        If the namespace is not empty, also append the append string
        """
        pass

    @abc.abstractmethod
    def cpp_get_name(self, reference: str) -> str:
        """Given a reference, return the C++ object name pointed to by the reference.
        """
        pass

    def cpp_get_ns_name(self, reference: str) -> str:
        ns = self.cpp_get_namespace(reference)
        name = self.cpp_get_name(reference)
        if ns is None:
            return 
        return "{}::{}".format("::".join(ns), name)

    @abc.abstractmethod
    def cpp_get_root_namespace(self) -> list:
        pass

    @abc.abstractmethod
    def cpp_get_usings(self) -> list:
        pass

    @abc.abstractmethod
    def cpp_get_lib_ns(self) -> list:
        pass

class CodeTemplator2(templator.MarkdownTemplator):

    """Since most code can use markdown in documentation blocks, we inherit from MarkdownTemplator.
    """

    def __init__(self, output_dir):
        super().__init__(output_dir)
        self.add_filter('enumify', self._enumify)
        self.add_filter('privatize', self._privatize)
        self.add_filter('doxygenify', self._doxygenify)
        self.enum_overrules = {}

    def set_enum_overrule(self, value, name):
        self.enum_overrules[value] = name

    def _enumify(self, s: str):
        if s in self.enum_overrules:
            return self.enum_overrules[s]

        if type(s) == int:
            s = 'Val_'+str(s)
        elif s[0].isnumeric():
            s = '_'+s

        return stringcase.constcase(s)
    
    @classmethod
    def _privatize(cls, s: str):
        return cls._add_leading_underscore(stringcase.camelcase(s))

    @staticmethod
    def _doxygenify(s: str):
        """ Translates a markdown string for use as a doxygen description.
        """
        if "```plantuml" in s:
            plantuml_replace = re.compile(r"```plantuml\n(.*)```", re.DOTALL|re.MULTILINE)
            return plantuml_replace.sub(r"\\startuml\n\1\\enduml", s)
        return s



class GeneratorFromSchema(object):

    def __init__(self, src_output_dir, header_output_dir, resolver):
        self.output_dir = {
            "src": src_output_dir,
            "header": header_output_dir,
        }
        assert(isinstance(resolver, ResolverBaseClass)), "Resolver is %s" % (resolver)
        self.resolver = resolver
        self.enum_overrules = {}

    def _make_sure_directory_exists(self, output_key, dir_path):
        d = os.path.join(self.output_dir[output_key], dir_path)
        if not os.path.exists(d):
            os.makedirs(d)

    def set_enum_overrule(self, value, name):
        self.enum_overrules[value] = name

    def set_enum_overrules(self, enum_dict):
        for value, name in enum_dict.items():
            self.set_enum_overrule(value, name)

    def GetDeps(self, schema):
        return schema.CppIncludes(self.resolver)


    def Generate(self, schema, path):
        retval = [None, None]
        srcGenerator = CodeTemplator2(self.output_dir['src']).add_template_package('jsonschemacodegen.templates.cpp')
        headerGenerator = CodeTemplator2(self.output_dir['header']).add_template_package('jsonschemacodegen.templates.cpp')

        for value, name in self.enum_overrules.items():
            srcGenerator.set_enum_overrule(value, name)
            headerGenerator.set_enum_overrule(value, name)

        args = {
            "Name": self.resolver.cpp_get_name(path),
            "schema": schemawrappers.SchemaFactory(schema),
        }
        headerFilename = self.resolver.cpp_get_header(path)
        self._make_sure_directory_exists('header', os.path.dirname(headerFilename))
        if '$ref' not in schema:
            srcFileName = "{}.cpp".format(self.resolver.cpp_get_filename_base(path))
            self._make_sure_directory_exists('src', os.path.dirname(srcFileName))
            srcGenerator.render_template(template_name="source.cpp.jinja2", 
                output_name=srcFileName, 
                deps=['"{}"'.format(self.resolver.cpp_get_header(path))], 
                usings=self.resolver.cpp_get_usings(),
                ns=self.resolver.cpp_get_namespace(path),
                resolver=self.resolver,
                **args)
            retval[0] = srcFileName
        headerGenerator.render_template(template_name="header.hpp.jinja2", 
            output_name=headerFilename, 
            ns=self.resolver.cpp_get_namespace(path),
            deps=self.GetDeps(args['schema']), 
            resolver=self.resolver,
            filename=headerFilename,
            filepath='',
            **args)
        retval[1] = headerFilename
        return tuple(retval)


class LibraryGenerator(object):

    def __init__(self, src_output_dir: str, header_output_dir: str, resolver):
        self.output_dir = {
            "src": src_output_dir,
            "header": header_output_dir,
        }
        self.resolver = resolver
    
    def _make_sure_directory_exists(self, output_key, dir_path):
        d = os.path.join(self.output_dir[output_key], dir_path)
        if not os.path.exists(d):
            os.makedirs(d)

    def Generate(self):
        retval = [None, "exceptions.hpp"]
        headerGenerator = templator.CodeTemplator(self.output_dir['header']).add_template_package('jsonschemacodegen.templates.cpp')
        headerGenerator.render_template(template_name="exceptions.hpp.jinja2", 
            output_name="exceptions.hpp", 
            ns=self.resolver.cpp_get_lib_ns(), 
        )
        return tuple(retval)
