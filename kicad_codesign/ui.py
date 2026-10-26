"""Shared visual primitives. Interactive controls retain native keyboard behavior."""
from dataclasses import dataclass
import wx
from wx.lib.wordwrap import wordwrap


@dataclass(frozen=True)
class Palette:
    background: str = "#F5F6F8"
    surface: str = "#FFFFFF"
    text: str = "#20242C"
    secondary: str = "#586170"
    border: str = "#DFE3E9"
    accent: str = "#185ADB"
    accent_text: str = "#FFFFFF"
    tint: str = "#EEF3FF"
    success: str = "#187348"
    warning: str = "#8A5600"
    error: str = "#BB2939"


LIGHT = Palette()
DARK = Palette("#1D2026", "#282C34", "#F2F4F8", "#BCC5D2", "#454D5A",
               "#A4C1FF", "#102545", "#293B5A", "#8CD6AD", "#F2CC86", "#FFAFB9")


def palette_for_system():
    background = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW)
    text = wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT)
    # Honor custom contrast themes rather than forcing a light decorative surface.
    if sum(background.Get()[:3]) < 320:
        return DARK
    if sum(text.Get()[:3]) > 500:
        return DARK
    return LIGHT


def font(size=11, bold=False):
    result = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
    result.SetPointSize(max(size, result.GetPointSize()))
    result.SetWeight(wx.FONTWEIGHT_BOLD if bold else wx.FONTWEIGHT_NORMAL)
    return result


def space(window, value):
    return window.FromDIP(value)


def text(parent, label, *, size=11, bold=False, secondary=False):
    widget = wx.StaticText(parent, label=label)
    theme = parent.GetTopLevelParent().theme
    widget.SetFont(font(size, bold))
    widget.SetForegroundColour(theme.secondary if secondary else theme.text)
    return widget


class WrappedText(wx.StaticText):
    """Keep the original string so repeated resize events cannot accumulate newlines."""
    def __init__(self, parent, label="", *, secondary=False, size=10):
        super().__init__(parent, label=label, style=wx.ST_NO_AUTORESIZE)
        self.source = label
        self._wrapped_height = None
        self.SetFont(font(size))
        theme = parent.GetTopLevelParent().theme
        self.SetForegroundColour(theme.secondary if secondary else theme.text)
        self.SetMinSize((1, -1))
        self.Bind(wx.EVT_SIZE, self.resized)

    def set_text(self, value):
        self.source = value
        self.rewrap()

    def rewrap(self):
        width = self.GetClientSize().width
        if width < 10:
            width = max(100, self.GetParent().GetClientSize().width - space(self, 40))
        self.SetMinSize((1, -1))
        dc = wx.ClientDC(self)
        dc.SetFont(self.GetFont())
        wrapped = wordwrap(self.source, width, dc, breakLongWords=True)
        super().SetLabel(wrapped)
        # A cached best size includes the previous minimum. Measure line height
        # directly so widening the window can shrink a previously wrapped label.
        lines = max(1, len(wrapped.splitlines()))
        height = lines * self.GetCharHeight() + space(self, 2)
        self.SetMinSize((1, height))
        if height != self._wrapped_height:
            self._wrapped_height = height
            wx.CallAfter(self.relayout)

    def relayout(self):
        if not self:
            return
        parent = self.GetParent()
        while parent:
            parent.Layout()
            parent = parent.GetParent()

    def resized(self, event):
        self.rewrap()
        event.Skip()


def button(parent, label, handler, *, primary=False, tooltip="", name=None):
    control = wx.Button(parent, label=label)
    control.SetFont(font(10))
    control.SetMinSize((-1, space(parent, 34)))
    theme = parent.GetTopLevelParent().theme
    if primary:
        control.SetBackgroundColour(theme.accent)
        control.SetForegroundColour(theme.accent_text)
    control.SetName(name or label.replace("&", ""))
    if tooltip:
        control.SetToolTip(tooltip)
    control.Bind(wx.EVT_BUTTON, handler)
    control.codesign_primary = primary
    return control


def enable_button(control, enabled):
    control.Enable(enabled)
    if getattr(control, "codesign_primary", False):
        theme = control.GetTopLevelParent().theme
        control.SetBackgroundColour(theme.accent if enabled else theme.background)
        control.SetForegroundColour(theme.accent_text if enabled else theme.secondary)


class Card(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent, style=wx.TAB_TRAVERSAL)
        self.theme = parent.GetTopLevelParent().theme
        self.SetBackgroundColour(self.theme.surface)
        self.body = wx.BoxSizer(wx.VERTICAL)
        shell = wx.BoxSizer(wx.VERTICAL)
        shell.Add(self.body, 1, wx.EXPAND | wx.ALL, space(self, 16))
        self.SetSizer(shell)


def bitmap(image):
    rgb = image.convert("RGB")
    return wx.Bitmap.FromBuffer(rgb.width, rgb.height, rgb.tobytes())


class Preview(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent, size=(-1, space(parent, 128)))
        self.image = None
        self.theme = parent.GetTopLevelParent().theme
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetName("Selection preview. No region selected.")
        self.title = text(self, "Give your idea a place to start", size=12, bold=True)
        self.description = text(self, "Circle a region in the KiCad editor.", size=10, secondary=True)
        self.title.SetBackgroundColour(self.theme.background)
        self.description.SetBackgroundColour(self.theme.background)
        empty = wx.BoxSizer(wx.VERTICAL)
        empty.AddStretchSpacer()
        empty.Add(self.title, 0, wx.ALIGN_CENTER | wx.BOTTOM, space(self, 8))
        empty.Add(self.description, 0, wx.ALIGN_CENTER)
        empty.AddStretchSpacer()
        self.SetSizer(empty)
        self.Bind(wx.EVT_PAINT, self.paint)
        self.Bind(wx.EVT_SIZE, lambda event: (self.Refresh(), event.Skip()))

    def set_image(self, image, description="Selected design region"):
        self.image = image
        self.title.Show(image is None)
        self.description.Show(image is None)
        self.SetName(description if image is not None else "Selection preview. No region selected.")
        self.Layout()
        self.Refresh()

    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.theme.background))
        dc.Clear()
        width, height = self.GetClientSize()
        if self.image:
            im = self.image.copy()
            im.thumbnail((max(1, width - space(self, 12)), max(1, height - space(self, 12))))
            dc.DrawBitmap(bitmap(im), (width - im.width) // 2, (height - im.height) // 2)


class StatusPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.theme = parent.GetTopLevelParent().theme
        self.SetBackgroundColour(self.theme.surface)
        body = wx.BoxSizer(wx.VERTICAL)
        line = wx.BoxSizer(wx.HORIZONTAL)
        self.title = text(self, "Ready", size=11, bold=True)
        self.counter = text(self, "", size=10, secondary=True)
        line.Add(self.title, 1)
        line.Add(self.counter, 0, wx.LEFT, space(self, 8))
        body.Add(line, 0, wx.EXPAND)
        self.detail = WrappedText(self, "Choose an editor to start.", secondary=True)
        body.Add(self.detail, 0, wx.EXPAND | wx.TOP, space(self, 5))
        self.gauge = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL, size=(-1, space(self, 3)))
        body.Add(self.gauge, 0, wx.EXPAND | wx.TOP, space(self, 10))
        self.gauge.Hide()
        self.SetSizer(body)

    def update(self, title, detail, tone="normal", count=0, busy=False):
        self.title.SetLabel(title)
        color = {"normal": self.theme.text, "working": self.theme.accent,
                 "success": self.theme.success, "warning": self.theme.warning,
                 "error": self.theme.error}.get(tone, self.theme.text)
        self.title.SetForegroundColour(color)
        self.detail.set_text(detail)
        self.counter.SetLabel(f"{count} / 40 actions" if count else "")
        self.gauge.Show(busy)
        self.Layout()
