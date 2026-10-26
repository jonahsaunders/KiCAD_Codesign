"""Selection and connection dialogs with visible, keyboard-accessible controls."""
import os
from dataclasses import replace
import wx
import wx.lib.scrolledpanel
from PIL import Image
from . import desktop as desk
from .ui import Card, WrappedText, bitmap, button, enable_button, font, space, text
from .providers import Connection, PROVIDERS, validate_base_url
from .presentation import validate_connection_fields


class CircleOverlay(wx.Frame):
    def __init__(self, owner, snapshot, callback):
        # Keep the selector discoverable in the window switcher and accessibility
        # tools while it covers the editor. The controller owns its lifetime.
        super().__init__(None, title="Select a region — KiCAD Codesign",
                         style=wx.STAY_ON_TOP | wx.BORDER_NONE)
        self.theme = owner.theme
        self.snapshot, self.callback = snapshot, callback
        self.strokes = []
        self.drawing = self.finished = False
        # A frame automatically expands its sole child. Keep a canvas between
        # the frame and the toolbar so the toolbar cannot cover the drawing.
        self.canvas = wx.Panel(self)
        self.canvas.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.canvas.SetCursor(wx.Cursor(wx.CURSOR_CROSS))
        for kind, handler in ((wx.EVT_PAINT, self.paint), (wx.EVT_LEFT_DOWN, self.down),
                              (wx.EVT_MOTION, self.motion), (wx.EVT_LEFT_UP, self.up),
                              (wx.EVT_MOUSE_CAPTURE_LOST, self.capture_lost)):
            self.canvas.Bind(kind, handler)
        self.Bind(wx.EVT_CHAR_HOOK, self.key)
        self.Bind(wx.EVT_CLOSE, lambda e: self.finish(False))
        self.toolbar = Card(self.canvas)
        self.toolbar.SetCursor(wx.Cursor(wx.CURSOR_ARROW))
        self.toolbar.body.Add(text(self.toolbar, "Circle what you want to change", size=13, bold=True))
        self.count = text(self.toolbar, "Draw a region, or use the entire view.", size=10, secondary=True)
        self.toolbar.body.Add(self.count, 0, wx.TOP | wx.BOTTOM, space(self, 8))
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.cancel_button = button(self.toolbar, "Cancel", lambda e: self.finish(False), tooltip="Cancel · Esc")
        self.clear_button = button(self.toolbar, "Clear", self.clear, tooltip="Clear · Backspace")
        self.full_button = button(self.toolbar, "Entire view", self.entire_view, tooltip="Select without drawing")
        self.use_button = button(self.toolbar, "Use selection", lambda e: self.finish(True), primary=True, tooltip="Keep selection · Enter")
        for control in (self.cancel_button, self.clear_button, self.full_button, self.use_button):
            row.Add(control, 0, wx.RIGHT, space(self, 6))
        self.toolbar.body.Add(row)
        enable_button(self.use_button, False)
        self.clear_button.Disable()
        self.toolbar.Fit()
        self.toolbar.SetPosition((space(self, 16), space(self, 16)))
        left, top, right, bottom = snapshot.rect
        self.SetSize((right - left, bottom - top))
        self.Show()
        desk.U.SetWindowPos(self.GetHandle(), -1, left, top, right - left, bottom - top, 0x0040)
        self.Raise()
        wx.CallAfter(self.focus_toolbar)

    def focus_toolbar(self):
        if self and not self.finished:
            self.full_button.SetFocus()

    def point(self, event):
        width, height = self.canvas.GetClientSize()
        return max(0, min(1, event.GetX() / max(1, width))), max(0, min(1, event.GetY() / max(1, height)))

    def down(self, event):
        self.drawing = True
        self.strokes.append([self.point(event)])
        self.canvas.CaptureMouse()

    def motion(self, event):
        if self.drawing:
            self.strokes[-1].append(self.point(event))
            self.canvas.Refresh()

    def up(self, event):
        if self.drawing:
            self.strokes[-1].append(self.point(event))
            self.drawing = False
            if self.canvas.HasCapture():
                self.canvas.ReleaseMouse()
            self.update_selection()

    def capture_lost(self, event):
        self.drawing = False
        self.update_selection()

    def update_selection(self):
        count = len([stroke for stroke in self.strokes if len(stroke) > 2])
        self.count.SetLabel(f"{count} region{'s' if count != 1 else ''} selected · Enter to continue" if count else "Draw a region, or use the entire view.")
        enable_button(self.use_button, count > 0)
        self.clear_button.Enable(bool(self.strokes))
        self.canvas.Refresh()

    def clear(self, event=None):
        self.strokes = []
        self.update_selection()

    def entire_view(self, event=None):
        self.strokes = [[(.02, .02), (.98, .02), (.98, .98), (.02, .98)]]
        self.finish(True)

    def key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.finish(False)
        elif event.GetKeyCode() == wx.WXK_RETURN and self.use_button.IsEnabled():
            self.finish(True)
        elif event.GetKeyCode() in (wx.WXK_BACK, wx.WXK_DELETE):
            self.clear()
        else:
            event.Skip()

    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self.canvas)
        width, height = self.canvas.GetClientSize()
        im = self.snapshot.image.resize((max(1, width), max(1, height)), Image.Resampling.BILINEAR)
        dc.DrawBitmap(bitmap(im), 0, 0)
        dc.SetPen(wx.Pen("#F6924A", space(self, 3)))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        for index, stroke in enumerate(self.strokes):
            points = [wx.Point(round(x * width), round(y * height)) for x, y in stroke]
            if len(points) > 1:
                if not self.drawing or index < len(self.strokes) - 1:
                    points.append(points[0])
                dc.DrawLines(points)

    def finish(self, accepted):
        if self.finished:
            return
        valid = [stroke for stroke in self.strokes if len(stroke) > 2]
        if accepted and not valid:
            return
        self.finished = True
        if self.canvas.HasCapture():
            self.canvas.ReleaseMouse()
        self.Hide()
        self.Destroy()
        wx.CallAfter(self.callback, self.snapshot, valid if accepted else None)


class ConnectionDialog(wx.Dialog):
    def __init__(self, parent, connection, profiles):
        super().__init__(parent, title="Connection", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.theme = parent.theme
        self.SetBackgroundColour(self.theme.background)
        self.SetFont(font())
        self.profiles = {name: replace(value) for name, value in profiles.items()}
        self.profiles[connection.provider] = replace(connection)
        self.old_provider = connection.provider
        outer = wx.BoxSizer(wx.VERTICAL)
        self.content_scroll = wx.lib.scrolledpanel.ScrolledPanel(self, style=wx.TAB_TRAVERSAL)
        self.content_scroll.SetBackgroundColour(self.theme.background)
        self.content_scroll.SetMinSize((1, 1))
        content = self.content = Card(self.content_scroll)
        content.body.Add(text(content, "Choose your assistant", size=18, bold=True), 0, wx.BOTTOM, space(self, 6))
        content.body.Add(WrappedText(content, "Connect a model that can see your design and suggest editor actions.", secondary=True),
                         0, wx.EXPAND | wx.BOTTOM, space(self, 20))

        def field(label, create_control, name):
            content.body.Add(text(content, label, size=10, bold=True), 0, wx.BOTTOM, space(self, 6))
            control = create_control()
            control.SetFont(font())
            control.SetName(name)
            control.SetMinSize((-1, space(self, 32)))
            content.body.Add(control, 0, wx.EXPAND | wx.BOTTOM, space(self, 14))
            return control

        self.provider = field("Provider", lambda: wx.Choice(content, choices=list(PROVIDERS)), "Provider")
        self.provider.SetStringSelection(connection.provider)
        self.model = field("Model", lambda: wx.TextCtrl(content, value=connection.model), "Vision-capable model ID")
        self.api_key = field("API key", lambda: wx.TextCtrl(content, value=connection.api_key, style=wx.TE_PASSWORD), "API key, kept only for this session")
        content.body.Add(WrappedText(content, "Your key stays in memory until the app closes. API usage is billed by your provider.", secondary=True),
                         0, wx.EXPAND | wx.BOTTOM, space(self, 16))
        self.endpoint_panel = wx.Panel(content, style=wx.TAB_TRAVERSAL)
        self.endpoint_panel.SetBackgroundColour(self.theme.surface)
        endpoint = wx.BoxSizer(wx.VERTICAL)
        endpoint.Add(text(self.endpoint_panel, "API base URL", size=10, bold=True), 0, wx.BOTTOM, space(self, 6))
        self.url = wx.TextCtrl(self.endpoint_panel, value=connection.base_url)
        self.url.SetFont(font())
        self.url.SetName("Compatible API base URL")
        self.url.SetMinSize((-1, space(self, 32)))
        endpoint.Add(self.url, 0, wx.EXPAND)
        self.endpoint_panel.SetSizer(endpoint)
        content.body.Add(self.endpoint_panel, 0, wx.EXPAND | wx.BOTTOM, space(self, 16))
        self.endpoint_panel.Show(connection.provider == "OpenAI-compatible")
        self.error = WrappedText(content, "")
        self.error.SetForegroundColour(self.theme.error)
        content.body.Add(self.error, 0, wx.EXPAND)
        self.error.Hide()
        scroll_body = wx.BoxSizer(wx.VERTICAL)
        scroll_body.Add(content, 0, wx.EXPAND)
        self.content_scroll.SetSizer(scroll_body)
        self.content_scroll.SetupScrolling(scroll_x=False)
        outer.Add(self.content_scroll, 1, wx.EXPAND | wx.ALL, space(self, 16))
        buttons = wx.StdDialogButtonSizer()
        cancel = wx.Button(self, wx.ID_CANCEL, "Cancel")
        self.save = wx.Button(self, wx.ID_OK, "Save connection")
        for control in (cancel, self.save):
            control.SetMinSize((-1, space(self, 34)))
            buttons.AddButton(control)
        buttons.Realize()
        self.save.SetDefault()
        outer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, space(self, 16))
        self.SetSizer(outer)
        work = self.work_area()
        self.SetMinSize((min(self.FromDIP(460), work.width), min(self.FromDIP(360), work.height)))
        self.SetSize((min(self.FromDIP(480), work.width), min(self.FromDIP(550), work.height)))
        self.CentreOnParent()
        self.provider.Bind(wx.EVT_CHOICE, self.changed)
        self.Bind(wx.EVT_BUTTON, self.validate, id=wx.ID_OK)
        wx.CallAfter(self.fit_content)

    def work_area(self):
        index = wx.Display.GetFromWindow(self)
        return wx.Display(index if index != wx.NOT_FOUND else 0).GetClientArea()

    def fit_content(self, focus=None):
        if not self:
            return
        work = self.work_area()
        chrome = self.GetSize().height - self.GetClientSize().height
        needed = self.content.GetSizer().GetMinSize().height + space(self, 82) + chrome
        width, height = self.GetSize()
        self.SetSize((min(width, work.width), min(max(height, needed), work.height)))
        left, top = self.GetPosition()
        width, height = self.GetSize()
        self.SetPosition((max(work.x, min(left, work.x + work.width - width)),
                          max(work.y, min(top, work.y + work.height - height))))
        self.Layout()
        self.content_scroll.FitInside()
        if focus and self.IsShown():
            focus.SetFocus()
            self.content_scroll.ScrollChildIntoView(focus)

    def connection(self):
        return Connection(self.provider.GetStringSelection(), self.model.GetValue().strip(),
                          self.api_key.GetValue().strip(), self.url.GetValue().strip())

    def changed(self, event):
        self.profiles[self.old_provider] = Connection(self.old_provider, self.model.GetValue(), self.api_key.GetValue(), self.url.GetValue())
        name = self.provider.GetStringSelection()
        provider = PROVIDERS[name]
        selected = self.profiles.get(name, Connection(name, provider.default_model, os.getenv(provider.env_key, ""), provider.default_url))
        self.model.ChangeValue(selected.model)
        self.api_key.ChangeValue(selected.api_key)
        self.url.ChangeValue(selected.base_url)
        self.endpoint_panel.Show(name == "OpenAI-compatible")
        self.error.Hide()
        self.old_provider = name
        self.Layout()
        self.content_scroll.Scroll(0, 0)
        wx.CallAfter(self.fit_content)

    def validate(self, event):
        connection = self.connection()
        problem = validate_connection_fields(connection.provider, connection.model, connection.api_key,
                                             connection.base_url, PROVIDERS, validate_base_url)
        if problem:
            field, message = problem
            self.error.Show()
            self.error.set_text(message)
            self.Layout()
            wx.CallAfter(self.fit_content, getattr(self, field))
            return
        self.profiles[connection.provider] = connection
        self.EndModal(wx.ID_OK)
