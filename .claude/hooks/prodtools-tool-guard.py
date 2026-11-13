#!/usr/bin/env python3
"""PreToolUse(Bash|Write) guard: use the prodtools tools before hand-rolling.

Denies a Bash command, or a Write of a script outside this repo, that
reaches SAM or /pnfs by hand when a prodtools CLI tool or MCP tool already
does the job: samweb_client in inline Python, SHA-256 path building,
curl/wget to the SAM web API, and bulk scans of /pnfs (find, recursive
grep, globbed reads, xargs fan-out).

The deny reason names the tools to use instead. When no tool fits, the
command (or file) must carry a `# no-prodtools-tool: <reason>` comment,
which lets it through and leaves the reason in the transcript.

Anything that does not match exits 0 with no output, so the normal
permission flow proceeds. Files written inside the repo are not checked:
utils/ legitimately imports samweb_client and builds hashed paths.
"""
import json
import os
import re
import sys

OVERRIDE = re.compile(r'#\s*no-prodtools-tool:\s*\S.{3,}')

SAM_TOOLS = ('MCP find_datasets / dataset_details / dataset_files / '
             'locate_file / trace_provenance; CLI datasetFileList, '
             'listNewDatasets, famtree')
PNFS_TOOLS = ('logparser <log dataset> -n 500 (CPU, memory, wall time from '
              'job logs; mean x log count = total), genFilterEff, '
              'datasetFileList; MCP dataset_files for paths')

PYTHON = re.compile(r'\bpython[0-9.]*\b|PYTHON_BIN')
HTTP = re.compile(r'\b(curl|wget|requests|urllib|http\.client)\b')
PNFS = re.compile(r'/pnfs/')

# (name, regex, extra regex the text must ALSO match or None, use instead).
# The extra regex keeps code searches (`grep -rn samweb_client utils/`)
# from tripping the Python- and HTTP-shaped rules.
RULES = [
    ('samweb_client in a script',
     re.compile(r'\b(import|from)\s+samweb_client\b|SAMWebClient\('),
     PYTHON, SAM_TOOLS),
    ('SHA-256 path building',
     re.compile(r'hashlib\.sha256\('), PYTHON,
     'MCP dataset_files or datasetFileList return the /pnfs paths'),
    ('direct SAM web API call',
     re.compile(r'samweb\.fnal\.gov|/sam/mu2e/api/'), HTTP, SAM_TOOLS),
    # One-off `samweb` CLI calls stay allowed: retire-file, prestage,
    # check-completeness and jit-cnf-build run them on purpose.
    ('find over /pnfs',
     re.compile(r'\bfind\s[^|;&\n]*/pnfs/'), None, PNFS_TOOLS),
    ('recursive grep over /pnfs',
     re.compile(r'\b[ez]?grep\s+(-\w*[rR]\w*\s|--recursive)[^|;&\n]*/pnfs/'),
     None, PNFS_TOOLS),
    ('globbed read over /pnfs',
     re.compile(r'\b(grep|zgrep|egrep|awk|cat|head|tail|wc)\b[^|;&\n]*'
                r'/pnfs/\S*[*?]'), None, PNFS_TOOLS),
    ('xargs fan-out over /pnfs files',
     re.compile(r'\bxargs\b[^|;&\n]*\b(grep|zgrep|awk|cat|head|tail)\b'),
     PNFS, PNFS_TOOLS),
]


def deny(reason):
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PreToolUse',
        'permissionDecision': 'deny',
        'permissionDecisionReason': reason}}))
    sys.exit(0)


def main():
    payload = json.load(sys.stdin)
    tool = payload.get('tool_name', '')
    ti = payload.get('tool_input') or {}
    if tool == 'Bash':
        text = ti.get('command') or ''
    elif tool == 'Write':
        path = os.path.realpath(ti.get('file_path') or '')
        repo = os.path.realpath(os.environ.get('CLAUDE_PROJECT_DIR')
                                or os.getcwd())
        if path == repo or path.startswith(repo + os.sep):
            return
        text = ti.get('content') or ''
    else:
        return
    if OVERRIDE.search(text):
        return
    for name, rx, also, instead in RULES:
        if rx.search(text) and (also is None or tool == 'Write'
                                or also.search(text)):
            deny(f'prodtools guard: {name}. A prodtools tool likely does '
                 f'this already: {instead}. Read EXAMPLES.md and the '
                 f'question-to-tool table in CLAUDE.md first. If none fits, '
                 f'rerun with a "# no-prodtools-tool: <why>" comment.')


if __name__ == '__main__':
    main()
