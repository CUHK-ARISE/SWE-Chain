from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

NODE_KIND = Literal["function", "async_function", "method", "async_method", "class", "attribute", "variable", "module", "file", "unknown"]

class NodeID(BaseModel):
    """
    Anchors:
        - `<module>`: module-level code
        - `<file>`: non-Python files (no AST)
        - `ClassName`: class definitions
        - `function_name`: top-level functions
        - `ClassName::method_name`: methods inside a class
        - `ClassName::attr_name`: class attributes
        - `var_name`: module-level variables
    """
    path: str = Field(description="File path (relative to repo root, without 'a/' or 'b/' prefix)")
    anchor: str = Field(description="Anchor within the file (e.g., <module>, <file>, ClassName, function_name, ClassName::method_name)")
    kind: NODE_KIND = Field(default="unknown", description="Node type: function, async_function, method, async_method, class, attribute, variable, module, file, unknown")
    
    @classmethod
    def parse(cls, s: str) -> NodeID:
        if "::" in s:
            path, anchor = s.split("::", 1)
            return cls(path=path, anchor=anchor)
        return cls(path=s, anchor="<file>")
    
    @classmethod
    def module(cls, path: str) -> NodeID:
        return cls(path=path, anchor="<module>", kind="module")
    
    @classmethod
    def file(cls, path: str) -> NodeID:
        return cls(path=path, anchor="<file>", kind="file")
    
    def to_dict(self) -> dict:
        return {"kind": self.kind, "node": str(self)}

    def __str__(self) -> str:
        return f"{self.path}::{self.anchor}"

    def __repr__(self) -> str:
        return f"NodeID('{self}')"
