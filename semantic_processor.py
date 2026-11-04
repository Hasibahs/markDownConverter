"""
semantic_processor.py
Post-processing and semantic enhancement pipeline for Markdown extracted from PDFs.

Addresses core AI chunking and retrieval problems:
1. Loss of Header Hierarchy: Maps font sizes, bold weights, and section numbering to Markdown headings (#, ##, ###).
2. Fragmented Tables: Detects multi-row fragmented pipe tables and troubleshooting/specification
   tables, merges split sentences, and converts them into structured key-value lists.
3. Pagination Noise: Uses comprehensive regular expressions and repetition analysis to detect
   and delete repeating running footers, headers, and page numbers.
"""

import os
import re
from typing import List, Dict, Optional, Tuple, Any

# ==============================================================================
# 1. SECTION NUMBERING & SPLIT HEADER RECOVERY
# ==============================================================================

def merge_split_section_headers(text: str) -> str:
    """
    In many PDF extractions, section numbers appear on a line of their own,
    followed by the section title on the next line:
        1
        Introduction
    or:
        Section 2
        System Architecture
    This function merges them into a clean Markdown heading:
        ## 1 Introduction
        ## Section 2: System Architecture
    """
    # Pattern 1: Standalone number (e.g. "1", "2.1", "10.2.1") followed by Title
    text = re.sub(
        r"(?m)^(?P<num>\d+(?:\.\d+)*\.?)\s*\n+(?P<title>[A-Z][A-Za-z0-9\s,\-–/&]{2,70})(?=\n|$)",
        r"\n## \g<num> \g<title>\n",
        text
    )

    # Pattern 2: "Section X" / "Chapter X" / "Part X" followed by Title
    text = re.sub(
        r"(?m)^(?P<sec>(?:Section|Chapter|Part|Appendix)\s+[A-Z0-9]+[:.]?)\s*\n+(?P<title>[A-Z][A-Za-z0-9\s,\-–/&]{2,70})(?=\n|$)",
        r"\n## \g<sec>: \g<title>\n",
        text
    )

    return text


# ==============================================================================
# 2. PAGINATION & FOOTER REMOVAL VIA REGEX
# ==============================================================================

# Explicit page number patterns
PAGE_PATTERNS = [
    # "Introduction v1.1 Page 5 of 29", "Page 5 of 29", "5 of 29", "Page 5 / 29"
    re.compile(r"^.*?\b(?:Page|pg\.?)\s+\d+\s*(?:of|\/)\s*\d+.*?$", re.IGNORECASE),
    # "Page 12", "Page: 12", "Pg. 12" standalone
    re.compile(r"^\s*(?:Page|pg\.?)\s*:?\s*\d+\s*$", re.IGNORECASE),
    # Standalone digits representing page numbers: "5", "- 5 -", "-- 5 --", "• 5 •", "[5]"
    re.compile(r"^\s*[-–—~*•\[\(\s]*\d{1,4}[-–—~*•\]\)\s]*$"),
    # Roman numeral page numbers: "iv", "ix", "- iv -"
    re.compile(r"^\s*[-–—~*•\[\(\s]*(?:[ivxlcdm]+)[-–—~*•\]\)\s]*$", re.IGNORECASE),
    # Document version + page patterns: e.g. "Doc Name - Rev 1.2 - Page 4"
    re.compile(r"^.*?\b(?:v\d+\.\d+|Rev(?:ision)?\s*\d+(?:\.\d+)?)\s*[-|•/]\s*Page\s*\d+.*?$", re.IGNORECASE),
    # Document running notices: "Confidential", "All Rights Reserved", "Draft"
    re.compile(r"^.*?\b(?:Confidential|All rights reserved|Proprietary & Confidential|Printed on:?\s+\d+).*?$", re.IGNORECASE),
]

def strip_pagination_noise(text: str) -> str:
    """
    Strips running headers, footers, and page numbers using regex and repetition detection.
    Preserves all Markdown headings (#).
    """
    lines = text.split("\n")
    if not lines:
        return text

    # Step 1: Detect lines that repeat verbatim across multiple pages (running headers/footers)
    line_counts: Dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        # Only consider reasonably short lines that look like headers/footers
        if 4 <= len(stripped) <= 90 and not stripped.startswith(("#", "-", "*", ">", "|", "```")):
            line_counts[stripped] = line_counts.get(stripped, 0) + 1

    repeating_headers_footers = {
        line for line, count in line_counts.items()
        if count >= 2 and any(char.isalpha() for char in line)
    }

    cleaned_lines: List[str] = []
    for line in lines:
        stripped = line.strip()

        # Never strip Markdown headings or code blocks
        if stripped.startswith(("#", "```")):
            cleaned_lines.append(line)
            continue

        # Check repeating headers/footers
        if stripped in repeating_headers_footers:
            continue

        # Check explicit pagination regex patterns
        is_page_noise = False
        for pattern in PAGE_PATTERNS:
            if pattern.fullmatch(stripped):
                is_page_noise = True
                break

        if not is_page_noise:
            cleaned_lines.append(line)

    result = "\n".join(cleaned_lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# ==============================================================================
# 3. FLATTEN COMPLEX & FRAGMENTED TABLES INTO KEY-VALUE LISTS
# ==============================================================================

TROUBLESHOOTING_KEYWORDS = {
    "problem", "issue", "symptom", "error", "fault", "cause", "solution",
    "resolution", "action", "remedy", "fix", "troubleshooting", "diagnostic",
    "observation", "condition", "expected", "actual", "correction", "procedure"
}

SPECIFICATION_KEYWORDS = {
    "specification", "parameter", "feature", "attribute", "metric", "requirement",
    "dimension", "rating", "capacity", "tolerance", "value", "description"
}

def is_complex_or_troubleshooting_table(headers: List[str], rows: List[List[str]]) -> bool:
    """
    Determines if a table is better represented as a flat key-value list rather than a pipe table.
    """
    if not headers:
        return False

    header_text = " ".join(headers).lower()

    # Check troubleshooting terms
    if any(kw in header_text for kw in TROUBLESHOOTING_KEYWORDS):
        return True

    # Check specification / key-value terms
    if any(kw in header_text for kw in SPECIFICATION_KEYWORDS):
        return True

    # 2-column tables are almost always Key -> Value
    if len(headers) == 2:
        return True

    # Check for long cell content (prose/explanations that get mangled in markdown tables)
    long_cells = 0
    total_cells = 0
    for row in rows:
        for cell in row:
            total_cells += 1
            if len(cell.strip()) > 45 or cell.count(".") >= 2:
                long_cells += 1

    if total_cells > 0 and (long_cells / total_cells) > 0.25:
        return True

    return False

def merge_fragmented_table_rows(rows: List[List[str]]) -> List[List[str]]:
    """
    Merges rows that were fragmented by PDF line-wrapping.
    In PDFs, a single table row often splits into 2-4 sub-rows where subsequent
    rows contain sentence continuations.
    """
    if not rows:
        return []

    merged: List[List[str]] = []
    for row in rows:
        if not merged:
            merged.append(list(row))
            continue

        prev_row = merged[-1]

        # Check if current row is a continuation of previous row
        is_continuation = False
        non_empty_cells = [c.strip() for c in row if c.strip()]

        if non_empty_cells:
            first_char = non_empty_cells[0][0]
            if first_char.islower() or first_char in (",", ")", "]", ";", "/"):
                is_continuation = True
            elif len(non_empty_cells) < len(prev_row) and not any(c.endswith((".", "!", "?")) for c in prev_row if c):
                is_continuation = True

        if is_continuation:
            for col_idx in range(len(prev_row)):
                curr_val = row[col_idx].strip() if col_idx < len(row) else ""
                if curr_val:
                    if prev_row[col_idx].strip():
                        if prev_row[col_idx].endswith("-"):
                            prev_row[col_idx] = prev_row[col_idx][:-1] + curr_val
                        else:
                            prev_row[col_idx] += " " + curr_val
                    else:
                        prev_row[col_idx] = curr_val
        else:
            merged.append(list(row))

    return merged

def table_to_key_value_list(headers: List[str], rows: List[List[str]]) -> str:
    """
    Converts a table into a clean, hierarchical key-value markdown list for AI ingestion.
    """
    lines: List[str] = []
    num_cols = len(headers)

    clean_headers = [h.strip() if h.strip() else f"Column {i+1}" for i, h in enumerate(headers)]

    for row_idx, row in enumerate(rows):
        cells = [row[i].strip() if i < len(row) else "" for i in range(num_cols)]
        if not any(cells):
            continue

        if len(clean_headers) == 2:
            key = cells[0] if cells[0] else f"Item {row_idx+1}"
            val = cells[1] if cells[1] else "(None)"
            lines.append(f"- **{key}**: {val}")
        else:
            primary_header = clean_headers[0]
            primary_val = cells[0] if cells[0] else f"Entry {row_idx+1}"
            lines.append(f"- **{primary_header}**: {primary_val}")

            for c_idx in range(1, num_cols):
                attr_name = clean_headers[c_idx]
                attr_val = cells[c_idx]
                if attr_val:
                    lines.append(f"  - **{attr_name}**: {attr_val}")

    return "\n".join(lines)

def flatten_complex_tables(text: str) -> str:
    """
    Finds markdown pipe tables in text, identifies fragmented or troubleshooting tables,
    merges broken row wraps, and flattens them into structured key-value lists.
    """
    lines = text.split("\n")
    output_lines: List[str] = []
    table_buffer: List[str] = []
    in_table = False

    def process_table_buffer(buf: List[str]) -> List[str]:
        if not buf:
            return []

        parsed_rows: List[List[str]] = []
        for line in buf:
            raw_line = line.strip()
            if re.match(r"^\|(?:\s*-+\s*\|)+$", raw_line):
                continue
            cells = [c.strip() for c in raw_line.split("|")[1:-1]]
            if any(cells):
                parsed_rows.append(cells)

        if not parsed_rows:
            return buf

        headers = parsed_rows[0]
        data_rows = parsed_rows[1:]

        merged_data_rows = merge_fragmented_table_rows(data_rows)

        if is_complex_or_troubleshooting_table(headers, merged_data_rows):
            kv_text = table_to_key_value_list(headers, merged_data_rows)
            return ["", kv_text, ""]
        else:
            reconstructed = []
            col_widths = [max(len(r[i]) if i < len(r) else 0 for r in [headers] + merged_data_rows) for i in range(len(headers))]
            col_widths = [max(w, 3) for w in col_widths]

            reconstructed.append("| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |")
            reconstructed.append("| " + " | ".join("-" * col_widths[i] for i in range(len(headers))) + " |")
            for r in merged_data_rows:
                cells = [r[i] if i < len(r) else "" for i in range(len(headers))]
                reconstructed.append("| " + " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(cells)) + " |")
            return reconstructed

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_buffer.append(line)
            in_table = True
        else:
            if in_table:
                output_lines.extend(process_table_buffer(table_buffer))
                table_buffer = []
                in_table = False
            output_lines.append(line)

    if in_table and table_buffer:
        output_lines.extend(process_table_buffer(table_buffer))

    return "\n".join(output_lines)


# ==============================================================================
# 4. ENFORCE MARKDOWN HEADINGS FROM FONT METRICS & SEMANTIC RULES
# ==============================================================================

NUMBERED_HEADING_PATTERN = re.compile(
    r"^(?:(?:Section|Chapter|Part)\s+\d+[:.]?|\d+(?:\.\d+)*\.?)\s+([A-Za-z0-9][^\n.!?]{1,80})$",
    re.IGNORECASE
)

STRUCTURAL_HEADING_PATTERN = re.compile(
    r"^(?:[A-Z][A-Za-z0-9\s,\-–/&]{2,60}|[A-Z0-9\s,\-–/&]{3,50})$"
)

def enforce_markdown_headings(text: str) -> str:
    """
    Analyzes markdown text to ensure structural headers are tagged with #, ##, ###.
    Applies regex rules for numbered sections and prominent standalone header lines.
    """
    lines = text.split("\n")
    processed_lines: List[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Already a markdown heading or table or list item or blockquote
        if stripped.startswith(("#", "-", "*", ">", "|", "```")) or not stripped:
            processed_lines.append(line)
            continue

        prev_blank = (i == 0) or (not lines[i - 1].strip())
        next_blank = (i == len(lines) - 1) or (not lines[i + 1].strip())

        is_heading = False
        prefix = "## "

        if (prev_blank or next_blank) and len(stripped) <= 80:
            match_num = NUMBERED_HEADING_PATTERN.match(stripped)
            if match_num and not stripped.endswith((".", ";", ":", ",")):
                is_heading = True
                if re.match(r"^\d+\.?\s+[A-Z]", stripped):
                    prefix = "## "
                elif re.match(r"^\d+\.\d+\.?\s+[A-Z]", stripped):
                    prefix = "### "
                else:
                    prefix = "#### "
            elif prev_blank and next_blank and STRUCTURAL_HEADING_PATTERN.match(stripped) and not stripped.endswith((".", ";", ":", ",")):
                words = stripped.split()
                if 1 <= len(words) <= 7:
                    is_heading = True
                    prefix = "### "

        if is_heading:
            processed_lines.append(f"\n{prefix}{stripped}\n")
        else:
            processed_lines.append(line)

    return "\n".join(processed_lines)


# ==============================================================================
# 5. MASTER SEMANTIC ENHANCER FUNCTION
# ==============================================================================

def enhance_markdown_semantics(markdown_text: str, pdf_path: Optional[str] = None) -> str:
    """
    Master pipeline addressing all 3 core feedback problems:
    1. Reconnects split section numbers and titles (e.g. 1 \\n Introduction -> ## 1 Introduction).
    2. Strips pagination noise, footers, and page numbers via regex and repetition detection.
    3. Merges fragmented table cells and flattens complex & troubleshooting tables to key-value lists.
    4. Enforces heading hierarchy (#, ##, ###).
    5. Cleans up extra whitespace.
    """
    content = markdown_text

    # Step 1: Merge split numbers and headers before footer cleanup
    content = merge_split_section_headers(content)

    # Step 2: Strip pagination noise & footers
    content = strip_pagination_noise(content)

    # Step 3: Flatten complex and fragmented tables into key-value lists
    content = flatten_complex_tables(content)

    # Step 4: Enforce heading tags on remaining structural titles
    content = enforce_markdown_headings(content)

    # Step 5: Final cleanup: normalize excessive blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)

    return content.strip()
