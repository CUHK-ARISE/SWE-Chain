from __future__ import annotations
from typing import Optional

from .node import NodeID


class TestNodeID(NodeID):
    """Handles IDs like 'tests/test_foo.py::test_bar[/parent-/child-None-None]'"""
    params: Optional[str] = None

    @classmethod
    def parse(cls, s: str) -> TestNodeID:
        s = s.replace("::()", "")
        if "::" not in s:
            return cls(path=s, anchor="<file>")
        path, anchor = s.split("::", 1)
        
        # Extract [params] from the last anchor part
        params = None
        bracket_idx = anchor.find("[")
        if bracket_idx != -1 and anchor.endswith("]"):
            params = anchor[bracket_idx + 1 : -1]
            anchor = anchor[:bracket_idx]
        return cls(path=path, anchor=anchor, params=params)
    
    @property
    def base_id(self) -> str:
        """Node ID without params — for cross-domain comparison with diff nodes."""
        return f"{self.path}::{self.anchor}"
    
    def __str__(self) -> str:
        if self.params is not None:
            return f"{self.path}::{self.anchor}[{self.params}]"
        return f"{self.path}::{self.anchor}"
    
    def __repr__(self) -> str:
        return f"TestNodeID('{self}')"
