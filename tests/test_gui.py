"""Native widget regression tests; no editor input or provider traffic."""
import unittest
from unittest.mock import patch
import wx
from PIL import Image
from kicad_codesign.app import CodesignFrame
from kicad_codesign.dialogs import CircleOverlay, ConnectionDialog
from kicad_codesign.desktop import Snapshot
from kicad_codesign.providers import Connection
from kicad_codesign.protocol import Action, Decision


class NativeInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App.Get() or wx.App(False)

    def setUp(self):
        with patch('kicad_codesign.desktop.list_editors', return_value=[]):
            self.frame = CodesignFrame()
        self.frame.timer.Stop()

    def tearDown(self):
        self.frame.close(None)
        wx.Yield()

    def settle(self):
        for _ in range(8):
            self.frame.Layout()
            self.frame.root.Layout()
            wx.Yield()

    def test_footer_controls_stay_inside_compact_and_tall_windows(self):
        for size in ((440, 600), (500, 960), (760, 900)):
            with self.subTest(size=size):
                self.frame.SetSize(self.frame.FromDIP(size))
                self.settle()
                client = self.frame.GetClientRect()
                for control in (self.frame.stop_button, self.frame.ask_button, self.frame.undo_button):
                    pos = self.frame.ScreenToClient(control.ClientToScreen((0, 0)))
                    extent = control.GetSize()
                    self.assertGreaterEqual(pos.y, 0)
                    self.assertLessEqual(pos.y + extent.height, client.height)
                    self.assertLessEqual(pos.x + extent.width, client.width)

    def test_wrapping_shrinks_after_widening(self):
        self.frame.SetSize(self.frame.FromDIP((440, 600)))
        self.frame.status('Choose a design, circle the region you want to edit, then describe the change in the field above.')
        self.settle()
        narrow = self.frame.state.detail.GetMinSize().height
        self.frame.SetSize(self.frame.FromDIP((760, 900)))
        self.settle()
        self.assertLessEqual(self.frame.state.detail.GetMinSize().height, narrow)
        self.assertLess(self.frame.state.GetSize().height, self.frame.FromDIP(160))

    def test_wrapped_text_allocates_space_for_every_line(self):
        self.frame.SetSize(self.frame.FromDIP((440, 600)))
        self.frame.status('Choose a design, circle the region you want to edit, then describe the change in the field above.')
        self.settle()
        detail = self.frame.state.detail
        lines = len(detail.GetLabel().splitlines())
        self.assertGreater(lines, 1)
        self.assertGreaterEqual(detail.GetSize().height, lines * detail.GetCharHeight())

    def test_stop_discards_pending_and_late_provider_decisions(self):
        self.frame.running = True
        self.frame.pending = Action('key', key='e')
        self.frame.controls()
        generation = self.frame.generation
        self.assertTrue(self.frame.step_button.IsEnabled())
        self.frame.stop()
        self.frame.received(generation, Decision('act', 'Late response', [], Action('key', key='r')), None)
        self.assertIsNone(self.frame.pending)
        self.assertFalse(self.frame.running)
        self.assertEqual(self.frame.state_title, 'Stopped')

    def test_connection_preserves_provider_drafts_and_validates_empty_key(self):
        original = Connection(model='custom-model')
        with ConnectionDialog(self.frame, original, {}) as dialog:
            dialog.validate(None)
            self.assertTrue(dialog.error.IsShown())
            self.assertIn('API key', dialog.error.source)
            dialog.provider.SetStringSelection('Claude')
            dialog.changed(None)
            dialog.model.ChangeValue('custom-claude')
            dialog.provider.SetStringSelection('OpenAI')
            dialog.changed(None)
            self.assertEqual(dialog.model.GetValue(), 'custom-model')
            dialog.provider.SetStringSelection('Claude')
            dialog.changed(None)
            self.assertEqual(dialog.model.GetValue(), 'custom-claude')
        self.assertEqual(original.model, 'custom-model')

    def test_connection_extra_fields_and_error_fit_the_content(self):
        with ConnectionDialog(self.frame, Connection(api_key=''), {}) as dialog:
            dialog.Show()
            dialog.provider.SetStringSelection('OpenAI-compatible')
            dialog.changed(None)
            dialog.validate(None)
            self.settle()
            self.assertTrue(dialog.endpoint_panel.IsShown())
            self.assertTrue(dialog.error.IsShown())
            for control in (dialog.url, dialog.error):
                position = dialog.content.ScreenToClient(control.ClientToScreen((0, 0)))
                self.assertLessEqual(position.y + control.GetSize().height,
                                     dialog.content.GetSize().height)
            position = dialog.ScreenToClient(dialog.save.ClientToScreen((0, 0)))
            self.assertLessEqual(position.y + dialog.save.GetSize().height, dialog.GetClientSize().height)

    def make_overlay(self, results):
        snapshot = Snapshot(0, (0, 0, 1100, 900), Image.new('RGB', (1100, 900), '#152030'), 'Test board')
        overlay = CircleOverlay(self.frame, snapshot, lambda snap, strokes: results.append(strokes))
        self.settle()
        return overlay

    def test_region_canvas_remains_visible_and_accepts_drawing(self):
        results = []
        overlay = self.make_overlay(results)
        self.assertLess(overlay.toolbar.GetSize().height, overlay.canvas.GetSize().height / 2)
        for kind, position, handler in ((wx.wxEVT_LEFT_DOWN, (200, 300), overlay.down),
                                         (wx.wxEVT_MOTION, (350, 400), overlay.motion),
                                         (wx.wxEVT_MOTION, (200, 500), overlay.motion),
                                         (wx.wxEVT_LEFT_UP, (200, 300), overlay.up)):
            event = wx.MouseEvent(kind)
            event.SetPosition(position)
            handler(event)
        self.assertTrue(overlay.use_button.IsEnabled())
        overlay.finish(True)
        overlay.finish(True)
        wx.Yield()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 1)
        self.assertEqual(len(results[0][0]), 4)
        self.assertTrue(all(0 <= x <= 1 and 0 <= y <= 1 for x, y in results[0][0]))

    def test_region_clear_cancel_and_entire_view(self):
        results = []
        overlay = self.make_overlay(results)
        overlay.strokes = [[(.1, .1), (.3, .1), (.3, .3)]]
        overlay.update_selection()
        overlay.clear()
        self.assertFalse(overlay.use_button.IsEnabled())
        overlay.finish(True)
        self.assertFalse(overlay.finished)
        overlay.finish(False)
        wx.Yield()
        self.assertEqual(results, [None])
        overlay = self.make_overlay(results)
        overlay.entire_view()
        wx.Yield()
        self.assertEqual(results[-1], [[(.02, .02), (.98, .02), (.98, .98), (.02, .98)]])


if __name__ == '__main__':
    unittest.main()
