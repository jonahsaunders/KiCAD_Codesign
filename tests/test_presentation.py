import unittest
from kicad_codesign.presentation import controls_for, fit_window, arrangement_widths, validate_connection_fields, contrast_ratio
from kicad_codesign.providers import PROVIDERS, validate_base_url
from kicad_codesign.ui import LIGHT, DARK


class PresentationTests(unittest.TestCase):
    def state(self, **changes):
        values = dict(has_editor=True, has_selection=True, has_prompt=True, busy=False, running=False, pending=False)
        values.update(changes)
        return controls_for(**values)

    def test_missing_prerequisites_disable_start(self):
        for key in ('has_editor', 'has_selection', 'has_prompt'):
            with self.subTest(key=key):
                self.assertFalse(self.state(**{key: False}).can_ask)

    def test_ready_state(self):
        self.assertTrue(self.state().can_ask)
        self.assertFalse(self.state().can_apply)

    def test_stop_available_during_thinking(self):
        state = self.state(busy=True, running=True)
        self.assertTrue(state.can_stop)
        self.assertFalse(state.can_ask)
        self.assertFalse(state.can_select)

    def test_pending_action_requires_active_run(self):
        self.assertTrue(self.state(pending=True, running=True).can_apply)
        self.assertFalse(self.state(pending=True, running=False).can_apply)

    def test_overlay_locks_destructive_navigation(self):
        state = self.state(overlay=True)
        self.assertFalse(state.can_ask)
        self.assertFalse(state.can_undo)
        self.assertFalse(state.can_select)

    def test_window_fits_small_scaled_work_area(self):
        width, height = fit_window(900, 700, 2)
        self.assertLessEqual(width, 900)
        self.assertLessEqual(height, 700)

    def test_narrow_display_does_not_squeeze_editor(self):
        self.assertIsNone(arrangement_widths(1366, 1.5))
        left, right = arrangement_widths(2560, 1.5)
        self.assertEqual(left + right, 2560)
        self.assertGreaterEqual(left, 900)

    def validate(self, **changes):
        values = dict(provider='OpenAI', model='gpt-5.4', api_key='test-key', base_url='https://api.openai.com/v1')
        values.update(changes)
        return validate_connection_fields(**values, providers=PROVIDERS, validate_url=validate_base_url)

    def test_connection_errors_identify_field(self):
        self.assertEqual(self.validate(model='')[0], 'model')
        self.assertEqual(self.validate(api_key='')[0], 'api_key')
        self.assertEqual(self.validate(provider='OpenAI-compatible', base_url='http://example.com')[0], 'url')

    def test_local_provider_can_omit_key(self):
        self.assertIsNone(self.validate(provider='OpenAI-compatible', api_key='', base_url='http://localhost:11434/v1'))

    def test_text_colors_meet_normal_text_contrast(self):
        for palette in (LIGHT, DARK):
            for foreground in (palette.text, palette.secondary, palette.success, palette.warning, palette.error):
                for background in (palette.surface, palette.background):
                    with self.subTest(foreground=foreground, background=background):
                        self.assertGreaterEqual(contrast_ratio(foreground, background), 4.5)
            self.assertGreaterEqual(contrast_ratio(palette.accent_text, palette.accent), 4.5)


if __name__ == '__main__':
    unittest.main()
