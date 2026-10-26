"""Pure presentation rules shared by the interface and its tests."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ControlState:
    can_select: bool
    can_circle: bool
    can_ask: bool
    can_apply: bool
    can_stop: bool
    can_undo: bool


def controls_for(*, has_editor, has_selection, has_prompt, busy, running, pending, overlay=False):
    locked = busy or running or pending or overlay
    return ControlState(
        can_select=not locked,
        can_circle=has_editor and not locked,
        can_ask=has_editor and has_selection and has_prompt and not locked,
        can_apply=pending and running and not busy and not overlay,
        can_stop=busy or running or pending,
        can_undo=has_editor and not locked,
    )


def fit_window(work_width, work_height, scale=1):
    """Return physical window dimensions that remain inside the current work area."""
    return min(round(500 * scale), work_width), min(round(960 * scale), work_height)


def arrangement_widths(work_width, scale=1):
    """Do not tile if either application would become too narrow to operate."""
    companion = round(480 * scale)
    editor_minimum = round(600 * scale)
    if work_width < companion + editor_minimum:
        return None
    return work_width - companion, companion


def validate_connection_fields(provider, model, api_key, base_url, providers, validate_url):
    if provider not in providers:
        return "provider", "Choose a provider."
    if not model.strip():
        return "model", "Enter a model that supports images and tool use."
    if provider != "OpenAI-compatible" and not api_key.strip():
        return "api_key", f"Enter your {provider} API key. It stays in memory until you close the app."
    if provider == "OpenAI-compatible":
        try:
            validate_url(base_url.strip())
        except ValueError as error:
            return "url", str(error)
        except RuntimeError as error:
            return "url", str(error)
    return None


def relative_luminance(hex_color):
    channels = [int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]


def contrast_ratio(foreground, background):
    values = sorted([relative_luminance(foreground), relative_luminance(background)])
    return (values[1] + .05) / (values[0] + .05)
