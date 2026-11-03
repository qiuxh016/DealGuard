"""Deterministic clause slicing that keeps citations tied to source offsets."""
from hashlib import sha256
import re


def policy_clauses(policy_id: str, version: str, source: str) -> list[dict[str, str | int]]:
    blocks: list[tuple[int, int]] = []
    block_start: int | None = None
    offset = 0
    for line in source.splitlines(keepends=True):
        if not line.strip():
            if block_start is not None:
                blocks.append((block_start, offset))
                block_start = None
        elif block_start is None:
            block_start = offset
        offset += len(line)
    if block_start is not None:
        blocks.append((block_start, len(source)))

    # Attach standalone Markdown headings to the following text block.
    merged: list[tuple[int, int]] = []
    index = 0
    while index < len(blocks):
        start, end = blocks[index]
        text = source[start:end].strip()
        if re.fullmatch(r"#{1,6}\s+.+", text):
            while index + 1 < len(blocks):
                next_start, next_end = blocks[index + 1]
                next_text = source[next_start:next_end].strip()
                end = next_end
                index += 1
                if not re.fullmatch(r"#{1,6}\s+.+", next_text):
                    break
        merged.append((start, end))
        index += 1

    clauses = []
    for start, end in merged:
        raw = source[start:end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw.rstrip())
        text_start, text_end = start + leading, start + trailing
        text = source[text_start:text_end]
        if not text:
            continue
        line_start = source.count("\n", 0, text_start) + 1
        line_end = source.count("\n", 0, max(text_start, text_end - 1)) + 1
        digest = sha256(f"{policy_id}@{version}#L{line_start}-L{line_end}\n{text}".encode("utf-8")).hexdigest()[:12]
        clauses.append({
            "chunk_id": digest,
            "citation_ref": f"{policy_id}@{version}#L{line_start}-L{line_end}",
            "line_start": line_start,
            "line_end": line_end,
            "text": text,
        })

    if not clauses and source:
        digest = sha256(f"{policy_id}@{version}#L1\n{source}".encode("utf-8")).hexdigest()[:12]
        clauses.append({"chunk_id": digest, "citation_ref": f"{policy_id}@{version}#L1", "line_start": 1,
                        "line_end": max(1, source.count("\n") + 1), "text": source})
    return clauses
