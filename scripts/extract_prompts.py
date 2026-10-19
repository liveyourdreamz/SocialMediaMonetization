"""Extract the system prompts from the Agent Prompt Pack markdown into src/cap/prompts.

The Prompt Pack (claude/agent-prompt-pack-v1.1.1.md in the Project) is the master
for prompt text. This script turns it into one file per prompt plus an index with
model settings, so the code never carries a hand-edited copy that can drift.

Usage: python scripts/extract_prompts.py path/to/agent-prompt-pack-v1.1.1.md
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "cap" / "prompts" / "pack"

SECTION_RE = re.compile(r"^## (\d+)\. (P(\d+)) (.+?)(?: \(agent (\d+)[^)]*\))?$", re.M)
XML_RE = re.compile(r"```xml\n(.*?)```", re.S)


def model_settings(row: str) -> dict:
    """Parse the `| Model | ... |` row into model and effort values."""
    text = row.lower()
    models = []
    for name in ("opus", "sonnet", "haiku"):
        if name in text:
            models.append(f"claude-{name}-5-5")
    efforts = re.findall(r"effort[:\s`]*(low|medium|high|xhigh|max)", text)
    return {"models": models, "efforts": sorted(set(efforts)), "raw": row.strip()}


def main(path: str) -> None:
    md = Path(path).read_text(encoding="utf-8")
    version = re.search(r"RCICE v(\d+\.\d+(?:\.\d+)?)", md)
    pack_version = version.group(1) if version else "unknown"

    # shared blocks: first xml block in section 2
    shared_sec = md.split("## 2. ", 1)[1].split("## 3. ", 1)[0]
    shared = XML_RE.search(shared_sec).group(1).strip()
    OUT.mkdir(parents=True, exist_ok=True)
    # one file per shared block so the registry can choose which a call receives
    for tag in ("shared_context", "untrusted_text_rule", "fail_closed_rule"):
        m_tag = re.search(rf"<{tag}[^>]*>.*?</{tag}>", shared, re.S)
        if not m_tag:
            raise SystemExit(f"shared block <{tag}> not found in the pack")
        (OUT / f"shared_{tag}.xml").write_text(m_tag.group(0) + "\n", encoding="utf-8")

    index: dict[str, dict] = {"pack_version": pack_version, "prompts": {}}
    matches = list(SECTION_RE.finditer(md))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else md.find("## 17.")
        body = md[start:end]
        pid, title, agent = m.group(2), m.group(4).strip(), m.group(5)
        blocks = XML_RE.findall(body)
        model_row = next((ln for ln in body.splitlines() if ln.startswith("| Model |")), "")
        files = []
        for j, block in enumerate(blocks):
            suffix = "" if len(blocks) == 1 else f"_call{'AB' if j == 0 else 'C'}"
            fname = f"{pid}{suffix}.xml"
            (OUT / fname).write_text(block.strip() + "\n", encoding="utf-8")
            files.append(fname)
        index["prompts"][pid] = {
            "title": title,
            "agent": int(agent) if agent else None,
            "files": files,
            "settings": model_settings(model_row),
        }
    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(index['prompts'])} prompts (pack v{pack_version}) to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
