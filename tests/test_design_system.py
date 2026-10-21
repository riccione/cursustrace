"""Tests for the webapp design system: color tokens and their contrast."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cursustrace.web import constants

CSS_PATH = Path(__file__).resolve().parents[1] / "styles" / "webapp.css"

# Semantic tokens and the values each theme must define.
LIGHT_THEME: dict[str, str] = {
    "--bg-base": "#F8F9FA",
    "--bg-surface": "#FFFFFF",
    "--bg-header": "#FFFFFF",
    "--text-primary": "#212529",
    "--text-secondary": "#6A7178",
    "--border-color": "#DEE2E6",
    "--primary": "#4F46E5",
    "--primary-hover": "#4338CA",
    "--primary-text": "#FFFFFF",
    "--success": "#047857",
    "--warning": "#B45309",
    "--danger": "#DC2626",
    "--info": "#2563EB",
}

DARK_THEME: dict[str, str] = {
    "--bg-base": "#121212",
    "--bg-surface": "#1E1E1E",
    "--bg-header": "#1E1E1E",
    "--text-primary": "#E9ECEF",
    "--text-secondary": "#ADB5BD",
    "--border-color": "#343A40",
    "--primary": "#5B57E8",
    "--primary-hover": "#4F46E5",
    "--primary-text": "#FFFFFF",
    "--success": "#34D399",
    "--warning": "#FBBF24",
    "--danger": "#F87171",
    "--info": "#60A5FA",
}

# Minimum contrast ratios: 4.5 for body text, 3.0 for icons and other graphics.
TEXT_CONTRAST = 4.5
GRAPHIC_CONTRAST = 3.0


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of a #RRGGBB color."""
    value = hex_color.lstrip("#")
    channels = [int(value[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two #RRGGBB colors."""
    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _theme_block(css: str, selector: str) -> dict[str, str]:
    """Return the custom properties declared inside a selector block."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match is not None, f"missing block: {selector}"
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(1)))


def _block_containing(css: str, token: str) -> dict[str, str]:
    """Return the custom properties of the block that declares ``token``."""
    for match in re.finditer(r"([^{}]*)\{([^}]*)\}", css):
        if token in match.group(2):
            return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", match.group(2)))
    raise AssertionError(f"no block declares {token}")


@pytest.fixture(scope="module")
def css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def test_css_file_is_the_one_the_app_loads() -> None:
    assert constants.WEBAPP_CSS_PATH == Path("styles/webapp.css")
    assert constants.load_webapp_css() == CSS_PATH.read_text(encoding="utf-8")


def test_light_theme_defines_every_token(css: str) -> None:
    block = _theme_block(css, ":root")
    for name, value in LIGHT_THEME.items():
        assert block.get(name, "").strip().upper() == value, f"light {name} != {value}"


def test_dark_theme_defines_every_token(css: str) -> None:
    block = _theme_block(css, "body.body--dark")
    for name, value in DARK_THEME.items():
        assert block.get(name, "").strip().upper() == value, f"dark {name} != {value}"


def test_quasar_brand_colors_are_remapped(css: str) -> None:
    block = _block_containing(css, "--q-primary")
    for quasar_var, token in (
        ("--q-primary", "--primary"),
        ("--q-primary-hover", "--primary-hover"),
        ("--q-positive", "--success"),
        ("--q-negative", "--danger"),
        ("--q-warning", "--warning"),
        ("--q-info", "--info"),
        ("--q-dark", "--bg-surface"),
        ("--q-dark-page", "--bg-base"),
    ):
        assert quasar_var in block, f"{quasar_var} not remapped"
        assert token in block[quasar_var], f"{quasar_var} must point at {token}"
        assert "important" in block[quasar_var], f"{quasar_var} needs !important"


def test_only_token_blocks_contain_hex_colors(css: str) -> None:
    """No stray hardcoded colors outside the theme variable blocks."""
    theme_blocks = [_theme_block(css, ":root"), _theme_block(css, "body.body--dark")]
    allowed = {value.strip().upper() for block in theme_blocks for value in block.values()}
    for hex_color in re.findall(r"#[0-9a-fA-F]{3,8}\b", css):
        assert hex_color.upper() in allowed, f"hardcoded color outside the theme: {hex_color}"


@pytest.mark.parametrize(
    ("foreground", "background", "minimum"),
    [
        ("--text-primary", "--bg-base", TEXT_CONTRAST),
        ("--text-primary", "--bg-surface", TEXT_CONTRAST),
        ("--text-secondary", "--bg-base", TEXT_CONTRAST),
        ("--text-secondary", "--bg-surface", TEXT_CONTRAST),
        ("--primary-text", "--primary", TEXT_CONTRAST),
        ("--primary", "--bg-surface", GRAPHIC_CONTRAST),
        ("--success", "--bg-surface", GRAPHIC_CONTRAST),
        ("--warning", "--bg-surface", GRAPHIC_CONTRAST),
        ("--danger", "--bg-surface", GRAPHIC_CONTRAST),
        ("--info", "--bg-surface", GRAPHIC_CONTRAST),
        ("--border-color", "--bg-surface", 1.0),
    ],
)
def test_theme_contrast_meets_wcag(
    css: str, foreground: str, background: str, minimum: float
) -> None:
    for name, theme in (("light", LIGHT_THEME), ("dark", DARK_THEME)):
        ratio = _contrast_ratio(theme[foreground], theme[background])
        assert ratio >= minimum, f"{name} {foreground} on {background}: {ratio:.2f}:1 < {minimum}"


def test_no_pure_white_or_black_surfaces(css: str) -> None:
    assert LIGHT_THEME["--bg-base"] != "#FFFFFF"
    assert DARK_THEME["--bg-base"] != "#000000"
    assert DARK_THEME["--bg-surface"] != "#000000"
