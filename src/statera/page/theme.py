"""The look of the catalog page: an institution's own name, logo, colors, fonts and CSS, chosen when the
page is made.

    statera docs --page --theme governance/themes/intranet     # this theme
    statera docs --page                                        # the repository's own ([page] theme), if any
    statera docs --page --theme default                        # the look the framework comes with

A theme is a FOLDER, so it is reviewed and versioned like the rest of the repository:

    <theme>/
    ├── theme.toml    what is said and how it looks (every key is optional)
    ├── logo.svg      the images theme.toml names (svg, png, jpg, webp, gif; .ico for the favicon)
    └── theme.css     optional: CSS added after the page's own, for what the keys below do not reach

    name   = "Data catalog"                # shown instead of the repository's name
    notice = "Internal use"                # a strip above every page
    footer = "Data team · data@example.org"
    mode   = "auto"                        # auto (follows the reader's system) | light | dark
    favicon = "favicon.png"

    [logo]
    file = "logo.svg"       # dark = "logo-white.svg" for the dark mode; alt = "…"; height = 32 (px)
    alone = false           # true: the logo already says the name, do not write it next to it

    [colors]                # any of: background surface sidebar text muted border accent accent_soft
    accent = "#0b4f8a"      #         header header_text code_background warning warning_soft
    [colors.dark]           # the same keys, for the dark mode
    accent = "#8fbce8"

    [fonts]
    text = "'Segoe UI', Arial, sans-serif"
    code = "Consolas, monospace"

    [home]                  # the first page, in the institution's words
    title = "…"
    lead = "…"

    [[links]]               # in the bar on top: back to the intranet, who to ask
    label = "Intranet"
    url = "https://intranet.example.org"

Everything goes INSIDE the page (the images as data, the CSS as text): the page stays one file that fetches
nothing, which is what lets it be dropped on an intranet or opened from a disk. What a theme writes ends up
in a page other people open, so it is checked here: an unknown key, a color or a font that is more than a
color or a font, a link that is not an address, or CSS that would close its own block, is refused with the
key it came from.
"""

from __future__ import annotations

import base64
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from statera.project import Project

DEFAULT = "default"  # `--theme default`: the framework's own look, whatever the repository declares
# key in theme.toml -> the page's CSS variable
COLORS = {
    "background": "--bg",
    "surface": "--panel",
    "sidebar": "--side",
    "text": "--ink",
    "muted": "--soft",
    "border": "--line",
    "accent": "--accent",
    "accent_soft": "--accent-soft",
    "header": "--top",
    "header_text": "--top-ink",
    "code_background": "--code-bg",
    "warning": "--warn",
    "warning_soft": "--warn-soft",
}
FONTS = {"text": "--font", "code": "--mono"}
BACKGROUND = {"light": "#fbfaf7", "dark": "#15191c"}  # the page's own (page.html), to derive accent_soft from
IMAGES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".webp": "image/webp", ".gif": "image/gif", ".ico": "image/x-icon"}  # fmt: skip
MODES = ("auto", "light", "dark")
MAX_IMAGE = 512 * 1024  # bytes: the image goes inside every copy of the page
COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|[a-zA-Z]+|(rgb|rgba|hsl|hsla|oklch|oklab|lab|lch)\([0-9a-zA-Z.,%/\s+-]+\))$")
FONT = re.compile(r"^[\w\s,.'\"-]+$")
LINK = re.compile(r"^(https?://|mailto:|/|\./|\.\./|#)[^\s<>\"']*$")
KEYS = {
    "": {"name", "notice", "footer", "mode", "favicon", "logo", "colors", "fonts", "home", "links"},
    "logo": {"file", "dark", "alt", "height", "alone"},
    "colors": {*COLORS, "dark"},
    "colors.dark": set(COLORS),
    "fonts": set(FONTS),
    "home": {"title", "lead"},
    "links": {"label", "url"},
}


class ThemeError(ValueError):
    """A theme that cannot be used as it is: the message names the file and the key."""


@dataclass
class Theme:
    """What the page needs of a theme: `data` travels with the catalog (the script draws the logo, the
    notice, the links), `css` goes after the page's own, `mode` and `favicon` go in its head."""

    folder: Path | None = None
    data: dict = field(default_factory=dict)
    css: str = ""
    mode: str = "auto"
    favicon: str | None = None


def find(project: Project, chosen: str | None = None) -> Path | None:
    """The folder of the theme to use: the one given (`--theme`: a path from here or from the repository's
    content folder), else the repository's own (`[page] theme` in statera.toml). None: the framework's look."""
    declared = project.config.get("page", {}).get("theme")
    name = chosen or declared
    if not name or name == DEFAULT:
        return None
    candidates = [Path(name).expanduser(), project.root / name] if chosen else [project.root / name]
    for folder in candidates:
        if folder.is_dir():
            return folder.resolve()
    where = "--theme" if chosen else "statera.toml [page] theme"
    raise ThemeError(f"{where}: no theme folder at {' or '.join(str(c) for c in candidates)}")


def _known(table: dict, section: str, file: Path) -> None:
    if not isinstance(table, dict):
        raise ThemeError(f"{file}: [{section}] must be a table")
    if unknown := sorted(set(table) - KEYS[section]):
        where = f"[{section}] " if section else ""
        raise ThemeError(
            f"{file}: {where}unknown key(s) {', '.join(unknown)}; accepted: {', '.join(sorted(KEYS[section]))}"
        )


def _text(table: dict, key: str, where: str, file: Path, limit: int = 400) -> str:
    value = table.get(key, "")
    if not isinstance(value, str) or len(value) > limit:
        raise ThemeError(f"{file}: {where}{key} must be text of up to {limit} characters")
    return " ".join(value.split())


def _image(folder: Path, name, key: str, file: Path) -> str:
    """The image as a data: address, to live inside the page."""
    path = (folder / str(name)).resolve()
    if not isinstance(name, str) or folder.resolve() not in path.parents:
        raise ThemeError(f"{file}: {key} must name a file inside the theme's folder")
    if path.suffix.lower() not in IMAGES:
        raise ThemeError(f"{file}: {key} = {name!r} is not an image the page takes ({', '.join(sorted(IMAGES))})")
    if not path.is_file():
        raise ThemeError(f"{file}: {key} = {name!r}: there is no such file in {folder}")
    content = path.read_bytes()
    if len(content) > MAX_IMAGE:
        raise ThemeError(
            f"{file}: {key} = {name!r} has {len(content) // 1024} kB; the page takes up to {MAX_IMAGE // 1024} kB"
        )
    return f"data:{IMAGES[path.suffix.lower()]};base64,{base64.b64encode(content).decode('ascii')}"


def _mixed(color: str, background: str, share: float = 0.14) -> str | None:
    """`color` laid thin over `background`: the tint behind what is selected. None unless both are #hex."""

    def rgb(value: str) -> tuple[int, int, int] | None:
        h = value.lstrip("#")
        if not re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value):
            return None
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    a, b = rgb(color), rgb(background)
    if a is None or b is None:
        return None
    return "#" + "".join(f"{round(x * share + y * (1 - share)):02x}" for x, y in zip(a, b, strict=True))


def _variables(colors: dict, fonts: dict, scheme: str, where: str, file: Path) -> list[str]:
    out = []
    for key, value in colors.items():
        if not isinstance(value, str) or not COLOR.match(value.strip()):
            raise ThemeError(f'{file}: {where}{key} = {value!r} is not a color (e.g. "#0b4f8a" or "rgb(11 79 138)")')
        out.append(f"{COLORS[key]}: {value.strip()};")
    if "accent" in colors and "accent_soft" not in colors:
        soft = _mixed(colors["accent"].strip(), str(colors.get("background", BACKGROUND[scheme])).strip())
        out.append(f"--accent-soft: {soft or 'color-mix(in srgb, var(--accent) 14%, var(--bg))'};")
    for key, value in fonts.items():
        if not isinstance(value, str) or not FONT.match(value):
            raise ThemeError(f"{file}: [fonts] {key} = {value!r} is not a list of font names")
        out.append(f"{FONTS[key]}: {value.strip()};")
    return out


def _css(config: dict, folder: Path, file: Path) -> str:
    """The theme's colors and fonts as the page's variables (light, and dark both when the reader's system
    asks for it and when the theme fixes it), its logo's height, then its own theme.css."""
    colors, fonts = dict(config.get("colors", {})), config.get("fonts", {})
    dark = colors.pop("dark", {})
    _known(dark, "colors.dark", file)
    light = _variables(colors, fonts, "light", "[colors] ", file)
    night = _variables(dark, {}, "dark", "[colors.dark] ", file)
    if height := config.get("logo", {}).get("height"):
        if not isinstance(height, int) or not 12 <= height <= 120:
            raise ThemeError(f"{file}: [logo] height must be a whole number of pixels, from 12 to 120")
        light.append(f"--logo-h: {height}px;")
    parts = []
    if light:
        parts.append(":root { " + " ".join(light) + " }")
    if night:
        body = " ".join(night)
        parts.append(f'@media (prefers-color-scheme: dark) {{ :root:not([data-mode="light"]) {{ {body} }} }}')
        parts.append(f':root[data-mode="dark"] {{ {body} }}')
    own = folder / "theme.css"
    if own.is_file():
        text = own.read_text(encoding="utf-8")
        if re.search(r"</\s*style", text, flags=re.I):
            raise ThemeError(f"{own}: a stylesheet cannot contain </style> (it would end the block it is put in)")
        parts.append(f"/* {own.name} */\n{text.strip()}")
    return "\n".join(parts)


def load(folder: Path | None) -> Theme:
    """The theme of a folder, checked. None: the framework's look."""
    if folder is None:
        return Theme()
    file = folder / "theme.toml"
    try:
        config = tomllib.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
    except tomllib.TOMLDecodeError as error:
        raise ThemeError(f"{file}: {error}") from error
    if not file.is_file() and not (folder / "theme.css").is_file():
        raise ThemeError(f"{folder} is not a theme: it has neither theme.toml nor theme.css")
    _known(config, "", file)
    for section in ("logo", "colors", "fonts", "home"):
        _known(config.get(section, {}), section, file)
    mode = config.get("mode", "auto")
    if mode not in MODES:
        raise ThemeError(f"{file}: mode = {mode!r}; accepted: {', '.join(MODES)}")

    data: dict = {k: _text(config, k, "", file) for k in ("name", "notice", "footer") if config.get(k)}
    if home := config.get("home"):
        data["home"] = {k: _text(home, k, "[home] ", file, limit=1200) for k in home}
    if logo := config.get("logo"):
        if "file" not in logo:
            raise ThemeError(f"{file}: [logo] needs file (the image, in the theme's folder)")
        if not isinstance(logo.get("alone", False), bool):
            raise ThemeError(f"{file}: [logo] alone must be true or false")
        data["logo"] = {
            "src": _image(folder, logo["file"], "[logo] file", file),
            "dark": _image(folder, logo["dark"], "[logo] dark", file) if "dark" in logo else None,
            "alt": _text(logo, "alt", "[logo] ", file) or data.get("name", ""),
            "alone": logo.get("alone", False),
        }
    links = config.get("links", [])
    if not isinstance(links, list):
        raise ThemeError(f"{file}: links must be a list of tables ([[links]])")
    for link in links:
        _known(link, "links", file)
        label, url = _text(link, "label", "[[links]] ", file, limit=60), link.get("url", "")
        if not label or not isinstance(url, str) or not LINK.match(url):
            raise ThemeError(
                f"{file}: [[links]] needs a label and a url that is an address "
                f"(https://…, mailto:…, or a path of the same site); got label={label!r}, url={url!r}"
            )
        data.setdefault("links", []).append({"label": label, "url": url})
    return Theme(
        folder=folder,
        data=data,
        css=_css(config, folder, file),
        mode=mode,
        favicon=_image(folder, config["favicon"], "favicon", file) if "favicon" in config else None,
    )


def of(project: Project, chosen: str | None = None) -> Theme:
    """The theme the page of this repository is made with (see `find`)."""
    return load(find(project, chosen))
