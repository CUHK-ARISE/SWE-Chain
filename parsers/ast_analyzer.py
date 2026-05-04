import ast
from pathlib import Path
from typing import List, Optional

from .node import NodeID


class PythonASTAnalyzer:
    def __init__(self, filepath: Path, display_path: str, file_content: Optional[str] = None):
        self.filepath = filepath
        self.display_path = display_path
        self.tree = None
        self.line_to_node = {}
        if file_content or filepath.exists():
            self.parse(file_content)
    
    
    def parse(self, file_content: Optional[str] = None):
        try:
            if file_content:
                content = file_content
            else:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
            self.tree = ast.parse(content, filename=str(self.filepath))
            self.build_line_mapping()
        except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
            pass
    
    
    def get_assign_target_name(self, node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                return node.target.id
        elif isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                return node.targets[0].id
        return None
    
    
    def build_line_mapping(self):
        if not self.tree:
            return
        
        # First pass: map FunctionDef, AsyncFunctionDef, ClassDef
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start_line = node.lineno
                end_line = node.end_lineno if hasattr(node, 'end_lineno') else start_line
                for line_num in range(start_line, end_line + 1):
                    self.line_to_node[line_num] = node
        
        # Second pass: module-level variable assignments (direct children of Module)
        for node in self.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                name = self.get_assign_target_name(node)
                if name:
                    start_line = node.lineno
                    end_line = node.end_lineno if hasattr(node, 'end_lineno') else start_line
                    for line_num in range(start_line, end_line + 1):
                        self.line_to_node[line_num] = node
        
        # Third pass: class-level attribute assignments (direct children of ClassDef, not inside methods)
        # These override the enclosing ClassDef mapping for their specific lines.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.Assign, ast.AnnAssign)):
                        name = self.get_assign_target_name(child)
                        if name:
                            start_line = child.lineno
                            end_line = child.end_lineno if hasattr(child, 'end_lineno') else start_line
                            for line_num in range(start_line, end_line + 1):
                                self.line_to_node[line_num] = child
    
    def find_containing_class(self, target_node: ast.AST) -> Optional[ast.ClassDef]:
        if not self.tree:
            return None
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                for child in ast.walk(node):
                    if child is target_node and child is not node:
                        return node
        return None
    
    
    def get_qualified_name(self, node: ast.AST) -> NodeID:
        if not node:
            return NodeID.module(self.display_path)
        parts = []
        kind = "unknown"
        
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            class_context = self.find_containing_class(node)
            if class_context:
                parts.append(class_context.name)
                kind = "async_method" if isinstance(node, ast.AsyncFunctionDef) else "method"
            else:
                kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            parts.append(node.name)
        
        elif isinstance(node, ast.ClassDef):
            class_context = self.find_containing_class(node)
            if class_context:
                parts.append(class_context.name)
            parts.append(node.name)
            kind = "class"
        
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            name = self.get_assign_target_name(node)
            if name:
                class_context = self.find_containing_class(node)
                if class_context:
                    parts.append(class_context.name)
                    parts.append(name)
                    kind = "attribute"
                else:
                    parts.append(name)
                    kind = "variable"
        
        if parts:
            anchor = "::".join(parts)
            return NodeID(path=self.display_path, anchor=anchor, kind=kind)
        
        return NodeID.module(self.display_path)
    
    
    def get_all_nodes_for_lines(self, line_numbers: List[int]) -> List[ast.AST]:
        seen_ids: set = set()
        nodes: List[ast.AST] = []
        for line_num in line_numbers:
            if line_num in self.line_to_node:
                node = self.line_to_node[line_num]
                node_id = id(node)
                if node_id not in seen_ids:
                    seen_ids.add(node_id)
                    nodes.append(node)
        return nodes
