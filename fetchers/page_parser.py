import re
from bs4 import BeautifulSoup, NavigableString, Tag

def covert_html_to_md(html_content: str) -> str:
    """
    Convert a chunk of HTML (e.g., a <section> from Flask changelog)
    into Markdown.
    
    1. <ul>/<ol>/<li> will be converted to "- " lists (with indentation)
    2. <a> will be converted to [text](url)
    3. <code> will be converted to `code`
    4. <strong>/<b> will be converted to **text**
    5. <em>/<i> will be converted to *text*
    6. <h1>~<h6> will be converted to corresponding # headings
    7. Reasonable line breaks will be preserved as much as possible.
    """
    soup = BeautifulSoup(html_content, "lxml")
    
    def render_node(node, indent: int = 0) -> str:
        if isinstance(node, NavigableString):
            return str(node)
        
        if not isinstance(node, Tag):
            return ""
        
        name = node.name.lower()
        
        if name == "br":
            return "\n"
        
        if name == "p":
            content = "".join(render_node(c, indent) for c in node.children).strip()
            return content + "\n\n" if content else ""
        
        # For headings, convert to Markdown heading format. h{l} -> {'#' * l}
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(name[1])
            content = "".join(render_node(c, indent) for c in node.children).strip()
            return f"{'#' * level} {content}\n\n" if content else ""
        
        # For lists, convert to Markdown list format with proper indentation. Handle nested lists recursively.
        if name in ("ul", "ol"):
            items = []
            for li in node.find_all("li", recursive=False):
                li_text_parts = []
                for child in li.children:
                    if isinstance(child, Tag) and child.name in ("ul", "ol"):
                        # Keep nested lists as-is with their newline prefix
                        li_text_parts.append("\n" + render_node(child, indent + 1).rstrip())
                    else:
                        # Normalize whitespace only in non-nested-list content
                        part = render_node(child, indent)
                        part = re.sub(r'\s+', ' ', part)
                        li_text_parts.append(part)
                li_text = "".join(li_text_parts).strip()
                if li_text:
                    prefix = "  " * indent + "- "
                    items.append(prefix + li_text)
            return "\n".join(items) + "\n\n" if items else ""
        
        # For tables, convert to Markdown table format. Only handle simple tables without rowspan/colspan for now.
        if name == "table":
            rows = []
            for tr in node.find_all("tr"):
                cells = []
                is_header = False
                for cell in tr.find_all(["th", "td"]):
                    if cell.name == "th":
                        is_header = True
                    cell_text = "".join(render_node(c, indent) for c in cell.children).strip()
                    cell_text = cell_text.replace("|", "\\|").replace("\n", " ")
                    cells.append(cell_text)
                if cells:
                    rows.append((cells, is_header))
            if not rows:
                return ""
            col_count = max(len(r[0]) for r in rows)
            lines = []
            header_emitted = False
            for cells, is_header in rows:
                # Pad row to col_count
                cells += [""] * (col_count - len(cells))
                lines.append("| " + " | ".join(cells) + " |")
                if is_header and not header_emitted:
                    lines.append("| " + " | ".join(["---"] * col_count) + " |")
                    header_emitted = True
            # If no header row was found, add separator after first row
            if not header_emitted and lines:
                lines.insert(1, "| " + " | ".join(["---"] * col_count) + " |")
            return "\n".join(lines) + "\n\n"
        
        if name in ("thead", "tbody", "tfoot"):
            return "".join(render_node(c, indent) for c in node.children)
        
        # For hyperlinks: <a href="url">text</a> -> [text](url)
        if name == "a":
            href = (node.get("href") or "").strip()
            text = "".join(render_node(c, indent) for c in node.children).strip() or href
            if href:
                return f"[{text}]({href})"
            return text
        
        # For inline code: <code>text</code> -> `text`
        if name == "code":
            text = "".join(render_node(c, indent) for c in node.children)
            text = text.replace("`", "\\`")
            return f"`{text}`"
        
        # For bold/italic: <strong>/<b> -> **text**
        if name in ("strong", "b"):
            text = "".join(render_node(c, indent) for c in node.children)
            return f"**{text}**"
        
        # For italic: <em>/<i> -> *text*
        if name in ("em", "i"):
            text = "".join(render_node(c, indent) for c in node.children)
            return f"*{text}*"
        
        return "".join(render_node(c, indent) for c in node.children)
    
    md = "".join(render_node(child, 0) for child in soup.body.contents) if soup.body else \
         "".join(render_node(child, 0) for child in soup.contents)
    
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()
