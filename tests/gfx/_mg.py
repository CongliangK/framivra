"""Shared MG test execution, without caching modules or changing skip policy."""
import importlib.util


def load_renderer(name, path):
    """Load a fresh renderer under the caller's existing module identity."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

