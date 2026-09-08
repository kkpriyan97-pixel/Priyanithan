# Priyanithan runtime compatibility patch.
import importlib.abc, importlib.machinery, sys

class _Loader(importlib.abc.Loader):
    def __init__(self, original): self.original = original
    def create_module(self, spec): return None
    def exec_module(self, module):
        source = self.original.get_source(module.__name__)
        source = source.replace('            discovered_assets.clear()\n', '            # Preserve incremental broker catalogue updates.\n')
        source = source.replace('        if AUTO_DISCOVER_ASSETS and not MANUAL_PAIRS:\n', '        if AUTO_DISCOVER_ASSETS:\n')
        source = source.replace('            universe = MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:]\n            if AUTO_DISCOVER_ASSETS and discovered_assets:\n                universe = sorted(discovered_assets.keys())\n', '            if AUTO_DISCOVER_ASSETS and discovered_assets:\n                universe = sorted(discovered_assets.keys())\n            else:\n                universe = MANUAL_PAIRS[:] if MANUAL_PAIRS else PAIRS[:]\n')
        exec(compile(source, self.original.path, 'exec'), module.__dict__)

class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'app': return None
        sys.meta_path.remove(self)
        try: spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally: sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None: return None
        spec.loader = _Loader(spec.loader)
        return spec

sys.meta_path.insert(0, _Finder())
