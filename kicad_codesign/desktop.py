"""Windows adapter: physical-pixel screenshots and input limited to one KiCad editor."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
from pathlib import Path
import time
import io
from PIL import ImageGrab, ImageChops, ImageDraw
from .protocol import validate_key
from .presentation import arrangement_widths

U = C.WinDLL("user32", use_last_error=True)
K = C.WinDLL("kernel32", use_last_error=True)
try:
    U.SetProcessDpiAwarenessContext(C.c_void_p(-4))
except (AttributeError, OSError):
    U.SetProcessDPIAware()

def signature(dll, name, result, *args):
    f = getattr(dll, name); f.restype = result; f.argtypes = list(args); return f

WNDPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
signature(U, "EnumWindows", W.BOOL, WNDPROC, W.LPARAM)
signature(U, "GetWindowThreadProcessId", W.DWORD, W.HWND, C.POINTER(W.DWORD))
signature(U, "GetWindowTextW", C.c_int, W.HWND, W.LPWSTR, C.c_int)
signature(U, "GetClassNameW", C.c_int, W.HWND, W.LPWSTR, C.c_int)
signature(U, "GetWindowRect", W.BOOL, W.HWND, C.POINTER(W.RECT))
signature(U, "GetWindow", W.HWND, W.HWND, W.UINT)
signature(U, "GetAncestor", W.HWND, W.HWND, W.UINT)
signature(U, "GetLastActivePopup", W.HWND, W.HWND)
signature(U, "GetForegroundWindow", W.HWND)
signature(U, "WindowFromPoint", W.HWND, W.POINT)
for fn in ("IsWindow", "IsWindowVisible", "IsIconic", "IsWindowEnabled"):
    signature(U, fn, W.BOOL, W.HWND)
signature(U, "SetForegroundWindow", W.BOOL, W.HWND)
signature(U, "ShowWindow", W.BOOL, W.HWND, C.c_int)
signature(U, "SetWindowPos", W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT)
signature(U, "SetCursorPos", W.BOOL, C.c_int, C.c_int)
signature(U, "GetAsyncKeyState", C.c_short, C.c_int)
signature(K, "OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
signature(K, "QueryFullProcessImageNameW", W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
signature(K, "CloseHandle", W.BOOL, W.HANDLE)

class MouseInput(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD), ("time", W.DWORD), ("extra", C.c_size_t)]
class KeyboardInput(C.Structure):
    _fields_ = [("vk", W.WORD), ("scan", W.WORD), ("flags", W.DWORD), ("time", W.DWORD), ("extra", C.c_size_t)]
class InputUnion(C.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput)]
class Input(C.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", W.DWORD), ("u", InputUnion)]
signature(U, "SendInput", W.UINT, W.UINT, C.POINTER(Input), C.c_int)
class GuiInfo(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", W.HWND), ("hwndFocus", W.HWND),
                ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND), ("hwndMoveSize", W.HWND), ("hwndCaret", W.HWND), ("rcCaret", W.RECT)]
signature(U, "GetGUIThreadInfo", W.BOOL, W.DWORD, C.POINTER(GuiInfo))

class DesktopError(RuntimeError): pass

def title(hwnd):
    buf = C.create_unicode_buffer(2048); U.GetWindowTextW(hwnd, buf, len(buf)); return buf.value
def classname(hwnd):
    buf = C.create_unicode_buffer(256); U.GetClassNameW(hwnd, buf, len(buf)); return buf.value
def pid_of(hwnd):
    pid = W.DWORD(); U.GetWindowThreadProcessId(hwnd, C.byref(pid)); return pid.value
def process_path(hwnd):
    h = K.OpenProcess(0x1000, False, pid_of(hwnd))
    if not h: return ""
    try:
        b = C.create_unicode_buffer(32768); n = W.DWORD(len(b))
        return b.value if K.QueryFullProcessImageNameW(h, 0, b, C.byref(n)) else ""
    finally: K.CloseHandle(h)

def rect_of(hwnd):
    r = W.RECT()
    if not U.GetWindowRect(hwnd, C.byref(r)): raise DesktopError("The editor is no longer open.")
    if r.right - r.left < 50 or r.bottom - r.top < 50: raise DesktopError("The editor is minimized or too small.")
    return (r.left, r.top, r.right, r.bottom)

@dataclass(frozen=True)
class Editor:
    hwnd: int
    pid: int
    title: str
    view: str
    exe: str

def list_editors():
    found = []
    @WNDPROC
    def each(hwnd, _):
        if not U.IsWindowVisible(hwnd) or U.GetWindow(hwnd, 4): return True
        name = title(hwnd); lower = name.lower()
        view = next((v for token, v in [("footprint editor", "Footprint"), ("pcb editor", "Layout"), ("schematic editor", "Schematic"),
                     ("pcbnew", "Layout"), ("eeschema", "Schematic")] if token in lower), None)
        if not view: return True
        exe = process_path(hwnd)
        if Path(exe).name.lower() in {"kicad.exe", "pcbnew.exe", "eeschema.exe"}:
            found.append(Editor(int(hwnd), pid_of(hwnd), name, view, exe))
        return True
    U.EnumWindows(each, 0)
    return sorted(found, key=lambda x: (x.view, x.title))

def belongs(hwnd, editor):
    if not hwnd or pid_of(hwnd) != editor.pid: return False
    current = U.GetAncestor(hwnd, 2) or hwnd
    for _ in range(16):
        if current == editor.hwnd: return True
        current = U.GetWindow(current, 4)
        if not current: break
    return False

def validate_editor(editor):
    if not U.IsWindow(editor.hwnd) or pid_of(editor.hwnd) != editor.pid or process_path(editor.hwnd) != editor.exe:
        raise DesktopError("The selected KiCad editor closed or changed. Select it again.")

def target_window(editor):
    validate_editor(editor)
    fg = U.GetForegroundWindow()
    if belongs(fg, editor): return fg
    h = editor.hwnd
    for _ in range(12):
        nxt = U.GetLastActivePopup(h)
        if not nxt or nxt == h or not U.IsWindowVisible(nxt): break
        h = nxt
    return h

def activate(editor, hwnd=None):
    validate_editor(editor)
    h = hwnd or target_window(editor)
    if not belongs(h, editor): raise DesktopError("This window does not belong to the selected editor.")
    if U.IsIconic(editor.hwnd): U.ShowWindow(editor.hwnd, 9)
    U.SetForegroundWindow(h)
    time.sleep(0.16)
    if not belongs(U.GetForegroundWindow(), editor):
        raise DesktopError("Windows did not focus KiCad. Click the editor, then retry.")
    return h

def ensure_visible(editor, rect):
    left, top, right, bottom = rect
    # Borders/shadows are omitted; an obscuring window makes capture fail closed.
    for ix in (0.06, .25, .5, .75, .94):
        for iy in (.09, .3, .5, .7, .92):
            h = U.WindowFromPoint(W.POINT(int(left+(right-left)*ix), int(top+(bottom-top)*iy)))
            if not belongs(h, editor) and not (pid_of(h) == editor.pid and classname(h) == "#32768"):
                raise DesktopError("Another window covers KiCad. Use Arrange windows, then retry.")

@dataclass
class Snapshot:
    hwnd: int
    rect: tuple
    image: object
    title: str

    def png(self):
        f = io.BytesIO(); self.image.save(f, "PNG"); return f.getvalue()

def capture(editor):
    h = activate(editor)
    r = rect_of(h)
    ensure_visible(editor, r)
    im = ImageGrab.grab(bbox=r, all_screens=True, include_layered_windows=True).convert("RGB")
    im.thumbnail((1560, 1000))
    return Snapshot(h, r, im, title(h))

def image_changed(a, b):
    if a.size != b.size: return True
    diff = ImageChops.difference(a, b).convert("L")
    # Ignore a blinking caret and antialiasing noise, but reject material view changes.
    count = sum(n for level, n in enumerate(diff.histogram()) if level > 28)
    return count / (a.width * a.height) > 0.002

def verify_snapshot(editor, before):
    after = capture(editor)
    if before.hwnd != after.hwnd or before.rect != after.rect or before.title != after.title or image_changed(before.image, after.image):
        raise DesktopError("KiCad changed while the model was thinking. No action was sent. Ask again using the current view.")
    return after

def annotate(image, strokes):
    result = image.copy(); d = ImageDraw.Draw(result)
    for stroke in strokes:
        points = [(round(x * result.width), round(y * result.height)) for x,y in stroke]
        if len(points) > 1:
            d.line(points+[points[0]], fill=(246, 146, 74), width=4, joint="curve")
    return result

def send_input(item):
    if U.SendInput(1, C.byref(item), C.sizeof(Input)) != 1:
        raise DesktopError("Windows blocked editor input. Run both apps at the same privilege level.")

def mouse(flags, data=0):
    send_input(Input(type=0, mi=MouseInput(0, 0, data & 0xffffffff, flags, 0, 0)))

def keyboard(vk=0, scan=0, flags=0):
    send_input(Input(type=1, ki=KeyboardInput(vk, scan, flags, 0, 0)))

KEYS = {"ctrl":0x11, "shift":0x10, "escape":0x1b, "enter":0x0d, "tab":9, "space":32, "backspace":8,
        "delete":0x2e, "up":0x26, "down":0x28, "left":0x25, "right":0x27, "home":0x24, "end":0x23,
        "pageup":0x21, "pagedown":0x22, "insert":0x2d, ",":0xbc, ".":0xbe, "/":0xbf, ";":0xba,
        "[":0xdb, "]":0xdd, "-":0xbd, "=":0xbb, **{f"f{i}":0x6f+i for i in range(1,13)}}

def emergency_pressed():
    return bool(U.GetAsyncKeyState(0x1b) & 0x8000)

def execute(editor, action, snapshot, cancelled=lambda: False):
    def check():
        if cancelled() or emergency_pressed(): raise DesktopError("Stopped. No more input will be sent.")
        validate_editor(editor)
        if not belongs(U.GetForegroundWindow(), editor): raise DesktopError("Focus left KiCad. Stopped before the next input.")
    check()
    h = snapshot.hwnd
    if not belongs(h, editor) or rect_of(h) != snapshot.rect: raise DesktopError("The editor moved. Ask again.")
    if any(s in title(h).lower() for s in ("scripting", "python", "console", "save as", "open file", "preferences")):
        raise DesktopError("This window requires your attention. Automation is paused.")
    left, top, right, bottom = snapshot.rect
    def move(x,y):
        check()
        px = left + round(x/1000*(right-left-1)); py = top + round(y/1000*(bottom-top-1))
        under = U.WindowFromPoint(W.POINT(px,py))
        if not belongs(under, editor) and not (pid_of(under) == editor.pid and classname(under) == "#32768"):
            raise DesktopError("The target point is covered by another window.")
        U.SetCursorPos(px,py)
    kind = action.kind
    if kind in {"move", "click", "double_click", "right_click", "drag", "scroll"}:
        move(action.x, action.y); time.sleep(.07); check()
    if kind in {"click", "double_click", "right_click"}:
        down, up = (8,16) if kind == "right_click" else (2,4)
        for _ in range(2 if kind == "double_click" else 1):
            check(); mouse(down)
            try: time.sleep(.035)
            finally: mouse(up)
            time.sleep(.06)
    elif kind == "drag":
        mouse(2)
        try:
            for n in range(1,21):
                move(action.x+(action.x2-action.x)*n/20, action.y+(action.y2-action.y)*n/20); time.sleep(.015)
        finally: mouse(4)
    elif kind == "scroll": mouse(0x0800, round(action.delta)*120)
    elif kind == "key":
        parts = validate_key(action.key); pressed=[]
        try:
            for part in parts:
                check(); vk=KEYS.get(part, ord(part.upper()) if len(part)==1 else 0)
                keyboard(vk); pressed.append(vk)
            time.sleep(.035)
        finally:
            for vk in reversed(pressed): keyboard(vk, flags=2)
    elif kind == "type":
        info = GuiInfo(); info.cbSize=C.sizeof(info)
        thread = U.GetWindowThreadProcessId(U.GetForegroundWindow(), None)
        U.GetGUIThreadInfo(thread, C.byref(info))
        cl = classname(info.hwndFocus).lower()
        if not belongs(info.hwndFocus, editor) or not any(x in cl for x in ("edit", "combobox", "textctrl")):
            raise DesktopError("Text entry needs a focused KiCad text field. Click the field and retry.")
        raw = action.text.encode("utf-16-le")
        for i in range(0,len(raw),2):
            check(); scan=int.from_bytes(raw[i:i+2], "little")
            keyboard(scan=scan, flags=4)
            keyboard(scan=scan, flags=6)
    elif kind == "wait":
        until=time.monotonic()+action.seconds
        while time.monotonic()<until: check(); time.sleep(.025)
    time.sleep(.22)

def arrange(editor, companion_hwnd):
    validate_editor(editor)
    class MonitorInfo(C.Structure):
        _fields_=[("cbSize",W.DWORD),("rcMonitor",W.RECT),("rcWork",W.RECT),("flags",W.DWORD)]
    signature(U,"MonitorFromWindow",W.HANDLE,W.HWND,W.DWORD)
    signature(U,"GetMonitorInfoW",W.BOOL,W.HANDLE,C.POINTER(MonitorInfo))
    info=MonitorInfo(); info.cbSize=C.sizeof(info)
    U.GetMonitorInfoW(U.MonitorFromWindow(editor.hwnd,2),C.byref(info))
    r=info.rcWork; width=r.right-r.left; height=r.bottom-r.top
    signature(U,"GetDpiForWindow",W.UINT,W.HWND)
    scale=(U.GetDpiForWindow(companion_hwnd) or 96)/96
    widths=arrangement_widths(width, scale)
    if widths is None:
        raise DesktopError("This display is too narrow for two usable windows. Place KiCad and Codesign on separate displays, or arrange them manually.")
    editor_width, panel=widths
    U.ShowWindow(editor.hwnd,9)
    U.SetWindowPos(editor.hwnd,None,r.left,r.top,editor_width,height,0x0014)
    U.SetWindowPos(companion_hwnd,None,r.right-panel,r.top,panel,height,0x0014)
