#!/usr/bin/env python3
"""
Wrapper for samweb_client Python module — the single SAM access path.

All SAM dimension grammar in prodtools is composed in this module.
Callers must not hand-write "dh.dataset ..." / "defname: ..." strings;
they call a named query method, or build the string with a q_* helper
when the query itself is an argument (e.g. create_definition).

Error-mode policy: every method fails loud. A SAM outage, expired
token or malformed query raises (samweb_client.exceptions.Error or a
subclass) instead of masquerading as an empty/zero result. An empty
return means SAM answered "no files" / "no locations", nothing else.
The one exception is locate_file(), which maps FileNotFound to "" —
a file SAM does not know has no location, and its callers use that
as an existence probe. definition_creation_date() is a documented
dashboard fail-soft: a missing date is None, but only for SAM-raised
errors.
"""

import functools
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from samweb_client import SAMWebClient #type: ignore
from samweb_client import Error as SAMError, FileNotFound  # type: ignore


# SAM rejects getMultipleMetadata outright above this many names
# ("Too many files requested (max 1000)") rather than truncating, so
# every batch caller has to respect it.
MAX_METADATA_BATCH = 1000

# ---------------------------------------------------------------------------
# SAM dimension grammar — query-string builders
# ---------------------------------------------------------------------------

def q_dataset(dataset: str, with_events: bool = False,
              availability: Optional[str] = None) -> str:
    """Dimension string selecting the files of a dataset."""
    q = f"dh.dataset {dataset}"
    if with_events:
        q += " and event_count>0"
    if availability:
        q += f" with availability {availability}"
    return q


def _q_definition(defname: str, with_events: bool = False) -> str:
    """Dimension string selecting the files of a SAM definition."""
    q = f"defname: {defname}"
    if with_events:
        q += " and event_count>0"
    return q


def _q_definition_files(defname: str, availability: Optional[str]) -> str:
    """Dimension string for a definition's files with an availability
    constraint (shared by the full-list and first-file readers)."""
    q = f"defname: {defname}"
    if availability:
        q += f" with availability {availability}"
    return q


def _parse_sam_datetime(date_str) -> Optional[datetime]:
    """Parse a SAM-rendered timestamp to a naive datetime (timezone
    dropped — SQLite consumers store naive UTC). Returns None on
    unparseable input."""
    if not date_str:
        return None
    date_str = str(date_str).strip()
    if '+' in date_str:
        date_str = date_str.split('+')[0]
    elif date_str.endswith('Z'):
        date_str = date_str[:-1]
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
    return None


def _q_dataset_like(pattern: str, sequencer: Optional[str] = None) -> str:
    """Files whose dataset matches a SAM `like` pattern (% wildcards),
    optionally pinned to one sequencer."""
    q = f"dh.dataset like '{pattern}'"
    if sequencer:
        q += f" and dh.sequencer {sequencer}"
    return q


def _q_parents_of_dataset(dataset: str) -> str:
    """Files that are parents of any file in `dataset`."""
    return f"isparentof: (dh.dataset {dataset})"


def _q_children_of_file(filename: str) -> str:
    """Files that are children of `filename`."""
    return f"ischildof: (file_name {filename})"


def _q_parents_of_file(filename: str) -> str:
    """Files that are parents of `filename`."""
    return f"isparentof: (file_name {filename})"


def q_recent_files(filetype: str, user: str, since_date: str) -> str:
    """Files of `filetype` created by `user` after `since_date` (YYYY-MM-DD)."""
    return f"Create_Date > {since_date} and file_format {filetype} and user {user}"

class SAMWebWrapper:
    """Wrapper for samweb_client to replace external samweb commands."""
    
    def __init__(self):
        """Init the SAMWeb client. Experiment must resolve even on grid
        workers where SAM_EXPERIMENT may be unset (jobfcl's inner loop),
        so fall back to 'mu2e' explicitly rather than samweb_client's
        env-only default."""
        experiment = (os.environ.get('SAM_EXPERIMENT')
                      or os.environ.get('EXPERIMENT') or 'mu2e')
        self.client = SAMWebClient(experiment=experiment)
    
    def count_files(self, query: str) -> int:
        """Count files matching a query (equivalent to samweb count-files).
        Raises on SAM errors."""
        return self.client.countFiles(query)

    def list_files(self, query: str) -> List[str]:
        """List files matching a query (equivalent to samweb list-files).
        Raises on SAM errors; [] means SAM matched nothing."""
        return self.client.listFiles(query)

    def locate_file(self, filename: str) -> str:
        """First location of a file (equivalent to samweb locate-file),
        or "" when the file is unknown to SAM or has no locations —
        the existence-probe contract json2jobdef's pushout path uses.
        Every other SAM error (outage, auth) raises."""
        try:
            locations = self.client.locateFile(filename)
        except FileNotFound:
            return ""
        return locations[0] if locations else ""
    
    def create_definition(self, definition_name: str, query: str) -> None:
        """Create a definition (equivalent to samweb create-definition).
        Raises samweb exceptions (e.g., DefinitionAlreadyExists, SAMWebHTTPError)
        on failure — write ops should fail loudly, not silently."""
        self.client.createDefinition(definition_name, query)

    def delete_definition(self, definition_name: str) -> None:
        """Delete a definition (equivalent to samweb delete-definition).
        Raises samweb exceptions (e.g., DefinitionNotFound) on failure."""
        self.client.deleteDefinition(definition_name)
    
    def describe_definition(self, definition_name: str) -> str:
        """Describe a definition (equivalent to samweb describe-definition).
        Raises on SAM errors (DefinitionNotFound included)."""
        return self.client.descDefinition(definition_name)
    
    def list_definition_files(self, definition_name: str, availability: str = "anylocation") -> List[str]:
        """List files in a definition (equivalent to samweb list-definition-files).
        availability constrains e.g. to 'anylocation' or 'physical'.
        Raises on SAM errors; [] means the definition is empty."""
        return self.client.listFiles(_q_definition_files(definition_name, availability))

    def first_file_in_definition(self, definition_name: str,
                                 availability: str = "anylocation") -> Optional[str]:
        """First file of a SAM definition without transferring the full
        list (streamed listFiles, closed after one name — a dataset can
        hold 100k files). Returns None for an empty definition; raises
        on SAM errors."""
        stream = self.client.listFiles(
            _q_definition_files(definition_name, availability), stream=True)
        try:
            return next(iter(stream), None)
        finally:
            close = getattr(stream, 'close', None)
            if close:
                close()

    def file_sizes_in_dataset(self, dataset: str) -> Dict[str, int]:
        """{filename: file_size} for a dataset via one list-files
        --fileinfo. Used by the input pre-flight check to get expected
        sizes without one get-metadata call per file."""
        q = q_dataset(dataset)
        return {fi.file_name: fi.file_size
                for fi in self.client.listFiles(dimensions=q, fileinfo=True)}

    def get_metadata(self, filename: str) -> Dict:
        """Get metadata for a file (equivalent to samweb get-metadata).
        Raises on SAM errors (FileNotFound included)."""
        return self.client.getMetadata(filename)

    def definition_creation_date(self, defname: str) -> Optional[datetime]:
        """Creation time of a SAM definition as a naive datetime, or None
        if unavailable. Prefers the structured JSON describe, falls back
        to parsing the text rendering (older servers). Fail-soft: an
        unknown date is treated as absent, not fatal, by dashboards —
        but only for SAM-raised errors; anything else propagates."""
        info = None
        try:
            info = self.client.descDefinitionDict(defname)
        except SAMError:
            pass
        if isinstance(info, dict):
            for key in ('create_time', 'creation_date'):
                parsed = _parse_sam_datetime(info.get(key))
                if parsed:
                    return parsed
        # Text fallback: "Creation Date: 2025-09-03T11:46:14+00:00"
        try:
            text = self.describe_definition(defname)
        except SAMError:
            return None
        match = re.search(r'Creation Date:\s+(.+)', text)
        return _parse_sam_datetime(match.group(1)) if match else None
    
    def file_lineage(self, filename: str, lineage_type: str = 'parents') -> List[str]:
        """Get file lineage via SAM client getFileLineage.
        lineage_type: 'parents', 'children', 'ancestors', 'descendants',
        or 'rawancestors'. Raises on SAM errors; [] means SAM recorded
        no lineage of that kind (e.g. a primary has no parents)."""
        result = self.client.getFileLineage(lineage_type, filename)
        return [item['file_name'] for item in result if 'file_name' in item]

    # -----------------------------------------------------------------
    # Named queries. Like everything above, these raise on SAM errors;
    # callers that can tolerate absence handle it themselves, visibly.
    # -----------------------------------------------------------------

    def files_in_dataset(self, dataset: str, with_events: bool = False,
                         availability: Optional[str] = None) -> List[str]:
        """List the files of a dataset."""
        return self.client.listFiles(q_dataset(dataset, with_events, availability))

    def dataset_file_count(self, dataset: str, with_events: bool = False) -> int:
        """Number of files in a dataset."""
        return self.client.countFiles(q_dataset(dataset, with_events))

    def dataset_summary(self, dataset: str) -> Dict:
        """SAM summary dict for a dataset (file_count, total_event_count,
        total_file_size, ...)."""
        return self.client.listFilesSummary(q_dataset(dataset))

    def definition_file_count(self, defname: str, with_events: bool = False) -> int:
        """Number of files in a SAM definition."""
        return self.client.countFiles(_q_definition(defname, with_events))

    def parents_of_dataset(self, dataset: str) -> List[str]:
        """Files that are parents of any file in `dataset`."""
        return self.client.listFiles(_q_parents_of_dataset(dataset))

    def children_of_file(self, filename: str) -> List[str]:
        """Files that are children of `filename`."""
        return self.client.listFiles(_q_children_of_file(filename))

    def parents_of_file(self, filename: str) -> List[str]:
        """Files that are parents of `filename`, excluding the etc.*.txt
        bookkeeping entries (same filter famtree.get_parents applies).

        Twin of file_lineage(filename, 'parents') built on list-files
        rather than getFileLineage; both raise on SAM errors, so an
        expired token or SAM outage never renders as 'this is a primary
        with no parents'."""
        parents = self.client.listFiles(_q_parents_of_file(filename))
        return [p for p in parents
                if not (p.startswith('etc.') and p.endswith('.txt'))]

    def files_like(self, pattern: str, sequencer: Optional[str] = None) -> List[str]:
        """Files whose dataset matches a SAM `like` pattern."""
        return self.client.listFiles(_q_dataset_like(pattern, sequencer))

    def locate_file_strict(self, filename: str) -> List[Dict]:
        """Full locate record list (no first-record pick, no FileNotFound
        mapping) — for the worker fcl-generation path, where an unknown
        file must raise rather than read as 'no location'."""
        return self.client.locateFile(filename)

    def locate_files_strict(self, filenames: List[str]) -> Dict[str, List[Dict]]:
        """Batch locate without error swallowing: one HTTP round-trip for
        the whole list instead of one per file. Same record shape as
        locate_file_strict, keyed by filename."""
        return self.client.locateFiles(filenames)

    def metadata_for_files(self, filenames: List[str]) -> List[Dict]:
        """Batch metadata: one HTTP round-trip per MAX_METADATA_BATCH
        files instead of one per file.

        Chunking lives here, not in callers: SAM rejects an oversized
        request outright, so skipping it means a hard failure instead of
        a slow path, and every new caller would rediscover the limit.
        Files unknown to SAM are silently absent from the result (samweb
        behavior). Raises on SAM errors — callers wanting warn-and-
        continue still chunk themselves and fall back to get_metadata
        per file."""
        out: List[Dict] = []
        for i in range(0, len(filenames), MAX_METADATA_BATCH):
            out.extend(self.client.getMultipleMetadata(
                filenames[i:i + MAX_METADATA_BATCH]))
        return out

    def definitions_matching(self, defname: Optional[str] = None,
                             user: Optional[str] = None) -> List[str]:
        """List definitions filtered by name pattern (% wildcard) and/or
        creating user — replaces `samweb list-definitions` CLI calls."""
        kwargs = {}
        if defname:
            kwargs['defname'] = defname
        if user:
            kwargs['user'] = user
        result = self.client.listDefinitions(**kwargs)
        if hasattr(result, '__iter__') and not isinstance(result, list):
            return list(result)
        return result


@functools.lru_cache(maxsize=1)
def get_samweb_wrapper() -> SAMWebWrapper:
    """Get or create a global SAMWeb wrapper instance.
    `lru_cache(maxsize=1)` makes lookup thread-safe (CPython's GIL +
    cache-result memoization) — replaces an earlier `if _x is None: _x = ...`
    pattern that was racy across threads."""
    return SAMWebWrapper()

# Convenience functions that match the external samweb command interface
def count_files(query: str) -> int:
    """Count files matching a query."""
    return get_samweb_wrapper().count_files(query)


def list_files(query: str) -> List[str]:
    """List files matching a query."""
    return get_samweb_wrapper().list_files(query)

def locate_file(filename: str) -> str:
    """Locate a file."""
    return get_samweb_wrapper().locate_file(filename)

def create_definition(definition_name: str, query: str) -> None:
    """Create a definition. Raises on failure."""
    get_samweb_wrapper().create_definition(definition_name, query)

def delete_definition(definition_name: str) -> None:
    """Delete a definition. Raises on failure."""
    get_samweb_wrapper().delete_definition(definition_name)

def describe_definition(definition_name: str) -> str:
    """Describe a definition."""
    return get_samweb_wrapper().describe_definition(definition_name)

def list_definition_files(definition_name: str) -> List[str]:
    """List files in a definition."""
    return get_samweb_wrapper().list_definition_files(definition_name)

def first_file_in_definition(definition_name: str,
                             availability: str = "anylocation") -> Optional[str]:
    """First file of a definition without transferring the full list."""
    return get_samweb_wrapper().first_file_in_definition(definition_name, availability)

def get_metadata(filename: str) -> Dict:
    """Get metadata for a file."""
    return get_samweb_wrapper().get_metadata(filename)

def file_sizes_in_dataset(dataset: str) -> Dict[str, int]:
    """{filename: file_size} for a dataset (one list-files --fileinfo)."""
    return get_samweb_wrapper().file_sizes_in_dataset(dataset)

def definition_creation_date(defname: str) -> Optional[datetime]:
    """Creation time of a SAM definition, or None if unavailable."""
    return get_samweb_wrapper().definition_creation_date(defname)

def file_lineage(filename: str, lineage_type: str = 'parents') -> List[str]:
    """Get file lineage using SAM client getFileLineage method."""
    return get_samweb_wrapper().file_lineage(filename, lineage_type)

# --- Named queries (fail loud) ---

def files_in_dataset(dataset: str, with_events: bool = False,
                     availability: Optional[str] = None) -> List[str]:
    """List the files of a dataset."""
    return get_samweb_wrapper().files_in_dataset(dataset, with_events, availability)

def dataset_file_count(dataset: str, with_events: bool = False) -> int:
    """Number of files in a dataset."""
    return get_samweb_wrapper().dataset_file_count(dataset, with_events)

def dataset_summary(dataset: str) -> Dict:
    """SAM summary dict for a dataset."""
    return get_samweb_wrapper().dataset_summary(dataset)

def definition_file_count(defname: str, with_events: bool = False) -> int:
    """Number of files in a SAM definition."""
    return get_samweb_wrapper().definition_file_count(defname, with_events)

def parents_of_dataset(dataset: str) -> List[str]:
    """Files that are parents of any file in `dataset`."""
    return get_samweb_wrapper().parents_of_dataset(dataset)

def children_of_file(filename: str) -> List[str]:
    """Files that are children of `filename`."""
    return get_samweb_wrapper().children_of_file(filename)

def parents_of_file(filename: str) -> List[str]:
    """Files that are parents of `filename`, raising on SAM errors."""
    return get_samweb_wrapper().parents_of_file(filename)

def files_like(pattern: str, sequencer: Optional[str] = None) -> List[str]:
    """Files whose dataset matches a SAM `like` pattern."""
    return get_samweb_wrapper().files_like(pattern, sequencer)

def locate_file_strict(filename: str) -> List[Dict]:
    """Locate a file, raising on SAM errors (no swallow)."""
    return get_samweb_wrapper().locate_file_strict(filename)

def locate_files_strict(filenames: List[str]) -> Dict[str, List[Dict]]:
    """Batch locate, raising on SAM errors (no swallow)."""
    return get_samweb_wrapper().locate_files_strict(filenames)

def metadata_for_files(filenames: List[str]) -> List[Dict]:
    """Batch metadata, raising on SAM errors (no swallow)."""
    return get_samweb_wrapper().metadata_for_files(filenames)

def definitions_matching(defname: Optional[str] = None,
                         user: Optional[str] = None) -> List[str]:
    """List definitions filtered by name pattern and/or creating user."""
    return get_samweb_wrapper().definitions_matching(defname, user)
