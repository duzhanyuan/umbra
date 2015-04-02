#!/usr/bin/python


"""Parses Umbra configuration"""


import json
import os
import re
import struct
import sys
from copy import deepcopy


class ConfigValidationException(Exception):
    """Configuration is not well-formed"""
    pass


def is_string(obj):
    """Returns true if object is a string-like object"""
    return isinstance(obj, unicode) or isinstance(obj, str)


def is_page(obj):
    """Returns true if string-like object is a valid web path"""
    return is_string(obj) and len(obj) > 0 and obj[0] == '/'


def is_list_of(list_, type_func, min_len=0):
    """
    Returns boolean indicating if list contains only elements of the type
    checked by type_func.
    """
    if type(list_) is not list:
        return False
    return (reduce(lambda a, b: a and type_func(b), list_, True)
            and min_len <= len(list_))


def assert_parse(value, msg):
    """Assert value is true while parsing"""
    if not value:
        raise ConfigValidationException(msg)


def c_str_repr(str_):
    """Returns representation of string in C (without quotes)"""
    def byte_to_repr(char_):
        """Converts byte to C code string representation"""
        x = ord(char_)
        if char_ in ['"', '\\', '\r', '\n']:
            return '\\' + chr(x)
        elif ord(' ') <= x <= ord('^') or x == ord('_') or ord('a') <= x <= ord('~'):
            return chr(x)
        else:
            return '\\x%02x' % x

    return '"%s"' % ''.join((byte_to_repr(x) for x in str_))


def dict_updated(d, e):
    """Returns copy of dict d with updates in e"""
    ret = d.copy()
    ret.update(e)
    return ret


class MacroDef:
    """Represents C macro definition"""

    def __init__(self, name, value):
        self.name = name
        self.value = value

    def to_string(self):
        return '#define %s %s\n' % (self.name, str(self.value))


class StructDef:
    """Represents C structure"""

    def __init__(self, name, elements):
        """
        Takes structure name and elements list of pairs

        Argument each element of elements is of the form (type, name)
        """
        self.name = name
        self.elements = elements

    def to_string(self):
        expand_elements = Option.expand_elements(self.elements)
        l = ['    %s %s;' % x for x in expand_elements]
        l.sort()
        parts = (['struct %s {' % self.name] +
                 l +
                 ['};'])
        return '\n'.join(parts) + '\n'

    def get_prototype(self):
        return 'struct %s;' % self.name


class VarInst:
    """Represents instance of variable"""

    instCount = 0

    def __init__(self, typestr, name, value):
        self.typestr = typestr
        self.name = name
        self.value = value

    def to_string(self):
        return '%s %s = %s;\n' % (self.typestr, self.name, self.value)

    @staticmethod
    def get_next_inst_name():
        VarInst.instCount += 1
        return 'inst_%03d' % VarInst.instCount


class StringArrInst(VarInst):
    """Represents an instance of a C array of strings (char **)"""
    def __init__(self, name, value):
        VarInst.__init__(self, '', name, value)

    def to_string(self):
        s = '{%s}' % ', '.join([c_str_repr(x) for x in self.value])
        return 'const char *%s[%d] = %s;\n' % (self.name, len(self.value), s)

    def to_proto_string(self):
        return 'const char *%s[%d];\n' % (self.name, len(self.value))


class StructArrInst(VarInst):
    """Represents an instance of a C array of structs (char **)"""
    def __init__(self, value, struct_name):
        for struct in value:
            assert_parse(isinstance(struct, StructInst), "Takes iterable of StructInsts")
        VarInst.__init__(self, '', VarInst.get_next_inst_name(), value)
        self.struct_name = struct_name

    def to_string(self):
        raise Exception('Call to_string_declaration() or to_string_initialize()')

    def to_string_declaration(self):
        return 'struct %s %s[%d];\n' % (self.struct_name, self.name, len(self.value))

    def to_string_initialize(self, indent=4):
        if len(self.value) == 0:
            return ''
        lines = []
        for i in xrange(len(self.value)):
            s = '%s[%d] = %s;' % (self.name, i, self.value[i].name)
            lines.append(indent * ' ' + s)
        return '\n'.join(lines)


class StructInst(VarInst):
    """Represents instance of a C struct"""


    def __init__(self, structDef, struct_name, inst_name=None):
        if inst_name == None:
            inst_name = VarInst.get_next_inst_name()
        VarInst.__init__(self, '', inst_name, None)
        structDef.set_instance_name(self.name)
        self.option = structDef
        self.struct_name = struct_name

    def to_string(self):
        s = ['struct %s %s = {' % (self.get_struct_name(), self.name)]
        allOpts = list(self.option.getAllOptions())
        allOpts.sort(key=lambda x: x.name)
        for opt in allOpts:
            for (typestr, name, value) in opt.get_elements_value():
                s.append('    .%s = %s,' % (name, value))
        s.append('};')
        return '\n'.join(s) + '\n'

    def get_struct_name(self):
        return self.struct_name


headerHeader = """/* Autogenerated header, do not modify */

#ifndef UMBRA_DYN_CONFIG_HEADER
#define UMBRA_DYN_CONFIG_HEADER

#include <stdbool.h>

void init_config_vars();
\n\n"""

headerFooter = "\n#endif\n"

derived_macro_defs = """/* Derived macro definitions*/
#define PAGES_CONF_LEN (sizeof(pages_conf) / sizeof(*pages_conf))
#define ENABLE_PARAM_CHECKS (ENABLE_PARAM_LEN_CHECK || ENABLE_PARAM_WHITELIST_CHECK || ENABLE_CSRF_PROTECTION)
#define ENABLE_SESSION_TRACKING (ENABLE_CSRF_PROTECTION)
"""

init_func_format = """void init_config_vars() {
%s
}
"""

bodyHeader = """/* Autogenerated C file, do not modify */

#include "%s"
#include "http_util.h"
\n\n"""

class CodeHeader:
    """Holds information of code being generated"""
    def __init__(self):
        self.macro_defs = []
        self.struct_defs = []
        self.params_structs = []
        self.page_conf_structs = []
        self.var_defs = []
        self.params_arrays = []
        self.page_conf_arrays = []

    def writeConfigHeader(self, output_header, output_body, f):
        f.write(headerHeader)

        f.write('/* Macro definitions */\n')
        for md in self.macro_defs:
            f.write(md.to_string() + '\n')

        f.write('/* Struct prototypes */\n\n')
        for sd in self.struct_defs:
            f.write(sd.get_prototype() + '\n')
        f.write('\n')

        f.write('/* Struct definitions */\n\n')
        for sd in self.struct_defs:
            f.write(sd.to_string() + '\n')

        f.write('#define WHITELIST_PARAM_LEN %d\n' % WhitelistOption.num_bytes)

        f.write('\n')

        f.write('/* Global variables */\n\n')
        for sd in self.page_conf_arrays:
            f.write("extern ")
            f.write(sd.to_string_declaration() + '\n')


        f.write('extern struct page_conf default_page_conf;\n')
        for vd in self.var_defs:
            f.write('extern ')
            f.write(vd.to_proto_string() + '\n')

        f.write(derived_macro_defs + '\n')

        f.write(headerFooter)

    def writeConfigBody(self, output_header, output_body, f):
        f.write(bodyHeader % output_header)

        f.write('/* Variable definitions */\n\n')
        for vd in self.var_defs:
            f.write(vd.to_string() + '\n')

        f.write('/* Struct instances */\n\n')

        f.write('/* Params instances */\n\n')
        for sd in self.params_structs:
            f.write(sd.to_string() + '\n')

        f.write('/* Param arrays */\n\n')
        for sd in self.params_arrays:
            f.write(sd.to_string_declaration() + '\n')

        f.write('/* Page_conf instances */\n\n')
        for sd in self.page_conf_structs:
            f.write(sd.to_string() + '\n')

        f.write('/* Page_conf array */\n\n')
        for sd in self.page_conf_arrays:
            f.write(sd.to_string_declaration() + '\n')

        f.write('/* Initializer function */\n')
        f.write('void init_config_vars() {\n')
        for sd in self.params_arrays:
            s = sd.to_string_initialize()
            if s:
                f.write(s + '\n')
        f.write('\n')
        for sd in self.page_conf_arrays:
            s = sd.to_string_initialize()
            if s:
                f.write(s + '\n')
        f.write('}\n')

    def add_macro_def(self, name, value):
        self.macro_defs.append(MacroDef(name, value))

    def add_struct_def(self, struct_def):
        if struct_def.name in [x.name for x in self.struct_defs]:
            return
        self.struct_defs.append(struct_def)

    def add_page_conf_struct(self, inst):
        self.page_conf_structs.append(inst)

    def add_params_struct(self, inst):
        self.params_structs.append(inst)

    def add_params_array(self, arr):
        if not isinstance(arr, StructArrInst):
            raise Exception("Must be type StructArrInst")
        self.params_arrays.append(arr)

    def add_page_conf_array(self, arr):
        if not isinstance(arr, StructArrInst):
            raise Exception("Must be type StructArrInst")
        self.page_conf_arrays.append(arr)

    def add_var_def(self, var):
        self.var_defs.append(var)


class Option:
    """Represents simple configuration option"""
    def __init__(self, name, isTopLevel=False, defaultValue=None):
        self.name = name
        self.value = defaultValue
        self.valueHasBeenSet = False
        self.isTopLevel = isTopLevel

    @staticmethod
    def expand_elements(elements):
        ret = []
        for e in elements:
            ret += e.get_elements()
        ret.sort()
        return ret

    @staticmethod
    def sort_struct_element_list(l):
        l.sort(key=lambda x: (x.getCType(), x.name))

    def validate(self):
        raise Exception('Validate not implemented')

    def setValue(self, value):
        self.value = value
        self.valueHasBeenSet = True

    def addConfig(self, info):
        raise NotImplementedError()

    def getCType(self):
        raise NotImplementedError()

    def getCValue(self):
        raise NotImplementedError()

    def getStructMemberValue(self):
        return self.getCValue()

    def getDesc(self):
        s = '%s %s:' % (self.__class__.__name__,
                        self.name)
        if hasattr(self, 'value'):
            s += '\nvaluetype=%s,\n value=%s' % (self.value.__class__.__name__,
                                                 repr(self.value))
        return s

    def assrt(self, value, msg):
        assert_parse(value, '<' + self.getDesc() + '>:\n' + msg)

    def get_elements(self):
        return [(self.getCType(), self.name)]

    def get_elements_value(self):
        return [(self.getCType(), self.name, self.getStructMemberValue())]


class BoolOption(Option):
    """Represents boolean config option"""

    def validate(self):
        self.assrt(isinstance(self.value, bool), 'Invalid Boolean value "%s"' %
                   repr(self.value))

    def addConfig(self, info):
        if not self.valueHasBeenSet:
            return
        if self.isTopLevel:
            info.add_macro_def(self.name.upper(), self.getCValue())

    def getCValue(self):
        return 'true' if self.value else 'false'

    def getCType(self):
        return 'int'


class PosIntOption(Option):
    """Represents positive integer config option"""

    def validate(self):
        if isinstance(self.value, float):
            self.value = long(self.value)
        self.assrt(isinstance(self.value, int) or isinstance(self.value, long),
                   'Must be integer or long')
        self.assrt(self.value > 0, 'Must be greater than 0')

    def addConfig(self, info):
        if self.isTopLevel:
            info.add_macro_def(self.name.upper(), self.getCValue())

    def getCValue(self):
        return str(self.value)

    def getCType(self):
        return 'int'


class StringOption(Option):
    """Represents string config option"""

    def validate(self):
        self.assrt(is_string(self.value), 'Value "%s" is not string' %
                   repr(self.value))

    def addConfig(self, info):
        if self.isTopLevel:
            info.add_macro_def(self.name.upper(), self.getCValue())

    def getCType(self):
        return 'const char *'

    def getCValue(self):
        return c_str_repr(self.value)


class WhitelistOption(StringOption):
    """Represents option where certain characters are whitelisted"""

    num_bytes = 0x100 / 8

    def getCType(self):
        return 'const char *'

    def getCValue(self):
        chars = []
        byte = 0
        for i in range(0x100):
            byte_idx = i % 8
            c = chr(i)
            if re.match(self.value, c):
                byte |= (1 << byte_idx)
            if byte_idx == 7:
                chars.append(byte)
                byte = 0

        return c_str_repr(struct.pack(WhitelistOption.num_bytes * 'B', *chars))


class StringArrOption(Option):
    """Represents array of strings config option"""

    def __init__(self, name, minLen=0, allowedVals=None,
                 isElementValid=None, isTopLevel=False, defaultValue=[]):
        Option.__init__(self, name, defaultValue=defaultValue)
        self.value = defaultValue
        self.allowedVals = allowedVals
        self.minLen = minLen
        self.isElementValid = isElementValid
        self.isTopLevel = isTopLevel

    def validate(self):
        self.assrt(is_list_of(self.value, is_string, self.minLen),
                   'Must be list')
        if self.allowedVals is not None:
            self.assrt(set(self.value).issubset(self.allowedVals),
                       'Elements must be in allowed set: %s' %
                       repr(self.allowedVals))
        if self.isElementValid is not None:
            for x in self.value:
                self.assrt(self.isElementValid(x), 'Invalid element "%s"' %
                           repr(x))

    def addConfig(self, info):
        if self.isTopLevel:
            info.add_var_def(StringArrInst(self.name, self.value))

    def getCType(self):
        return 'const char **'

    def getCValue(self):
        return '{%s}' % ', '.join([c_str_repr(x) for x in self.value])


class HTTPReqsOption(StringArrOption):
    def getStructMemberValue(self):
        return ' | '.join(['HTTP_REQ_' + x for x in self.value])

    def getCType(self):
        return 'int'


class MultiOption(Option):
    """Represents option that contains child options"""

    def __init__(self, name, requiredConf, optionalConf, isTopLevel=False, nameVisitOrder=None):
        self.name = name
        self.value = None
        self.jsonInput = None
        self.valueHasBeenSet = False
        self.requiredConf = deepcopy(requiredConf)
        self.optionalConf = deepcopy(optionalConf)
        self.requiredName2Conf = {x.name:x for x in self.requiredConf}
        self.optionalName2Conf = {x.name:x for x in self.optionalConf}
        for x in self.requiredConf.union(self.optionalConf):
            self.assrt(isinstance(x, Option), "Must take Options")
        self._instance_name = None
        self.isTopLevel = isTopLevel

        if nameVisitOrder != None:
            self.assrt(isinstance(nameVisitOrder, list), "nameVisitOrder must be a list")
            optNames = set((x.name for x in self.getAllOptions()))
            fmt_args = sorted(nameVisitOrder), sorted(optNames)
            self.assrt(set(nameVisitOrder) == optNames,
                       "Elements nameVisitOrder do not match names of options\n" +
                       ("nameVisitOrder=%s, optionNames=%s" % fmt_args))
        self.nameVisitOrder = nameVisitOrder

    def getAllOptionsSorted(self):
        if self.nameVisitOrder != None:
            n2c = self._getName2Conf()
            for x in self.nameVisitOrder:
                yield n2c[x]
        else:
            for x in self.getAllOptions():
                yield x

    def getRequiredOptionsSorted(self):
        for x in self.getAllOptionsSorted():
            if x in self.requiredConf:
                yield x

    def getOptionalOptionsSorted(self):
        for x in self.getAllOptionsSorted():
            if x in self.optionalConf:
                yield x

    def valueSorted(self, value):
        for x in self.getAllOptionsSorted():
            if x.name in value.keys():
                yield (x.name, value[x.name])

    def validate(self):
        for x in self.getRequiredOptionsSorted():
            self.assrt(x.valueHasBeenSet, 'Option %s has not been specified' %
                       x.name)
            x.validate()
        for x in self.getOptionalOptionsSorted():
            if x.valueHasBeenSet:
                x.validate()

    def setValue(self, value):
        self.value = value
        self.valueHasBeenSet = True

        # Create mapping between option name and option object
        name2conf = self._getName2Conf()

        # Set option values
        for optname, optval in self.valueSorted(value):
            self.assrt(optname in name2conf, 'Unknown option "%s"' % optname)
            name2conf[optname].setValue(optval)

    def addConfig(self, info):
        for option in self.getAllOptions():
            option.addConfig(info)

    def _getName2Conf(self):
        name2conf = self.requiredName2Conf.copy()
        name2conf.update(self.optionalName2Conf)
        return name2conf

    def getAllOptions(self):
        return self.requiredConf.union(self.optionalConf)

    def getCType(self):
        return 'void *'

    def get_instance_name(self):
        if self._instance_name == None:
            raise Exception('Instance name has not been set')
        return self._instance_name

    def set_instance_name(self, name):
        self._instance_name = name

    def getStructMemberValue(self):
        return self.get_instance_name()


class DefaultPageConfOption(MultiOption):
    def __init__(self, name, requiredConf, optionalConf, param_option, isTopLevel=False):
        MultiOption.__init__(self, name, requiredConf, optionalConf, isTopLevel)
        self.param_option = param_option

    def addConfig(self, info):
        # Add structure definition

        default_page_conf_name = 'default_page_conf'
        # Add name
        nameOpt = StringOption('name')
        nameOpt.setValue(default_page_conf_name)
        self.requiredConf.add(nameOpt)

        # Set params
        self.param_option.set_instance_name('NULL')
        self.requiredConf.add(self.param_option)

        opts = list(self.getAllOptions())
        Option.sort_struct_element_list(opts)
        page_conf_struct = StructDef('page_conf', opts)
        info.add_struct_def(page_conf_struct)

        # Call children
        for option in self.getAllOptions():
            option.addConfig(info)

        # Add default structure instance
        inst = StructInst(self, 'page_conf', inst_name=default_page_conf_name)
        info.add_page_conf_struct(inst)


class NamedOptionSet(MultiOption):
    """
    Represents option that has child options such that each child has a unique
    name that maps to the set of child options.
    """

    def __init__(self, name, requiredConf, optionalConf, defaultConf=None, isTopLevel=False):
        MultiOption.__init__(self, name, requiredConf, optionalConf)
        self.suboptions = {}
        self.orig_form = deepcopy(self)
        self.isTopLevel = isTopLevel
        self.defaultConf = defaultConf

    def _updateWithDefaults(self, page_conf):
        default_opt_name2val = self.defaultConf._getName2Conf()
        for opt in page_conf.getAllOptions():
            if isinstance(opt, NamedOptionSet):  # params
                for p in opt.suboptions.values():
                    self._updateWithDefaults(p)
            else:
                if opt.valueHasBeenSet:
                    continue
                else:
                    opt.setValue(default_opt_name2val[opt.name].value)

    def setValue(self, value):
        self.value = value
        self.valueHasBeenSet = True
        for path, conf in value.items():
            page_conf = MultiOption(self.name + '$' + path, self.requiredConf,
                                    self.optionalConf)
            page_conf.setValue(conf.copy())
            self.suboptions[path] = page_conf
            if self.defaultConf != None:
                self._updateWithDefaults(page_conf)

    def getOrigForm(self):
        return deepcopy(self.orig_form)

    def get_elements(self):
        return [(self.getCType() + ' *', self.name),
                ('unsigned int', self.name + '_len')]

    def get_elements_value(self):
        return [(self.getCType() + ' *', self.name, self.getStructMemberValue()),
                ('unsigned int', self.name + '_len', len(self.suboptions))]


class PageConfOption(NamedOptionSet):
    def validate(self):
        for path, page_conf in self.suboptions.items():
            self.assrt(is_page(path),
                       'Path "%s" is not valid, must start with a "/"' % path)
            page_conf.validate()

    def addConfig(self, info):
        # Add structure definition
        nameOpt = StringOption('name')
        self.requiredConf.add(nameOpt)
        opts = list(self.getAllOptions())
        Option.sort_struct_element_list(opts)
        page_conf_struct = StructDef('page_conf', opts)
        info.add_struct_def(page_conf_struct)

        # Call children
        for option in self.getAllOptions():
            option.addConfig(info)

        # Add structure instances
        struct_insts = []
        for (page, options) in self.suboptions.items():
            for opt in options.getAllOptions():
                opt.addConfig(info)
            nameOptCopy = deepcopy(nameOpt)
            nameOptCopy.setValue(page)
            options.requiredConf.add(nameOptCopy)
            inst = StructInst(options, 'page_conf')
            struct_insts.append(inst)
            info.add_page_conf_struct(inst)

        page_conf_arr = StructArrInst(struct_insts, 'page_conf')
        page_conf_arr.name = 'pages_conf'
        info.add_page_conf_array(page_conf_arr)

    def getCType(self):
        return 'struct page_conf'


class ParamsOption(NamedOptionSet):
    def validate(self):
        for param, param_conf in self.suboptions.items():
            self.assrt(is_string(param),
                       'Param "%s" is not valid, must be string' % param)
            self.assrt(not '%' in param, ('Param "%s" is not valid, must not ' % param) +
                       'contain any percent ("%") signs. Do not URL encode the parameters.')
            param_conf.validate()

    def addConfig(self, info):
        # Add structure definition
        nameOpt = StringOption('name')
        self.requiredConf.add(nameOpt)
        opts = list(self.getAllOptions())
        Option.sort_struct_element_list(opts)
        params_struct = StructDef('params', opts)
        info.add_struct_def(params_struct)

        # Call children
        for option in self.getAllOptions():
            option.addConfig(info)

        # Add structure instances
        struct_insts = []
        for (param, options) in self.suboptions.items():
            nameOptCopy = deepcopy(nameOpt)
            nameOptCopy.setValue(param)
            options.requiredConf.add(nameOptCopy)
            inst = StructInst(options, 'params')
            struct_insts.append(inst)
            info.add_params_struct(inst)

        params_arr = StructArrInst(struct_insts, 'params')
        info.add_params_array(params_arr)
        self.set_instance_name(params_arr.name)

    def getCType(self):
        return 'struct params'


# Configuration specification
param_conf_required = set()
param_conf_optional = {
    PosIntOption('max_param_len'),
    WhitelistOption('whitelist')
}

params_option = ParamsOption('params', param_conf_required, param_conf_optional)
page_conf_required = set()
allowedHTTPReq = ['GET', 'POST', 'HEAD', 'PUT', 'DELETE',
                  'CONNECT', 'TRACE', 'OPTIONS']
page_conf_optional = {
    params_option,
    BoolOption('restrict_params'),
    HTTPReqsOption('request_types', minLen=1,
                   allowedVals=allowedHTTPReq),
    BoolOption('requires_login'),
    BoolOption('has_csrf_form'),
    BoolOption('receives_csrf_form_action')
}.union(deepcopy(param_conf_optional))

default_page_conf_required = {deepcopy(x) for x in page_conf_required.union(page_conf_optional)
                              if x.name not in ['params']}
default_page_conf_optional = set()

# Update ENABLE_PARAM_CHECKS to depend on parameter options
enable_options = {
    BoolOption('enable_header_field_len_check', isTopLevel=True),
    BoolOption('enable_header_value_len_check', isTopLevel=True),
    BoolOption('enable_request_type_check', isTopLevel=True),
    BoolOption('enable_param_len_check', isTopLevel=True),
    BoolOption('enable_param_whitelist_check', isTopLevel=True),
    BoolOption('enable_url_directory_traversal_check', isTopLevel=True),
    BoolOption('enable_csrf_protection', isTopLevel=True),
    BoolOption('enable_https', isTopLevel=True)
}

global_conf_required = {
    PosIntOption('max_header_field_len', isTopLevel=True),
    PosIntOption('max_header_value_len', isTopLevel=True)
}.union(enable_options)

global_conf_optional = {
    StringOption('https_certificate', isTopLevel=True, defaultValue=""),
    StringOption('https_private_key', isTopLevel=True, defaultValue=""),
    #StringArrOption('successful_login_pages', minLen=1, isTopLevel=True, defaultValue=[]),
    PosIntOption('max_num_sessions', isTopLevel=True, defaultValue=20),
    PosIntOption('session_life_seconds', isTopLevel=True, defaultValue=300)
}

default_page_conf = DefaultPageConfOption(
    'default_page_config',
    default_page_conf_required,
    default_page_conf_optional,
    params_option)

toplevel_conf = MultiOption('toplevel', {
    MultiOption('global_config', global_conf_required,
                global_conf_optional),
    default_page_conf,
    PageConfOption('page_config', page_conf_required, page_conf_optional,
                   defaultConf=default_page_conf)
    }, set(), nameVisitOrder=['default_page_config', 'global_config', 'page_config'])

def comments_removed_read(f):
    ret_lines = []
    for l in f.readlines():
        if not re.match(r"\s*#", l):
            ret_lines.append(l)
    return ''.join(ret_lines)

def parse_config(config_file):
    print 'Parsing config file "%s"' % config_file
    with open(config_file, 'r') as f:
        conf_str = comments_removed_read(f)
        conf = json.loads(conf_str)
        toplevel_conf.setValue(conf)
    toplevel_conf.validate()
    return toplevel_conf


def write_header(toplevel_conf, output_header, output_body):
    info = CodeHeader()
    toplevel_conf.addConfig(info)
    with open(output_header, 'w') as f:
        info.writeConfigHeader(output_header, output_body, f)
    with open(output_body, 'w') as f:
        info.writeConfigBody(output_header, output_body, f)


def main():
    if len(sys.argv) != 4:
        print 'Usage: %s CONFIG OUTPUT_HEADER OUPUT_C_FILE' % sys.argv[0]
        sys.exit(1)
    config_file, output_header, output_body = tuple(sys.argv[1:])
    try:
        toplevel_conf = parse_config(config_file)
        write_header(toplevel_conf, output_header, output_body)
    except:
        if os.path.exists(output_header):
            os.remove(output_header)
        raise
    print '[done]'


if __name__ == '__main__':
    main()
