#!/usr/bin/env python3
"""
Wrapper for samweb_client Python module
"""

import os
import sys
from typing import List, Dict, Optional, Union

from samweb_client import SAMWebClient #type: ignore

class SAMWebWrapper:
    """Wrapper for samweb_client to replace external samweb commands."""
    
    def __init__(self):
        """Initialize the SAMWeb client."""
        self.client = SAMWebClient()
    
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
    
    def locate_files(self, filenames: List[str]) -> Dict[str, str]:
        """Locate multiple files in batch (equivalent to samweb locate-files)."""
        try:
            return self.client.locateFiles(filenames)
        except Exception as e:
            print(f"Error locating files: {e}")
            return {}
    
    def create_definition(self, definition_name: str, query: str) -> bool:
        """Create a definition (equivalent to samweb create-definition)."""
        try:
            self.client.createDefinition(definition_name, query)
            return True
        except Exception as e:
            print(f"Error creating definition {definition_name}: {e}")
            return False
    
    def delete_definition(self, definition_name: str) -> bool:
        """Delete a definition (equivalent to samweb delete-definition)."""
        try:
            self.client.deleteDefinition(definition_name)
            return True
        except Exception as e:
            print(f"Error deleting definition {definition_name}: {e}")
            return False
    
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
            if availability:
                query = f"defname: {definition_name} with availability {availability}"
            else:
                query = f"defname: {definition_name}"
            return self.client.listFiles(query)
        except Exception as e:
            print(f"Error listing definition files for {definition_name}: {e}")
            return []
    
    def list_definitions(self, defname: str = None) -> List[str]:
        """List all definitions (equivalent to samweb list-definitions).
        
        Args:
            defname: Optional pattern to filter definitions (supports % wildcard)
        """
        try:
            if defname:
                result = self.client.listDefinitions(defname=defname)
            else:
                result = self.client.listDefinitions()
            
            # Convert filter object to list if needed
            if hasattr(result, '__iter__') and not isinstance(result, list):
                return list(result)
            return result
        except Exception as e:
            print(f"Error listing definitions: {e}")
            return []
    
    def get_metadata(self, filename: str) -> Dict:
        """Get metadata for a file (equivalent to samweb get-metadata)."""
        try:
            return self.client.getMetadata(filename)
        except Exception as e:
            print(f"Error getting metadata for {filename}: {e}")
            return {}
    
    def modify_metadata(self, filename: str, metadata: Dict) -> bool:
        """Modify metadata for a file (equivalent to samweb modify-metadata)."""
        try:
            self.client.modifyFileMetadata(filename, metadata)
            return True
        except Exception as e:
            print(f"Error modifying metadata for {filename}: {e}")
            return False
    
    def verify_file_checksum(self, filename: str) -> bool:
        """Verify file checksum (equivalent to samweb verify-file-checksum)."""
        try:
            return self.client.verifyFileChecksum(filename)
        except Exception as e:
            print(f"Error verifying checksum for {filename}: {e}")
            return False
    
    def add_file_location(self, filename: str, location: str) -> bool:
        """Add file location (equivalent to samweb add-file-location)."""
        try:
            self.client.addFileLocation(filename, location)
            return True
        except Exception as e:
            print(f"Error adding file location for {filename}: {e}")
            return False
    
    def remove_file_location(self, filename: str, location: str) -> bool:
        """Remove file location (equivalent to samweb remove-file-location)."""
        try:
            self.client.removeFileLocation(filename, location)
            return True
        except Exception as e:
            print(f"Error removing file location for {filename}: {e}")
            return False
    
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
    

# Global instance for easy access
_samweb_wrapper = None

def get_samweb_wrapper() -> SAMWebWrapper:
    """Get or create a global SAMWeb wrapper instance."""
    global _samweb_wrapper
    if _samweb_wrapper is None:
        _samweb_wrapper = SAMWebWrapper()
    return _samweb_wrapper

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

def create_definition(definition_name: str, query: str) -> bool:
    """Create a definition."""
    return get_samweb_wrapper().create_definition(definition_name, query)

def delete_definition(definition_name: str) -> bool:
    """Delete a definition."""
    return get_samweb_wrapper().delete_definition(definition_name)

def describe_definition(definition_name: str) -> str:
    """Describe a definition."""
    return get_samweb_wrapper().describe_definition(definition_name)

def list_definition_files(definition_name: str) -> List[str]:
    """List files in a definition."""
    return get_samweb_wrapper().list_definition_files(definition_name)

def list_definitions(defname: str = None) -> List[str]:
    """List all definitions.
    Args:
        defname: Optional pattern to filter definitions (supports % wildcard)
    """
    return get_samweb_wrapper().list_definitions(defname)

def get_metadata(filename: str) -> Dict:
    """Get metadata for a file."""
    return get_samweb_wrapper().get_metadata(filename)

def modify_metadata(filename: str, metadata: Dict) -> bool:
    """Modify metadata for a file."""
    return get_samweb_wrapper().modify_metadata(filename, metadata)

def verify_file_checksum(filename: str) -> bool:
    """Verify file checksum."""
    return get_samweb_wrapper().verify_file_checksum(filename)

def add_file_location(filename: str, location: str) -> bool:
    """Add file location."""
    return get_samweb_wrapper().add_file_location(filename, location)

def remove_file_location(filename: str, location: str) -> bool:
    """Remove file location."""
    return get_samweb_wrapper().remove_file_location(filename, location)

def file_lineage(filename: str, lineage_type: str = 'parents') -> List[str]:
    """Get file lineage using SAM client getFileLineage method."""
    return get_samweb_wrapper().file_lineage(filename, lineage_type)
