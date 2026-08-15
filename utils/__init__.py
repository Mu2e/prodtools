# prodtools/utils/__init__.py
# Intentionally empty: every consumer imports submodules directly
# (`from utils.jobfcl import Mu2eJobFCL`). Eager re-exports here used to
# drag samweb_client into every `import utils.X`, forcing lazy-import
# workarounds in submit.py and other consumers.
