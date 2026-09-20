"""stage_efficiency: generated/passed counts and their chain product.

A thin wrapper over utils.stage_eff. Read-only: a dataset stage reads SAM
metadata, a file stage opens art files in a `muse setup SimJob` subprocess
(this server's ops environment has no ROOT) and writes nothing.

Files are opened by the account that runs this server. Over stdio that is the
caller; on the shared HTTP endpoint it is the host account, which sees /pnfs
and group-readable /exp areas but not a private directory.
"""
from prodtools_mcp.adapters import ToolError, classify_catalog_error

MAX_STAGES = 8
MAX_FILES_PER_STAGE = 500


def stage_efficiency(stages, prescales=None, musing=None, evaluate_fn=None):
    """`evaluate_fn` is injectable for tests; production uses
    utils.stage_eff.stage_efficiency."""
    from utils import stage_eff

    if not isinstance(stages, list) or not stages:
        raise ToolError('invalid_argument',
                        'stages must be a non-empty list of stages',
                        'Pass e.g. [["sim.mu2e.MuBeamCat.Run1Bai.art"], '
                        '["/path/a.art", "/path/b.art"]], upstream first.')
    if len(stages) > MAX_STAGES:
        raise ToolError('invalid_argument',
                        f'{len(stages)} stages; at most {MAX_STAGES}')
    for number, stage in enumerate(stages, start=1):
        if (not isinstance(stage, list) or not stage
                or not all(isinstance(item, str) and item for item in stage)):
            raise ToolError('invalid_argument',
                            f'stage {number} must be a non-empty list of strings',
                            'One dataset name, or file paths / globs / xrootd URLs.')
        if len(stage) > MAX_FILES_PER_STAGE:
            raise ToolError('invalid_argument',
                            f'stage {number} lists {len(stage)} files; at most '
                            f'{MAX_FILES_PER_STAGE}',
                            'Give the dataset name instead; SAM sums every file.')

    # JSON object keys are strings; the library keys stages by number.
    selected = {}
    for key, label in (prescales or {}).items():
        if not str(key).isdigit() or not isinstance(label, str) or not label:
            raise ToolError('invalid_argument',
                            f'prescales entry {key!r}: {label!r} is not '
                            '{"<stage number>": "<module label>"}')
        selected[int(key)] = label

    run = evaluate_fn or stage_eff.stage_efficiency
    try:
        return run(stages, selected, musing)
    except stage_eff.StageEffError as exc:
        raise ToolError('invalid_argument', str(exc),
                        'Check the file list and prescale labels; the reply '
                        'of a call without `prescales` lists the labels a '
                        'file stage carries.') from exc
    except ToolError:
        raise
    except Exception as exc:
        # What is left is the SAM path of a dataset stage.
        raise classify_catalog_error(
            exc, f'stage_efficiency failed: {type(exc).__name__}: {exc}') from exc
