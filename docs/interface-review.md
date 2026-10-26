# Interface review

Reviewed on Windows with KiCad 10.0.6, Python 3.11.5, and wxPython 4.2.2.

## Changes

- Established consistent spacing, system typography, readable colors, and clear primary actions.
- Separated Design from Activity and kept Stop, Undo, and action review in a fixed footer.
- Added explicit editor selection, useful empty states, and disabled actions until prerequisites are available.
- Added visible selection controls and a keyboard-only Entire view path.
- Kept the selection toolbar separate from the drawing canvas so it cannot fill and hide the editor image.
- Made connection errors appear beside their fields, preserved per-provider drafts, and made additional fields scroll when space is constrained.
- Corrected multiline text measurement so help text and action explanations remain visible after resizing.
- Added native menus, keyboard shortcuts, accessible control names, and muted disabled primary buttons.
- Protected late callbacks after cancellation or closure and rejected side-by-side arrangements that make either window unusable.

## Verification

The 44 automated tests pass. Native widget checks cover 440 × 600, 500 × 960, and 760 × 900 logical window sizes; text wrapping; provider drafts and validation; selection drawing, acceptance, clearing, cancellation, and Entire view; and ignored late responses after Stop.

Native screenshots were inspected for the empty workspace, selected region, connection form, validation error, compatible endpoint, pending action, compact action review, and stopped state. The final rendering was done on a separate noninteractive Windows desktop without pointer input, active-window changes on the working desktop, or access to open user designs.

The screenshots use actual application widgets with deterministic example data. The board image in `tests/fixtures/practice-board.png` comes from the included PCB, exported with KiCad's SVG plotter using F.Cu, F.Silkscreen, and Edge.Cuts, then rasterized on a dark background. The proposed action is a scripted fixture. Client-area capture omits operating-system window chrome.

`tools/capture_interface.py` reproduces the captures and records wrapped-text geometry in `build/interface-review/`. It blocks live editor capture and input. `tools/run_tests.py` runs the complete suite on the same kind of isolated desktop.

## Remaining validation

Live paid-provider editing, complete schematic and footprint workflows, screen-reader walkthroughs, and the full range of display scaling and operating-system contrast settings remain unverified. Palette contrast tests are not a substitute for a complete accessibility assessment. Native input actions still execute on the interface thread, so Stop may wait for a short action to complete; the input adapter checks the emergency key during execution.
