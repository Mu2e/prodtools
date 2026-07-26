#!/usr/bin/env python3
"""
Wrapper for samweb_client Python module — the single SAM access path.

All SAM dimension grammar in prodtools is composed in this module.
Callers must not hand-write "dh.dataset ..." / "defname: ..." strings;
they call a named query method, or build the string with a q_* helper
when the query itself is an argument (e.g. create_definition).

Error-mode policy:
- Named query methods (files_in_dataset, dataset_file_count, ...) fail
  loud: a SAM outage raises instead of masquerading as an empty/zero
  result.
- Legacy generic passthroughs (list_files, count_files, ...) keep their
  historical swallow-and-default behavior; some callers rely on it
  (e.g. prod_utils uses describe_definition() == '' as an existence
  check).
"""

import functools
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from samweb_client import SAMWebClient #type: ignore


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


def q_dataset_below_sequencer(dataset: str, sequencer_upper: str) -> str:
    """Files of `dataset` with sequencer strictly below `sequencer_upper`
    (index-definition windows)."""
    return f"dh.dataset {dataset} and dh.sequencer < {sequencer_upper}"


def q_dataset_files_named(dataset: str, filenames: List[str]) -> str:
    """Files of `dataset` restricted to an explicit basename list
    (recovery definitions)."""
    return f"dh.dataset {dataset} and file_name in ({', '.join(filenames)})"


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
        """Initialize the SAMWeb client. The experiment must resolve even
        on grid workers where SAM_EXPERIMENT may be unset (jobfcl runs in
        the worker's inner loop), so fall back to 'mu2e' explicitly rather
        than relying on samweb_client's env-only default."""
        experiment = (os.environ.get('SAM_EXPERIMENT')
                      or os.environ.get('EXPERIMENT') or 'mu2e')
        self.client = SAMWebClient(experiment=experiment)
    
    def count_files(self, query: str) -> int:
        """Count files matching a query (equivalent to samweb count-files)."""
        try:
            return self.client.countFiles(query)
        except Exception as e:
            print(f"Error counting files: {e}")
            return 0
    
    
    def list_files(self, query: str, summary: bool = False) -> List[str]:
        """List files matching a query (equivalent to samweb list-files)."""
        try:
            if summary:
                return self.client.listFilesSummary(query)
            else:
                return self.client.listFiles(query)
        except Exception as e:
            print(f"Error listing files: {e}")
            return []
    
    def locate_file(self, filename: str) -> str:
        """Locate a file (equivalent to samweb locate-file)."""
        try:
            locations = self.client.locateFile(filename)
            if locations:
                return locations[0]  # Return first location
            return ""
        except Exception as e:
            print(f"Error locating file {filename}: {e}")
            return ""
    
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
        """Describe a definition (equivalent to samweb describe-definition)."""
        try:
            return self.client.descDefinition(definition_name)
        except Exception as e:
            print(f"Error describing definition {definition_name}: {e}")
            return ""
    
    def list_definition_files(self, definition_name: str, availability: str = "anylocation") -> List[str]:
        """List files in a definition (equivalent to samweb list-definition-files).
        
        Args:
            definition_name: Name of the SAM definition
            availability: Availability constraint ('anylocation', 'physical', etc.)
        """
        try:
            return self.client.listFiles(_q_definition_files(definition_name, availability))
        except Exception as e:
            print(f"Error listing definition files for {definition_name}: {e}")
            return []

    def first_file_in_definition(self, definition_name: str,
                                 availability: str = "anylocation") -> Optional[str]:
        """First file of a SAM definition without transferring the full
        list (streamed listFiles, closed after one name — a dataset can
        hold 100k files). Returns None on SAM errors or an empty
        definition — same swallow semantics as list_definition_files,
        whose query grammar it shares."""
        try:
            stream = self.client.listFiles(
                _q_definition_files(definition_name, availability), stream=True)
            try:
                return next(iter(stream), None)
            finally:
                close = getattr(stream, 'close', None)
                if close:
                    close()
        except Exception as e:
            print(f"Error listing definition files for {definition_name}: {e}")
            return None

    def file_sizes_in_dataset(self, dataset: str) -> Dict[str, int]:
        """{filename: file_size} for a dataset via one list-files
        --fileinfo. Used by the input pre-flight check to get expected
        sizes without one get-metadata call per file."""
        q = q_dataset(dataset)
        return {fi.file_name: fi.file_size
                for fi in self.client.listFiles(dimensions=q, fileinfo=True)}

    def get_metadata(self, filename: str) -> Dict:
        """Get metadata for a file (equivalent to samweb get-metadata)."""
        try:
            return self.client.getMetadata(filename)
        except Exception as e:
            print(f"Error getting metadata for {filename}: {e}")
            return {}

    def definition_creation_date(self, defname: str) -> Optional[datetime]:
        """Creation time of a SAM definition as a naive datetime, or None
        if unavailable. Prefers the structured JSON describe; falls back
        to parsing the text rendering (older servers). Fail-soft: the
        dashboard consumers treat an unknown date as absent, not fatal."""
        info = None
        try:
            info = self.client.descDefinitionDict(defname)
        except Exception:
            pass
        if isinstance(info, dict):
            for key in ('create_time', 'creation_date'):
                parsed = _parse_sam_datetime(info.get(key))
                if parsed:
                    return parsed
        # Text fallback: "Creation Date: 2025-09-03T11:46:14+00:00"
        match = re.search(r'Creation Date:\s+(.+)', self.describe_definition(defname))
        return _parse_sam_datetime(match.group(1)) if match else None
    
    def file_lineage(self, filename: str, lineage_type: str = 'parents') -> List[str]:
        """Get file lineage using SAM client getFileLineage method.

        Args:
            filename: Name of the file to get lineage for
            lineage_type: Type of lineage ('parents', 'children', 'ancestors', 'descendants', 'rawancestors')
        """
        try:
            result = self.client.getFileLineage(lineage_type, filename)
            return [item['file_name'] for item in result if 'file_name' in item]
        except Exception as e:
            print(f"Error getting file lineage {lineage_type} for {filename}: {e}")
            return []

    # -----------------------------------------------------------------
    # Named queries — fail loud.
    # Unlike the legacy passthroughs above, these do NOT swallow
    # exceptions: a SAM outage raises instead of masquerading as an
    # empty/zero result. Callers that can tolerate absence handle it
    # themselves, visibly.
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

        Fail-loud twin of file_lineage(filename, 'parents'), which
        swallows every exception and returns []. For a lineage caller
        that empty list is indistinguishable from 'this file has no
        parents' — i.e. 'it is a primary' — so an expired token or a SAM
        outage renders as a confident, materially wrong answer. Callers
        that must tell absence from failure use this one."""
        parents = self.client.listFiles(_q_parents_of_file(filename))
        return [p for p in parents
                if not (p.startswith('etc.') and p.endswith('.txt'))]

    def files_like(self, pattern: str, sequencer: Optional[str] = None) -> List[str]:
        """Files whose dataset matches a SAM `like` pattern."""
        return self.client.listFiles(_q_dataset_like(pattern, sequencer))

    def locate_file_strict(self, filename: str) -> List[Dict]:
        """locate_file_full without the error swallowing — for the worker
        fcl-generation path, where a masked SAM error must not surface as
        a misleading 'file not found'."""
        return self.client.locateFile(filename)

    def locate_files_strict(self, filenames: List[str]) -> Dict[str, List[Dict]]:
        """Batch locate without error swallowing: one HTTP round-trip for
        the whole list instead of one per file. Same record shape as
        locate_file_strict, keyed by filename."""
        return self.client.locateFiles(filenames)

    def metadata_for_files(self, filenames: List[str]) -> List[Dict]:
        """Batch metadata: one HTTP round-trip for the whole list instead
        of one per file. Files unknown to SAM are silently absent from
        the result (samweb behavior). Raises on SAM errors — callers
        wanting warn-and-continue chunk the list and fall back to
        get_metadata per file."""
        return self.client.getMultipleMetadata(filenames)

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


def list_files(query: str, summary: bool = False) -> List[str]:
    """List files matching a query."""
    return get_samweb_wrapper().list_files(query, summary)

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
