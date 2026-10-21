from __future__ import annotations

"""Publishes the digest as a Notion page through the public API (NOTION_TOKEN + NOTION_PARENT_PAGE_ID)."""
import os
import re
from pathlib import Path

import requests

API = "https://api.notion.com/v1"
NUMBERED = re.compile(r"^\d+\. ")
CONFIG = Path(__file__).resolve().parent.parent / "publish.yaml"
INLINE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`")


def rich_text(text: str) -> list[dict]:
    out, pos = [], 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            out.append({"type": "text", "text": {"content": text[pos:m.start()]}})
        if m.group(1):
            out.append({"type": "text", "text": {"content": m.group(1), "link": {"url": m.group(2)}}})
        elif m.group(3):
            out.append({"type": "text", "text": {"content": m.group(3)}, "annotations": {"bold": True}})
        else:
            out.append({"type": "text", "text": {"content": m.group(4)}, "annotations": {"code": True}})
        pos = m.end()
    if pos < len(text):
        out.append({"type": "text", "text": {"content": text[pos:]}})
    return [r for r in out if r["text"]["content"]][:100]


def _block(kind: str, text: str) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(text)}}


def md_to_blocks(md: str) -> list[dict]:
    blocks, table = [], []

    def flush_table():
        if not table:
            return
        width = len(table[0])
        blocks.append({"object": "block", "type": "table", "table": {
            "table_width": width, "has_column_header": True, "has_row_header": False,
            "children": [{"type": "table_row", "table_row": {"cells": [rich_text(c) for c in row[:width]]}}
                         for row in table]}})
        table.clear()

    for line in md.splitlines()[1:]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(set(c) <= {"-"} for c in cells):
                table.append(cells)
            continue
        flush_table()
        if not line.strip():
            continue
        if line.startswith("## "):
            blocks.append(_block("heading_2", line[3:]))
        elif line.startswith("### "):
            blocks.append(_block("heading_3", line[4:]))
        elif line.strip() == "---":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif NUMBERED.match(line):
            blocks.append(_block("numbered_list_item", NUMBERED.sub("", line, count=1)))
        elif line.startswith("- "):
            blocks.append(_block("bulleted_list_item", line[2:]))
        else:
            blocks.append(_block("paragraph", line.strip("_")))
    flush_table()
    return blocks


def parent() -> str | None:
    if os.environ.get("NOTION_PARENT_PAGE_ID"):
        return os.environ["NOTION_PARENT_PAGE_ID"]
    if CONFIG.exists():
        import yaml

        return (yaml.safe_load(CONFIG.read_text()) or {}).get("notion_parent_page_id") or None
    return None


def publish(md: str, title: str, parent_id: str | None = None) -> str:
    token = os.environ.get("NOTION_TOKEN")
    parent_id = parent_id or parent()
    if not token or not parent_id:
        raise RuntimeError("--notion needs NOTION_TOKEN and NOTION_PARENT_PAGE_ID (or --notion-parent)")
    h = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}
    blocks = md_to_blocks(md)
    r = requests.post(f"{API}/pages", headers=h, timeout=60, json={
        "parent": {"page_id": parent_id},
        "properties": {"title": {"title": [{"text": {"content": title}}]}},
        "children": blocks[:100]})
    r.raise_for_status()
    page = r.json()
    for i in range(100, len(blocks), 100):
        requests.patch(f"{API}/blocks/{page['id']}/children", headers=h, timeout=60,
                       json={"children": blocks[i:i + 100]}).raise_for_status()
    return page["url"]
