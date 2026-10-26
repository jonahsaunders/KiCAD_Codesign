"""Render real native widgets with deterministic example data, without UI input.

No live editor is read or controlled, no API key is loaded, and no request is sent.
The practice-board fixture was exported from demo/Codesign-Practice.kicad_pcb.
"""
import ctypes as C
from ctypes import wintypes as W
import math
import json
from pathlib import Path
import sys
from unittest.mock import patch
from background_desktop import isolate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    desktop = isolate()  # Must precede wx import and window creation.
    import wx
    from PIL import Image
    from kicad_codesign import desktop as desk
    from kicad_codesign.app import CodesignFrame
    from kicad_codesign.dialogs import CircleOverlay, ConnectionDialog
    from kicad_codesign.providers import Connection
    from kicad_codesign.protocol import Action, Decision
    from kicad_codesign.ui import WrappedText

    user = C.WinDLL('user32', use_last_error=True)
    user.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
    user.PrintWindow.restype = W.BOOL
    images = ROOT / 'docs' / 'images'
    review = ROOT / 'build' / 'interface-review'
    images.mkdir(parents=True, exist_ok=True)
    review.mkdir(parents=True, exist_ok=True)
    app = wx.App(False)

    def settle(window):
        for _ in range(16):
            window.Layout()
            window.Refresh()
            window.Update()
            wx.Yield()

    def capture(window, path):
        settle(window)
        def labels(parent):
            result = []
            for child in parent.GetChildren():
                if isinstance(child, WrappedText):
                    result.append({'text': child.GetLabel(), 'size': list(child.GetSize()),
                                   'minimum': list(child.GetMinSize()), 'shown': child.IsShown()})
                result.extend(labels(child))
            return result
        (review / (path.stem + '.json')).write_text(json.dumps(labels(window), indent=2), encoding='utf-8')
        width, height = window.GetClientSize()
        output = wx.Bitmap(width, height, 24)
        dc = wx.MemoryDC(output)
        try:
            if not user.PrintWindow(window.GetHandle(), dc.GetHandle(), 1):
                raise C.WinError(C.get_last_error())
        finally:
            dc.SelectObject(wx.NullBitmap)
        if not output.SaveFile(str(path), wx.BITMAP_TYPE_PNG):
            raise RuntimeError(f'Could not save {path}')
        print(f'{path.relative_to(ROOT)}: {width} x {height}')

    # Fail closed if this fixture ever attempts live capture or native input.
    with patch.dict('os.environ', {'OPENAI_API_KEY': '', 'ANTHROPIC_API_KEY': '', 'LLM_API_KEY': ''}), \
         patch.object(desk, 'list_editors', return_value=[]), \
         patch.object(desk, 'capture', side_effect=AssertionError('No live captures in render fixtures')), \
         patch.object(desk, 'execute', side_effect=AssertionError('No input in render fixtures')), \
         patch('kicad_codesign.app.request_decision', side_effect=AssertionError('No provider calls in render fixtures')):
        frame = CodesignFrame()
        frame.timer.Stop()
        frame.SetSize(frame.FromDIP((500, 1020)))
        frame.Show()
        capture(frame, review / 'empty.png')
        board = Image.open(ROOT / 'tests' / 'fixtures' / 'practice-board.png').convert('RGB')
        snapshot = desk.Snapshot(0, (0, 0, board.width, board.height), board, 'Practice board export')
        editor = desk.Editor(0, 0, 'Codesign-Practice — PCB Editor', 'Layout', '')
        frame.editors = [editor]
        frame.choice.Append(f'{editor.view} · {editor.title}')
        frame.choice.SetSelection(1)
        frame.selected()
        stroke = [(.30 + .085 * math.cos(i * math.tau / 60),
                   .295 + .115 * math.sin(i * math.tau / 60)) for i in range(61)]
        frame.circled(snapshot, [stroke])
        frame.prompt.ChangeValue('Set the circled resistor R1 to 10k. Keep its position unchanged.')
        frame.controls()
        capture(frame, images / 'workspace.png')

        overlay = CircleOverlay(frame, snapshot, lambda *args: None)
        overlay.strokes = [stroke]
        overlay.update_selection()
        capture(overlay, review / 'selection.png')
        overlay.finish(False)
        wx.Yield()

        with ConnectionDialog(frame, Connection(api_key=''), {}) as dialog:
            dialog.Show()
            capture(dialog, images / 'connection.png')
            dialog.validate(None)
            capture(dialog, review / 'connection-error.png')
            dialog.provider.SetStringSelection('OpenAI-compatible')
            dialog.changed(None)
            capture(dialog, review / 'connection-compatible.png')

        frame.running = True
        frame.provider_label.SetLabel(f'OpenAI · {frame.connection.model}')
        frame.received(frame.generation, Decision('act',
            'Open the selected resistor’s properties so its value can be updated.',
            [], Action('key', key='e')), None)
        capture(frame, images / 'action-review.png')
        for width, height in ((440, 600), (760, 900)):
            frame.SetSize(frame.FromDIP((width, height)))
            capture(frame, review / f'review-{width}x{height}.png')
        frame.stop()
        capture(frame, review / 'stopped.png')
        frame.close(None)
        wx.Yield()


if __name__ == '__main__':
    main()
