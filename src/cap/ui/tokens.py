"""Proof design tokens -> CSS. `design/proof/tokens.json` is the copy of the Proof
design system's token file (DC-10); this module compiles it to `static/tokens.css`
in the same shape the design-system page generates, so the app and the design
reference read the same values. `cap tokens build` regenerates; a test refuses a
stale file.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOKENS_JSON = ROOT / "design" / "proof" / "tokens.json"
TOKENS_CSS = Path(__file__).resolve().parent / "static" / "tokens.css"


def _value(tok: dict, theme: str, first: str) -> str | None:
    v = tok["value"]
    if isinstance(v, str):
        return v if theme == first else None
    return v.get(theme) or (v.get(first) if theme == first else None)


def _theme_block(tokens: dict, theme: str, first: str) -> str:
    sel = f':root, [data-theme="{theme}"]' if theme == first else f'[data-theme="{theme}"]'
    lines = []
    for tok in tokens["color"]["tokens"]:
        v = _value(tok, theme, first)
        if v is None and theme == first:
            v = tok["value"] if isinstance(tok["value"], str) else tok["value"][first]
        if v is not None:
            lines.append(f"  --{tok['name']}: {v};")
    if theme != first:
        lines.append("  color-scheme: dark;")
    return sel + " {\n" + "\n".join(lines) + "\n}"


def _system_dark(tokens: dict, dark: str, first: str) -> str:
    """The system default: dark follows the OS unless the page sets data-theme."""
    lines = [
        f"    --{tok['name']}: {v};"
        for tok in tokens["color"]["tokens"]
        if (v := _value(tok, dark, first)) is not None
    ]
    lines.append("    color-scheme: dark;")
    return (
        "@media (prefers-color-scheme: dark) {\n"
        '  :root:not([data-theme="' + first + '"]) {\n' + "\n".join(lines) + "\n  }\n}"
    )


def _scalars(tokens: dict) -> str:
    """Spacing, radius and border tokens, and the font families."""
    scal = [
        f"  --{tok['name']}: {tok['value']};"
        for fam in ("spacing", "radius", "border")
        for tok in tokens.get(fam, {}).get("tokens", [])
    ]
    scal += [f"  --font-{key}: {stack};" for key, stack in tokens["type"]["families"].items()]
    return ":root {\n" + "\n".join(scal) + "\n}"


def _type_class(group: dict, st: dict) -> str:
    fam = st.get("family", group["family"])
    props = [
        f"font-family: var(--font-{fam})",
        f"font-size: {st['fontSize']}",
        f"line-height: {st['lineHeight']}",
        f"font-weight: {st['fontWeight']}",
    ]
    if st.get("letterSpacing"):
        props.append(f"letter-spacing: {st['letterSpacing']}")
    if st["name"] == "label":
        props.append("text-transform: uppercase")
    return f".{st['name']} {{ " + "; ".join(props) + "; }"


def compile_css(tokens: dict) -> str:
    themes = [t["id"] for t in tokens["color"]["themes"]]
    first = themes[0]
    out = [f"/* {tokens['name']} — generated from tokens.json; do not edit */"]
    out += [_theme_block(tokens, theme, first) for theme in themes]
    dark = [t for t in themes if t != first]
    if dark:
        out.append(_system_dark(tokens, dark[0], first))
    out.append(_scalars(tokens))
    out += [_type_class(g, st) for g in tokens["type"]["groups"] for st in g["styles"]]
    return "\n".join(out) + "\n"


def build(write: bool = True) -> str:
    css = compile_css(json.loads(TOKENS_JSON.read_text(encoding="utf-8")))
    if write:
        TOKENS_CSS.write_text(css, encoding="utf-8")
    return css


def is_current() -> bool:
    return TOKENS_CSS.exists() and TOKENS_CSS.read_text(encoding="utf-8") == build(write=False)
