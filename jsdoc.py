#!/usr/bin/env python

"""
Python library & command-line tool for performing a variety of build
& deployment tasks of jQuery plugins.  

This offers the following features:

* List all dependencies of a plugin or plugins
* Check for method name conflicts among a set of plugins
* Extract metadata from doc comments
* Generate documentation for a set of files.

It depends on the existence of certain @tags in the documentation.  These are:

* @module: Display name of the module
* @author: Author's name
* @version: Version number
* @organization: Name of sponsoring organization, if any
* @license: License type (BSD/MIT/GPL/LGPL/Artistic/etc.)
* @dependency: Filename of parent plugin.  Multiple tags allowed
"""

import os
import re
import sys
import getopt
import cgi

try:
    import cjson
    encode_json = lambda val: cjson.encode(val)
except ImportError:
    try:
        import simplejson
        encode_json = lambda val: simplejson.dumps(val)
    except ImportError:
        def encode_json(val):
            raise ImportError(
                    "Either cjson or simplejson is required for JSON encoding")

def first_sentence(str):
    """
    Returns the first sentence of a string - everything up to the period,
    or the whole text if there is no period.
    """
    index = str.find('.')
    return index != -1 and str[0:index] or str

##### INPUT/OUTPUT #####

def warn(format, *args):
    sys.stderr.write(format % args + '\n')

def flatten(iter_of_iters):
    retval = []
    for val in iter_of_iters:
        retval.extend(val)
    return retval

def is_js_file(filename):
    """
    Returns true if the filename ends in .js and is not a packed or
    minified file (no '.pack' or '.min' in the filename)

    >>> is_js_file('jquery.min.js')
    False
    >>> is_js_file('foo.json')
    False
    >>> is_js_file('ui.combobox.js')
    True

    """
    return filename.endswith('.js') \
       and not '.pack' in filename \
       and not '.min' in filename

def list_js_files(dir):
    """
    Generator for all JavaScript files in the directory, recursively

    >>> list_js_files('examples').next()
    'examples/module.js'

    """
    for dirpath, dirnames, filenames in os.walk(dir):
        for filename in filenames:
            if is_js_file(filename):
                yield os.path.join(dirpath, filename)

def get_file_list(paths):
    """
    Returns a list of all JS files, given the root paths.
    """
    return flatten(list_js_files(path) for path in paths)

def read_file(path):
    """
    Opens a file, reads it into a string, closes the file, and returns
    the file text.
    """
    fd = open(path)
    try:
        return fd.read()
    finally:
        fd.close()

def save_file(path, text):
    """
    Saves a string to a file
    """
    fd = open(path, 'w')
    try:
        fd.write(text)
    finally:
        fd.close()

##### Parsing utilities #####

def split_delimited(delimiters, split_by, text):
    """
    Generator that walks the ``text`` and splits it into an array on
    ``split_by``, being careful not to break inside a delimiter pair.
    ``delimiters`` should be an even-length string with each pair of matching
    delimiters listed together, open first.


    >>> list(split_delimited('{}[]', ',', ''))
    ['']
    >>> list(split_delimited('', ',', 'foo,bar'))
    ['foo', 'bar']
    >>> list(split_delimited('[]', ',', 'foo,[bar, baz]'))
    ['foo', '[bar, baz]']
    >>> list(split_delimited('{}', ' ', '{Type Name} name Desc'))
    ['{Type Name}', 'name', 'Desc']
    >>> list(split_delimited('[]{}', ',', '[{foo,[bar, baz]}]'))
    ['[{foo,[bar, baz]}]']

    Two adjacent delimiters result in a zero-length string between them:

    >>> list(split_delimited('{}', ' ', '{Type Name}  Desc'))
    ['{Type Name}', '', 'Desc']

    ``split_by`` may be a predicate function instead of a string, in which
    case it should return true on a character to split.

    >>> list(split_delimited('', lambda c: c in '[]{}, ', '[{foo,[bar, baz]}]'))
    ['', '', 'foo', '', 'bar', '', 'baz', '', '', '']

    """
    delims = [0] * (len(delimiters) / 2)
    actions = {}
    for i in xrange(0, len(delimiters), 2):
        actions[delimiters[i]] = (i / 2, 1)
        actions[delimiters[i + 1]] = (i / 2, -1)

    if isinstance(split_by, str):
        def split_fn(c): return c == split_by
    else:
        split_fn = split_by
    last = 0

    for i in xrange(len(text)):
        c = text[i]
        if split_fn(c) and not any(delims):
            yield text[last:i]
            last = i + 1
        try:
            which, dir = actions[c]
            delims[which] = delims[which] + dir
        except KeyError:
            pass # Normal character
    yield text[last:]

def get_doc_comments(text):
    r"""
    Returns a list of all documentation comments in the file text.  Each
    comment is a pair, with the first element being the comment text and
    the second element being the line after it, which may be needed to
    guess function & arguments.

    >>> get_doc_comments(read_file('examples/module.js'))[0][0][:40]
    '/**\n * This is the module documentation.'
    >>> get_doc_comments(read_file('examples/module.js'))[1][0][7:50]
    'This is documentation for the first method.'
    >>> get_doc_comments(read_file('examples/module.js'))[1][1]
    'function the_first_function(arg1, arg2) '
    >>> get_doc_comments(read_file('examples/module.js'))[2][0]
    '/** This is the documentation for the second function. */'


    """
    def make_pair(match):
        comment = match.group()
        try:
            end = text.find('\n', match.end(0)) + 1
            if '@class' not in comment:
                next_line = split_delimited('()', '\n', text[end:]).next()
            else:
                next_line = text[end:text.find('\n', end)]
        except StopIteration:
            next_line = ''
        return (comment, next_line)
    return [make_pair(match) for match in re.finditer('/\*\*(.*?)\*/', 
            text, re.DOTALL)]

def strip_stars(doc_comment):
    r"""
    Strips leading stars from a doc comment.  

    >>> strip_stars('/** This is a comment. */')
    'This is a comment.'
    >>> strip_stars('/**\n * This is a\n * multiline comment. */')
    'This is a\n multiline comment.'
    >>> strip_stars('/** \n\t * This is a\n\t * multiline comment. \n*/')
    'This is a\n multiline comment.'

    """
    return re.sub('\n\s*?\*\s*?', '\n', doc_comment[3:-2]).strip()

def split_tag(section):
    """
    Splits the JSDoc tag text (everything following the @) at the first
    whitespace.  Returns a tuple of (tagname, body).
    """
    splitval = re.split('\s+', section, 1)
    tag, body = len(splitval) > 1 and splitval or (splitval[0], '')
    return tag.strip(), body.strip()

FUNCTION_REGEXPS = [
    'function (\w+)',
    '(\w+):\sfunction',
    '\.(\w+)\s*=\s*function',
]

def guess_function_name(next_line, regexps=FUNCTION_REGEXPS):
    """
    Attempts to determine the function name from the first code line
    following the comment.  The patterns recognized are described by
    `regexps`, which defaults to FUNCTION_REGEXPS.  If a match is successful, 
    returns the function name.  Otherwise, returns None.
    """
    for regexp in regexps:
        match = re.search(regexp, next_line)
        if match:
            return match.group(1)
    return None

def guess_parameters(next_line):
    """
    Attempts to guess parameters based on the presence of a parenthesized
    group of identifiers.  If successful, returns a list of parameter names;
    otherwise, returns None.
    """
    match = re.search('\(([\w\s,]+)\)', next_line)
    if match:
        return [arg.strip() for arg in match.group(1).split(',')]
    else:
        return None

def parse_comment(doc_comment, next_line):
    r"""
    Splits the raw comment text into a dictionary of tags.  The main comment
    body is included as 'doc'.

    >>> comment = get_doc_comments(read_file('examples/module.js'))[4][0]
    >>> parse_comment(strip_stars(comment), '')['doc']
    'This is the documentation for the fourth function.\n\n Since the function being documented is itself generated from another\n function, its name needs to be specified explicitly. using the @function tag'
    >>> parse_comment(strip_stars(comment), '')['function']
    'not_auto_discovered'

    If there are multiple tags with the same name, they're included as a list:

    >>> parse_comment(strip_stars(comment), '')['param']
    ['{String} arg1 The first argument.', '{Int} arg2 The second argument.']

    """
    sections = re.split('\n\s*@', doc_comment)
    tags = { 
        'doc': sections[0].strip(),
        'guessed_function': guess_function_name(next_line),
        'guessed_params': guess_parameters(next_line)
    }
    for section in sections[1:]:
        tag, body = split_tag(section)
        if tag in tags:
            existing = tags[tag]
            try:
                existing.append(body)
            except AttributeError:
                tags[tag] = [existing, body]
        else:
            tags[tag] = body
    return tags

def parse_comments_for_file(filename):
    """
    Returns a list of all parsed comments in a file.  Mostly for testing &
    interactive use.
    """
    return [parse_comment(strip_stars(comment), next_line)
            for comment, next_line in get_doc_comments(read_file(filename))]


#### Classes #####

class CodeBaseDoc(dict):
    """
    Represents the documentation for an entire codebase.

    This takes a list of root paths and a list of prefixes to chop off the
    beginning of each filename.  The resulting object acts like a dictionary of
    FileDoc objects.  
    
    The dictionary may either be keyed by the basename of the
    file (the default) or by having ``prefix`` chopped off the beginning of
    each full filename.  You may pass multiple prefixes as a list; the full
    filename is tested against each and chopped if it matches.

    >>> CodeBaseDoc(['examples']).keys()
    ['module_closure.js', 'module.js', 'class.js', 'subclass.js']

    It also handles dependency & subclass analysis, setting the appropriate
    fields on the contained objects.  Note that the keys (after prefix
    chopping) should match the names declared in @dependency or @see tags;
    otherwise, you may get MissingDependencyErrors:

    >>> CodeBaseDoc(['examples'], '').keys()
    Traceback (most recent call last):
    MissingDependency: Couldn't find dependency module.js when processing examples/module_closure.js

    """

    def __init__(self, root_paths, prefix=None):
        if isinstance(prefix, str):
            prefix = [prefix]

        self.populate_files(root_paths, prefix)
        self.build_dependencies()
        self.build_superclass_lists()

    def populate_files(self, root_paths, prefix):
        files = get_file_list(root_paths)
        def key_name(file_name):
            if prefix is None:
                return os.path.basename(file_name)
            for pre in prefix:
                if file_name.startswith(pre):
                    return file_name[len(pre):]
            return file_name

        for file in files:
            name = key_name(file)
            self[name] = FileDoc(name, read_file(file))

    def build_dependencies(self):
        """
        >>> CodeBaseDoc(['examples'])['subclass.js'].all_dependencies
        ['module.js', 'module_closure.js', 'class.js', 'subclass.js']
        """
        for module in self.values():
            module.set_all_dependencies(find_dependencies([module.name], self))

    def build_superclass_lists(self):
        """
        >>> CodeBaseDoc(['examples']).all_classes['MySubClass'].all_superclasses[0].name
        'MyClass'
        """
        cls_dict = self.all_classes
        for cls in cls_dict.values():
            cls.all_superclasses = []
            superclass = cls.superclass
            try:
                while superclass:
                    superclass_obj = cls_dict[superclass]
                    cls.all_superclasses.append(superclass_obj)
                    superclass = superclass_obj.superclass
            except KeyError:
                print "Missing superclass: " + superclass

    def _module_index(self, attr):
        return dict((obj.name, obj) for module in self.values()
                                    for obj in getattr(module, attr))

    @property
    def all_functions(self):
        """
        Returns a dict of all functions in all modules of the codebase,
        keyed by their name.
        """
        return self._module_index('functions')

    @property
    def all_methods(self):
        """
        Returns a dict of all methods in all modules.
        """
        return self._module_index('methods')

    @property
    def all_classes(self):
        """
        Returns a dict of all classes in all modules.
        """
        return self._module_index('classes')

    def to_json(self):
        return encode_json(self.to_dict())

    def to_dict(self):
        return dict((key, val.to_dict()) for key, val in self.items())

    def to_html(self):
        """
        Builds basic HTML for the full module index.
        """
        def entry_html(file):
            return ('<dt><a href = "%(name)s">%(name)s</a></dt>\n' +
                    '<dd>%(short_doc)s</dd>') % file.to_dict()
        return '<dl>\n%s\n</dl>' % '\n'.join(
                entry_html(f) for f in self.values())

class FileDoc(object):
    """
    Represents documentaion for an entire file.  The constructor takes the
    source text for file, parses it, then provides a class wrapper around
    the parsed text.
    """

    def __init__(self, file_name, file_text):
        self.name = file_name
        self.order = []
        self.comments = { 'file_overview': ModuleDoc({}) }
        is_first = True
        for comment, next_line in get_doc_comments(file_text):
            raw = parse_comment(strip_stars(comment), next_line)

            if 'fileoverview' in raw:
                obj = ModuleDoc(raw)
            elif raw.get('function') or raw.get('guessed_function'):
                obj = FunctionDoc(raw)
            elif raw.get('class'):
                obj = ClassDoc(raw)
            elif is_first:
                obj = ModuleDoc(raw)
            else:
                continue

            self.order.append(obj.name)
            self.comments[obj.name] = obj
            is_first = False

        for method in self.methods:
            try:
                self.comments[method.member].add_method(method)
            except AttributeError:
                warn('member %s of %s is not a class', 
                            method.member, method.name)
            except KeyError:
                pass

    def __str__(self):
        return "Docs for file " + self.name

    def keys(self):
        """
        Returns all legal names for doc comments.

        >>> file = FileDoc('module.js', read_file('examples/module.js'))
        >>> file.keys()[1]
        'the_first_function'
        >>> file.keys()[4]
        'not_auto_discovered'

        """
        return self.order

    def values(self):
        """
        Same as list(file_doc).

        >>> file = FileDoc('module.js', read_file('examples/module.js'))
        >>> file.values()[0].doc[:30]
        'This is the module documentati'

        """
        return list(self)

    def __iter__(self):
        """
        Returns all comments from the file, in the order they appear.
        """
        return (self.comments[name] for name in self.order)

    def __getitem__(self, index):
        """
        If `index` is a string, returns the named method/function/class 
        from the file.

        >>> file = FileDoc('module.js', read_file('examples/module.js'))
        >>> file['the_second_function'].doc
        'This is the documentation for the second function.'

        If `index` is an integer, returns the ordered comment from the file.

        >>> file[0].name
        'file_overview'
        >>> file[0].doc[:30]
        'This is the module documentati'

        """
        if isinstance(index, int):
            return self.comments[self.order[index]]
        else:
            return self.comments[index]

    def set_all_dependencies(self, dependencies):
        """
        Sets the `all_dependencies` property on the module documentation.
        """
        self.comments['file_overview'].all_dependencies = dependencies

    def _module_prop(self, name):
        return getattr(self.comments['file_overview'], name)

    @property
    def doc(self):
        return self._module_prop('doc')

    @property
    def short_doc(self):
        return self._module_prop('short_doc')

    @property
    def author(self):
        return self._module_prop('author')

    @property
    def version(self):
        return self._module_prop('version')

    @property
    def dependencies(self):
        """
        Returns the immediate dependencies of a module (only those that are
        explicitly declared).  Use the `all_dependencies` field for transitive
        dependencies - the FileDoc must have been created by a CodeBaseDoc for
        this field to exist.

        >>> FileDoc('', read_file('examples/module_closure.js')).dependencies
        ['module.js']
        >>> FileDoc('subclass.js', read_file('examples/subclass.js')).dependencies
        ['module_closure.js', 'class.js']

        """
        return self._module_prop('dependencies')

    @property
    def all_dependencies(self):
        return self._module_prop('all_dependencies')

    def _filtered_iter(self, pred):
        return (self.comments[name] for name in self.order 
                if pred(self.comments[name]))

    @property
    def functions(self):
        """
        Returns a generator of all standalone functions in the file, in textual
        order.

        >>> file = FileDoc('module.js', read_file('examples/module.js'))
        >>> list(file.functions)[0].name
        'the_first_function'
        >>> list(file.functions)[3].name
        'not_auto_discovered'

        """
        def is_function(comment):
            return isinstance(comment, FunctionDoc) and not comment.member
        return self._filtered_iter(is_function)

    @property
    def methods(self):
        """
        Returns a generator of all member functions in the file, in textual
        order.  

        >>> file = FileDoc('class.js', read_file('examples/class.js'))
        >>> file.methods.next().name
        'first_method'

        """
        def is_method(comment):
            return isinstance(comment, FunctionDoc) and comment.member
        return self._filtered_iter(is_method)

    @property
    def classes(self):
        """
        Returns a generator of all classes in the file, in textual order.

        >>> file = FileDoc('class.js', read_file('examples/class.js'))
        >>> cls = file.classes.next()
        >>> cls.name
        'MyClass'
        >>> cls.methods[0].name
        'first_method'

        """
        return self._filtered_iter(lambda c: isinstance(c, ClassDoc))

    def to_dict(self):
        return [comment.to_dict() for comment in self]

    def to_html(self):
        # TODO: finish this, then to_html for each CommentDoc
        return """
<h1>Module documentation for %(name)s</h1>
%(doc)s
<h2>Function Index</h2>
%(function_index)s
<h2>Class Index</h2>
%(class_index)s
<h2>Functions</h2>
%(function_body)s
<h2>Classes</h2>
%(function)s
"""

class CommentDoc(object):
    """
    Base class for all classes that represent a parsed comment of some sort.
    """
    def __init__(self, parsed_comment):
        self.parsed = parsed_comment

    def __str__(self):
        return "Docs for " + self.name

    def __repr__(self):
        return str(self)

    def __getitem__(self, tag_name):
        return self.get(tag_name)

    def get(self, tag_name, default=''):
        """
        Returns the value of a particular tag, or None if that tag doesn't
        exist.  Use 'doc' for the comment body itself.
        """
        return self.parsed.get(tag_name, default)

    def get_as_list(self, tag_name):
        """
        Returns the value of a tag, making sure that it's a list.  Absent
        tags are returned as an empty-list; single tags are returned as a
        one-element list.

        The returned list is a copy, and modifications do not affect the
        original object.
        """
        val = self.get(tag_name, [])
        if isinstance(val, list):
            return val[:]
        else:
            return [val]

    @property
    def doc(self):
        return self.get('doc')

    @property
    def short_doc(self): 
        return first_sentence(self.doc)

    def to_json(self):
        return encode_json(self.to_dict())

    def to_html(self):
        return self.DEFAULT_HTML_STRING % self.to_dict()

    def to_dict(self):
        vars = self.parsed.copy()
        vars['short_doc'] = self.short_doc

class ModuleDoc(CommentDoc):
    """
    Represents the top-level fileoverview documentation.  Much of this is
    proxied behind FileDoc, and should be accessed through that.  This class
    is to ensure consistency when the doc comments of a class are iterated
    through.
    """

    @property
    def name(self): return 'file_overview'

    @property
    def author(self): return self.get('author')

    @property
    def version(self): return self.get('version')

    @property
    def dependencies(self): return self.get_as_list('dependency')

    def to_dict(self):
        vars = super(ModuleDoc, self).to_dict()
        vars['dependencies'] = self.dependencies
        vars['name'] = self.name
        try:
            vars['all_dependencies'] = self.all_dependencies[:]
        except AttributeError:
            vars['all_dependencies'] = []
        return vars

class FunctionDoc(CommentDoc):
    r"""
    Represents documentation for a single function or method.  Takes a parsed
    comment and provides accessors for accessing the various fields.

    >>> comments = parse_comments_for_file('examples/module_closure.js')
    >>> fn1 = FunctionDoc(comments[1])
    >>> fn1.name
    'the_first_function'
    >>> fn1.doc
    'The auto-naming can pick up functions defined as fields of an object,\n as is common with classes and the module pattern.'

    """
    def __init__(self, parsed_comment):
        super(FunctionDoc, self).__init__(parsed_comment)
    
    @property
    def name(self): 
        return self.get('guessed_function') or self.get('function')

    @property
    def params(self):
        """
        Returns a ParamDoc for each parameter of the function, picking up
        the order from the actual parameter list.

        >>> comments = parse_comments_for_file('examples/module_closure.js')
        >>> fn2 = FunctionDoc(comments[2])
        >>> fn2.params[0].name
        'elem'
        >>> fn2.params[1].type
        'Function(DOM)'
        >>> fn2.params[2].doc
        'The Options array.'

        """
        tag_texts = self.get_as_list('param') + self.get_as_list('argument')
        if self.get('guessed_params') is None:
            return [ParamDoc(text) for text in tag_texts]
        else:
            param_dict = {}
            for text in tag_texts:
                param = ParamDoc(text)
                param_dict[param.name] = param
            return [param_dict.get(name) or ParamDoc('{} ' + name)
                    for name in self.get('guessed_params')]

    @property
    def options(self):
        """
        Return the options for this function, as a list of ParamDocs.  This is
        a common pattern for emulating keyword arguments.

        >>> comments = parse_comments_for_file('examples/module_closure.js')
        >>> fn2 = FunctionDoc(comments[2])
        >>> fn2.options[0].name
        'foo'
        >>> fn2.options[1].type
        'Int'
        >>> fn2.options[1].doc
        'Some other option'

        """
        return [ParamDoc(text) for text in self.get_as_list('option')]

    @property
    def return_val(self):
        """
        Returns the return value of the function, as a ParamDoc with an
        empty name:

        >>> comments = parse_comments_for_file('examples/module_closure.js')
        >>> fn1 = FunctionDoc(comments[1])
        >>> fn1.return_val.name
        ''
        >>> fn1.return_val.doc
        'Some value'
        >>> fn1.return_val.type
        'String'

        >>> fn2 = FunctionDoc(comments[2])
        >>> fn2.return_val.doc
        'Some property of the elements.'
        >>> fn2.return_val.type
        'Array<String>'

        """
        ret = self.get('return') or self.get('returns')
        type = self.get('type')
        if '{' in ret and '}' in ret:
            if not '}  ' in ret:
                # Ensure that name is empty
                ret = ret.replace('} ', '}  ')
            return ParamDoc(ret)
        if ret and type:
            return ParamDoc('{%s}  %s' % (type, ret))
        return ParamDoc(ret)

    @property
    def throws(self):
        """
        Returns a list of ParamDoc objects (with empty names) of the
        exception tags for the function.

        >>> comments = parse_comments_for_file('examples/module_closure.js')
        >>> fn1 = FunctionDoc(comments[1])
        >>> fn1.throws[0].doc
        'Another exception'
        >>> fn1.throws[1].doc
        'A fake exception'
        >>> fn1.throws[1].type
        'String'

        """
        def make_param(text):
            if '{' in text and '}' in text:
                # Make sure param name is blank:
                word_split = list(split_delimited('{}', ' ', text))
                if word_split[1] != '':
                    text = ' '.join([word_split[0], ''] + word_split[1:])
            else:
                # Handle old JSDoc format
                word_split = text.split()
                text = '{%s}  %s' % (word_split[0], ' '.join(word_split[1:]))
            return ParamDoc(text)
        return [make_param(text) for text in 
                self.get_as_list('throws') + self.get_as_list('exception')]

    @property
    def is_private(self):
        return 'private' in self.parsed

    @property
    def member(self):
        return self.get('member')

    @property
    def is_constructor(self):
        return 'constructor' in self.parsed

    def to_dict(self):
        vars = super(FunctionDoc, self).to_dict()
        vars.update({
            'name': self.name,
            'params': [param.to_dict() for param in self.params],
            'options': [option.to_dict() for option in self.options],
            'throws': [exc.to_dict() for exc in self.throws],
            'return_val': self.return_val.to_dict(),
            'is_private': self.is_private,
            'is_constructor': self.is_constructor,
            'member': self.member
        })
        return vars

class ClassDoc(CommentDoc):
    """
    Represents documentation for a single class.
    """
    def __init__(self, parsed_comment):
        super(ClassDoc, self).__init__(parsed_comment)
        self.methods = []
        # Methods are added externally with add_method, after construction

    @property
    def name(self):
        return self.get('class')

    @property
    def superclass(self):
        """
        Returns the immediate superclass name of the class, as a string.  For
        the full inheritance chain, use the `all_superclasses` property, which
        returns a list of objects and only works if this ClassDoc was created
        from a CodeBaseDoc.
        """
        return self.get('extends') or self.get('base')

    @property
    def constructors(self):
        return [fn for fn in self.methods if fn.is_constructor]

    def add_method(self, method):
        self.methods.append(method)

    def to_dict(self):
        vars = super(ClassDoc, self).to_dict()
        vars.update({
            'name': self.name,
            'method': [method.to_dict() for method in self.methods]
        })
        return vars

class ParamDoc(object):
    """
    Represents a parameter, option, or parameter-like object, basically
    anything that has a name, a type, and a description, separated by spaces.
    This is also used for return types and exceptions, which use an empty
    string for the name.

    >>> param = ParamDoc('{Array<DOM>} elems The elements to act upon')
    >>> param.name
    'elems'
    >>> param.doc
    'The elements to act upon'
    >>> param.type
    'Array<DOM>'

    You can also omit the type: if the first element is not surrounded by
    curly braces, it's assumed to be the name instead:

    >>> param2 = ParamDoc('param1 The first param')
    >>> param2.type
    ''
    >>> param2.name
    'param1'
    >>> param2.doc
    'The first param'

    """
    def __init__(self, text):
        parsed = list(split_delimited('{}', ' ', text))
        if parsed[0].startswith('{') and parsed[0].endswith('}'):
            self.type = parsed[0][1:-1]
            self.name = parsed[1]
            self.doc = ' '.join(parsed[2:])
        else:
            self.type = ''
            self.name = parsed[0]
            self.doc = ' '.join(parsed[1:])

    def to_dict(self):
        return {
            'name': self.name,
            'type': self.type,
            'doc': self.doc
        }

##### DEPENDENCIES #####

class CyclicDependency(Exception):
    """
    Exception raised if there is a cyclic dependency.
    """
    def __init__(self, remaining_dependencies):
        self.values = remaining_dependencies

    def __str__(self):
        return ('The following dependencies result in a cycle: '
              + ', '.join(self.values))

class MissingDependency(Exception):
    """
    Exception raised if a file references a dependency that doesn't exist.
    """
    def __init__(self, file, dependency):
        self.file = file
        self.dependency = dependency

    def __str__(self):
        return "Couldn't find dependency %s when processing %s" % \
                (self.dependency, self.file)


def build_dependency_graph(start_nodes, js_doc):
    """
    Builds a graph where nodes are filenames and edges are reverse dependencies
    (so an edge from jquery.js to jquery.dimensions.js indicates that jquery.js
    must be included before jquery.dimensions.js).  The graph is represented
    as a dictionary from filename to (in-degree, edges) pair, for ease of
    topological sorting.  Also returns a list of nodes of degree zero.
    """
    queue = []
    dependencies = {}
    start_sort = []
    def add_vertex(file):
        in_degree = len(js_doc[file].dependencies)
        dependencies[file] = [in_degree, []]
        queue.append(file)
        if in_degree == 0:
            start_sort.append(file)
    def add_edge(from_file, to_file):
        dependencies[from_file][1].append(to_file)
    def is_in_graph(file):
        return file in dependencies

    for file in start_nodes:
        add_vertex(file)
    for file in queue:
        for dependency in js_doc[file].dependencies:
            if dependency not in js_doc:
                raise MissingDependency(file, dependency)
            if not is_in_graph(dependency):
                add_vertex(dependency)
            add_edge(dependency, file)
    return dependencies, start_sort 

def topological_sort(dependencies, start_nodes):
    retval = []
    def edges(node): return dependencies[node][1]
    def in_degree(node): return dependencies[node][0]
    def remove_incoming(node): dependencies[node][0] = in_degree(node) - 1
    while start_nodes:
        node = start_nodes.pop()
        retval.append(node)
        for child in edges(node):
            remove_incoming(child)
            if not in_degree(child):
                start_nodes.append(child)
    leftover_nodes = [node for node in dependencies.keys()
                      if in_degree(node) > 0]
    if leftover_nodes:
        raise CyclicDependency(leftover_nodes)
    else:
        return retval

def find_dependencies(start_nodes, js_doc):
    """ 
    Sorts the dependency graph, taking in a list of starting module names and a
    CodeBaseDoc (or equivalent dictionary).  Returns an ordered list of
    transitive dependencies such that no module appears before its
    dependencies.
    """
    return topological_sort(*build_dependency_graph(start_nodes, js_doc))

##### HTML utilities #####
def build_html_page(title, body):
    """
    Builds the simple tag skeleton for a title and body.
    """
    return """<html>
    <head><title>%s</title></head>
    <body>
        %s
    </body>
</html>""" % (title, body)

##### Command-line functions #####

def usage(command_name):
    print """
Usage: %(name)s <command> [options]

Available commands:

  depend [start files]: Generate a list of all dependencies of the specified
                        start files.
  doc [filename]: Writes HTML documentation for specified file to STDOUT
  build: Build HTML documentation for all files on the JSPath

By default, this tool recursively searches the current directory for .js files
to build up its dependency database.  This can be changed with the --input or
--jspath options (see below).

Available options:

  -p, --jspath  Directory to search for JS libraries (multiple allowed)
  -i, --input   Read available JS files from STDIN 
  -o, --output  Output to file (or directory, for build) instead of STDOUT
  -j, --json    Write output in JSON format (requires python-json module)
  -h, --help    Print usage information and exit
  -t, --test    Run PyJSDoc unit tests

Cookbook of common tasks:

  Find dependencies of the Dimensions plugin in the jQuery CVS repository, 
  filtering out packed files from the search path:

  $ find trunk/plugins -name "*.js" | grep -v pack | %(name)s -i depend jquery.dimensions.js

  Concatenate dependent plugins into a single file for web page:

  $ %(name)s depend myplugin1.js myplugin2.js | xargs cat > scripts.js

  Read documentation information for form plugin (including full dependencies),
  and include on a PHP web page using the PHP Services_JSON module:

  <?php
  $json = new Services_JSON();
  $jsdoc = $json->decode(`jsdoc doc jquery.form.js -j -p trunk/plugins`);
  ?>

  Build documentation for all plugins on your system:

  $ %(name)s build -o /var/www/htdocs/jqdocs
""" % {'name': os.path.basename(command_name) }

def get_path_list(opts):
    """
    Returns a list of all root paths where JS files can be found, given the
    command line options for this script.
    """
    paths = []
    for opt, arg in opts:
        if opt in ('-i', '--input'):
            return [line.strip() for line in sys.stdin.readlines()]
        elif opt in ('-p', '--jspath'):
            paths.append(arg)
    return paths or [os.getcwd()]

def main():
    """
    Main command-line invocation.
    """

    if '--test' in sys.argv:
        import doctest
        doctest.testmod()
        sys.exit(0)

    try:
        opts, args = getopt.gnu_getopt(sys.argv[1:], 'p:io:jt', [
            'jspath=', 'input', 'output=', 'json', 'test'])
    except getopt.GetoptError:
        usage(sys.argv[0])
        sys.exit(2)

    js_paths = get_path_list(opts)
    js_files = get_file_list(js_paths)

    try:
       data_fn = globals()[args[0] + '_data']
       format_fn = globals()[args[0] + '_format']
    except (KeyError, IndexError):
        usage(sys.argv[0])
        sys.exit(2)

    show_json = False
    output_file = False
    for opt, arg in opts:
        if opt in ['-j', '--json']:
            show_json = True
        elif opt in ['-o', '--output']:
            output_file = arg

    def add_trailing_slash(path):
        return path + ('/' if not path.endswith('/') else '')
    js_paths = map(add_trailing_slash, js_paths)

    try:
        result = data_fn(args, js_paths, js_files)
        output = show_json and json_format(result) or \
                 format_fn(args, result, js_files, output_file)
        if output_file and format_fn != build_format:
            save_file(output_file, output)
        else:
            print output
    except ArgNotFound, e:
        print e

if __name__ == '__main__':
    main()
