"""Limited current-worker source/code binding, never whole-runtime attestation.

Compile verified local source without executing it and compare complete code
objects in this interpreter. Fingerprints include local filenames/version-specific
code fields: they are audit hints, not portable or historical execution proofs.
"""
import ast
import ctypes
import contextlib
import os
from pathlib import Path
import sys
import types

from .contract import canonical, digest, file_hash, require


def _material(value):
    if isinstance(value, types.CodeType):
        return {'code': {key: _material(getattr(value, key)) for key in dir(value)
                         if key.startswith('co_') and not callable(getattr(value, key))}}
    if type(value) is dict:
        require(all(type(k) is str for k in value), 'Unsupported default mapping key')
        return {'dict': {k: _material(v) for k,v in sorted(value.items())}}
    if type(value) in (tuple, frozenset):
        items = [_material(v) for v in value]
        if type(value) is frozenset:
            items.sort(key=canonical)
        return {type(value).__name__: items}
    if type(value) is bytes:
        return {'bytes': value.hex()}
    if value is Ellipsis:
        return {'ellipsis': True}
    if value is None or type(value) in (str, int, bool, float, complex):
        return {type(value).__name__: repr(value)}
    raise ValueError('Unsupported code/default constant: ' + type(value).__name__)


def _codes(code):
    yield code
    for child in code.co_consts:
        if isinstance(child, types.CodeType):
            yield from _codes(child)


def _source_default(node, tree, module):
    if isinstance(node, ast.Name):
        definitions = [n.value for n in tree.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == node.id for t in n.targets)]
        require(len(definitions) == 1, 'Cannot bind named default: ' + node.id)
        value = ast.literal_eval(definitions[0])
        require(_material(module.__dict__[node.id]) == _material(value), 'Named default changed')
        return value
    return ast.literal_eval(node)


def check_callable(function, module, source):
    # Only the standard contextmanager decoration used by these fixed seams.
    if hasattr(function, '__wrapped__'):
        wrapped = function.__wrapped__
        helper = contextlib.contextmanager(wrapped)
        require(function.__globals__ is contextlib.__dict__
                and _material(function.__code__) == _material(helper.__code__)
                and function.__closure__ is not None and len(function.__closure__) == 1
                and function.__closure__[0].cell_contents is wrapped, 'Unknown callable wrapper')
        function = wrapped
    require(type(function) is types.FunctionType and function.__globals__ is module.__dict__
            and function.__module__ == module.__name__, 'Callable globals/module mismatch')
    filename = str(Path(module.__file__).resolve())
    require(Path(function.__code__.co_filename).resolve() == Path(filename), 'Callable source origin mismatch')
    compiled = compile(source, function.__code__.co_filename, 'exec', dont_inherit=True,
                       optimize=sys.flags.optimize)
    matches = [c for c in _codes(compiled) if c.co_name == function.__code__.co_name
               and c.co_firstlineno == function.__code__.co_firstlineno]
    require(len(matches) == 1 and _material(matches[0]) == _material(function.__code__),
            'Callable complete code differs from compiled source: ' + function.__qualname__)
    tree = ast.parse(source, filename=filename)
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == function.__name__ and min([n.lineno]+[d.lineno for d in n.decorator_list])
             == function.__code__.co_firstlineno]
    require(len(nodes) == 1, 'Cannot bind callable defaults')
    args = nodes[0].args
    defaults = tuple(_source_default(n, tree, module) for n in args.defaults) or None
    kwdefaults = {k.arg: _source_default(v, tree, module)
                  for k, v in zip(args.kwonlyargs, args.kw_defaults) if v is not None} or None
    require(_material(function.__defaults__) == _material(defaults)
            and _material(function.__kwdefaults__) == _material(kwdefaults), 'Callable defaults differ from source')
    return {'qualname': function.__qualname__, 'first_line': function.__code__.co_firstlineno,
            'code_sha256': digest(canonical(_material(function.__code__))),
            'defaults': _material(defaults), 'kwdefaults': kwdefaults,
            'status': 'MATCH_SAME_INTERPRETER'}


def _runtime_path():
    if sys.platform == 'darwin':
        library = ctypes.CDLL(None)
        function = library._NSGetExecutablePath
        function.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32)]
        function.restype = ctypes.c_int
        size = ctypes.c_uint32(16384)
        buffer = ctypes.create_string_buffer(size.value)
        require(function(buffer, ctypes.byref(size)) == 0, 'Actual runtime path unavailable')
        return Path(os.fsdecode(buffer.value)).resolve()
    if sys.platform.startswith('linux'):
        return Path('/proc/self/exe').resolve()
    raise ValueError('Actual runtime image path unsupported')


def _file(path):
    path = Path(path)
    resolved = path.resolve()
    return {'path': str(path), 'resolved': str(resolved), 'bytes': resolved.stat().st_size,
            'sha256': file_hash(resolved)}


class WorkerBinding:
    """One worker snapshot; original refs survive temporary adapter installation."""
    def __init__(self, spec, spec_file, arm, runner_module):
        from vtx import gfx_render, proc_supervisor
        from experiments.fast_png_pilot import adapter
        from . import contract, profiles, quality
        self.root = Path(runner_module.ROOT).resolve()
        self.gfx = gfx_render
        self.original_tab, self.original_browser = gfx_render.Tab, gfx_render.Browser
        self.original_helpers = {name: getattr(gfx_render, name)
                                 for name in ('_atomic_write_bytes', '_two_shot_combine')}
        selected = {
            gfx_render: ['render_page', 'Tab.__init__', 'Tab.navigate', 'Tab.call', 'Tab.eval',
                         'Tab.shot', 'Tab.close', 'Browser.__init__', 'Browser.start',
                         'Browser.open_tabs', 'Browser.stop', 'Browser.hard_restart',
                         '_atomic_write_bytes', '_two_shot_combine'],
            adapter: ['_classes', 'capture_scope'],
            runner_module: ['render_arm', 'execute_pair', 'verify_plan', 'main',
                            'owned_worker_identity', 'verify_worker_identity'],
            quality: ['run_quality', 'frame_index', 'compare_rendered_pngs'],
            contract: ['build_plan', 'read_bound'], profiles: ['continuous_plan', 'validate_selection'],
        }
        if spec['plan']['mode'] == 'poll-observation':
            from experiments.fast_png_pilot import poll_observer
            selected[poll_observer] = ['_context','disabled_observation','PollObserver.__init__',
                'PollObserver.nav_enter','PollObserver.nav_exit','PollObserver.poll_enter',
                'PollObserver.call_enter','PollObserver.call_exit','PollObserver.poll_exit',
                'PollObserver.status','PollObserver.persist']
            selected[adapter] += ['CaptureTrace.poll_observation_report','CaptureTrace.report']
            selected[profiles] += ['poll_plan']
            selected[runner_module] += ['poll_job_context','verify_poll_attempt','verify_poll_arm']
        self.sources, self.references = [], []
        for module, names in selected.items():
            path = Path(module.__file__).resolve()
            relative = path.relative_to(self.root)
            origin = getattr(module.__spec__, 'origin', None)
            require(origin is not None and Path(origin).resolve() == path, 'Module origin mismatch')
            source = path.read_bytes()
            record = {'module': module.__name__, '__file__': module.__file__, 'origin': origin,
                      'relative': str(relative), **_file(path), 'callables': []}
            for name in names:
                function = module
                for part in name.split('.'):
                    function = getattr(function, part)
                record['callables'].append(check_callable(function, module, source))
                self.references.append((module, name, function))
            self.sources.append(record)
        pid = os.getpid()
        start = proc_supervisor.proc_starttime(pid)
        require(type(start) is int and start > 0, 'Worker supervisor start identity unavailable')
        self.report = {
            'schema': 'limited-callable-source-binding/1', 'status': 'BOUND_CURRENT_WORKER',
            'namespace': spec['namespace'], 'run': spec['output'], 'arm': arm,
            'attempt': 'worker-start; per-job index references this record',
            'pid': pid, 'ppid': os.getppid(), 'pgid': os.getpgid(0), 'supervisor_starttime': start,
            'argv': list(sys.argv), 'cwd': os.getcwd(), 'spec_sha256': file_hash(spec_file),
            'plan_sha256': digest(canonical(spec['plan'])), 'implementation': spec['implementation'],
            'python_version': sys.version, 'optimization': sys.flags.optimize,
            'flags': repr(sys.flags), 'sys_executable': _file(sys.executable),
            'actual_runtime_image': _file(_runtime_path()), 'modules': self.sources,
            'restoration': 'NOT_CHECKED',
            'coverage': 'Only listed callables, including nested code, module globals identity and literal defaults. '
                        'No whole-interpreter, external-library, mutable-global or historical execution attestation.',
        }

    def restored(self):
        require(self.gfx.Tab is self.original_tab and self.gfx.Browser is self.original_browser,
                'Adapter class restoration differs from frozen references')
        for module, name, original in self.references:
            current = module
            for part in name.split('.'):
                current = getattr(current, part)
            require(current is original, 'Callable reference changed: ' + name)
        for source in self.sources:
            require(file_hash(source['resolved']) == source['sha256'], 'Source changed during worker')
        # A reference can retain its identity while its code/defaults are replaced.
        for module, name, original in self.references:
            source = next(s for s in self.sources if s['module'] == module.__name__)
            actual = check_callable(original, module, Path(source['resolved']).read_bytes())
            expected = next(c for c in source['callables'] if c['qualname'] == actual['qualname'])
            require(actual == expected, 'Callable code/default changed during worker: ' + name)
        self.report['restoration'] = 'MATCH_FROZEN_REFERENCES_AND_SOURCE'

    def installed(self, *, poll_observation=False):
        for installed, original, role in ((self.gfx.Tab,self.original_tab,'Tab'),
                                          (self.gfx.Browser,self.original_browser,'Browser')):
            expected = ['PollObserved'+role, 'Experimental'+role] if poll_observation else ['Experimental'+role]
            current = installed
            for name in expected:
                require(current.__name__ == name and current.__module__ == 'experiments.fast_png_pilot.adapter'
                        and len(current.__bases__) == 1, 'Unexpected adapter delegation layer')
                current = current.__bases__[0]
            require(current is original, 'Adapter does not delegate to frozen original classes')
        if poll_observation:
            call = self.gfx.Tab.call
            cells = dict(zip(call.__code__.co_freevars, (c.cell_contents for c in call.__closure__ or ())))
            require(cells.get('original_call') is self.original_tab.call,
                    'Poll wrapper does not delegate to frozen original Tab.call')
        for name, original in self.original_helpers.items():
            cells = self.gfx.__dict__[name].__closure__ or ()
            require(any(cell.cell_contents is original for cell in cells),
                    'Adapter helper does not retain frozen original callable')
        return {'status': 'BOUND_ORIGINAL_DELEGATES',
                'tab': self.original_tab.__qualname__, 'browser': self.original_browser.__qualname__,
                'poll_observation':poll_observation}
