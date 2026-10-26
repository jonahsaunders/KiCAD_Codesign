# KiCAD Codesign

A Windows desktop companion for asking an LLM to edit a visible KiCad design. Circle a region over the actual editor, describe the change, and follow the assistant's proposed mouse and keyboard actions in KiCad.

**Status: early prototype.** The native interface and provider adapters are implemented. Automated tests cover the action protocol, provider request/response formats, a local mock endpoint, and view-change checks. Full live editing with OpenAI or Claude has not yet been verified. Start with the included practice designs.

## What it does

- Select a running **PCB layout, schematic, or footprint editor** window.
- Draw one or more freehand regions over a frozen snapshot aligned with the editor.
- Send your instruction, circled reference, and current editor image to **OpenAI**, **Claude**, or an **OpenAI-compatible** endpoint.
- Review and apply one action at a time, or enable automatic continuation with a 40-action limit.
- Watch each action in the real KiCad editor, with a fresh screenshot after each action and a local action log.
- Stop pending work and use KiCad's native Undo.

This version controls KiCad through Windows screenshots and mouse/keyboard input. It does not use KiCad's PCB IPC API or parse the live electrical design into objects. The three editor types use the same visual-control adapter; complete workflows in all three still need validation. It is not an embedded KiCad panel or a continuous video stream.

## Start on Windows

1. Install KiCad 10 under its normal `Program Files\KiCad` location. The app uses KiCad's bundled Python, wxPython, and Pillow.
2. Download this repository as a ZIP and extract it, or clone it.
3. Double-click **`Start KiCAD Codesign.cmd`**.
4. Open a design in KiCad. In Codesign, press **Refresh** and select the editor you want to work in.
5. Choose **Connection…**, select your provider, enter an API key and a vision-capable model ID, then press **OK**.

The defaults are `gpt-5.4` for OpenAI and `claude-sonnet-4-6` for Claude. Model IDs are editable. You need API access for the selected provider; this app does not connect through the ChatGPT or Claude desktop subscription.

If you use a separate Python installation, install Python 3.11 or newer and run:

```powershell
python -m pip install -r requirements.txt
python launch.py
```

The desktop adapter currently requires Windows. Linux and macOS are not implemented.

## Circle, explain, and edit

1. Press **Arrange side by side** to place KiCad beside the companion.
2. Press **Circle in KiCad** or **Ctrl+Shift+Space**. Draw around the target. Press **Enter** to keep the region, **Backspace** to clear it, or **Esc** to cancel.
3. Describe the desired change. For example: “Set the circled resistor's value to 10k. Leave its position unchanged.”
4. Press **Ask / Start**. Review the explanation and proposed action, then press **Apply next action**. Each applied action is followed by another model inspection.
5. Optionally enable **Continue automatically**. There is a 1.2-second preview before each automatically applied action.
6. Inspect the result and save it yourself in KiCad. **Undo in KiCad** sends one native undo, not a rollback of the whole task.

Use **Stop**, **Ctrl+Shift+F12**, or hold **Esc** to stop. A request already sent to an LLM may finish at the provider, but its response is ignored after Stop. Mouse/keyboard actions already applied remain in KiCad. A global shortcut can be unavailable if another app has registered it; the buttons remain available.

Choose a different editor from the dropdown to work in another view. Switching editors clears the selection. For footprint editing, first open KiCad's Footprint Editor, load the desired footprint, and press Refresh in Codesign.

## Practice project

**Open demo** opens either `demo/Codesign-Practice.kicad_pcb` or `demo/Codesign-Practice.kicad_sch`. Both contain standalone R1, R2, and C1 practice objects. These documents are not a routed or electrically complete circuit. Finish any KiCad startup dialogs, then select the practice editor from the dropdown.

## Connections and local data

- OpenAI uses the Responses API; Claude uses the Messages API.
- API keys are held in memory until the app closes. You can also set `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `LLM_API_KEY` in your environment. Environment-provided credentials are managed by your environment, not saved by this app.
- Pressing Ask sends the circled reference image, current selected editor/dialog image, prompt, and action history to the chosen provider. The image may contain the whole selected editor window, not just the circle.
- Local history is saved under `sessions/`, including prompts, model summaries, and before/after screenshots. **History folder** opens that directory. It is excluded from Git.
- Remote compatible endpoints require HTTPS. HTTP is permitted only for localhost. Compatible models must support both image inputs and function calling.

## Current limitations

- Visual automation can misidentify objects. Exact values should be entered through KiCad property fields. A completed model response is not ERC, DRC, or electrical-design verification.
- Keep the selected editor visible and avoid changing it during a run. The app checks window identity, geometry, and material image changes before input; this is a heuristic and cannot detect every small change.
- Text entry requires a recognized, focused Windows text control. Custom widgets, unfamiliar dialogs, localized window titles, or custom KiCad shortcuts may need manual intervention.
- Model actions are restricted to a small validated input vocabulary, and input is scoped to the selected KiCad window and owned dialogs. This is a guardrail, not a complete security boundary against every operation available through KiCad's UI.
- Saving, opening documents, and changing preferences are intended to remain user-controlled. Files are not automatically backed up by this app; screenshots are interaction records, not restorable design backups.
- Running KiCad and Codesign at different Windows privilege levels can block input.

## Development

```text
launch.py                 Desktop entry point
kicad_codesign/app.py      Interface, selection overlay, and action loop
kicad_codesign/desktop.py  Windows capture, window scoping, and input adapter
kicad_codesign/protocol.py Validated provider-independent decision format
kicad_codesign/providers.py OpenAI, Claude, and compatible API connectors
tests/test_core.py        Protocol, provider, and screenshot-validation tests
```

From the repository root, run the tests with KiCad's Python:

```powershell
& "C:\Program Files\KiCad\10.0\bin\python.exe" -m unittest discover -s tests -v
```

The 26 tests do not require an API key. One test starts a temporary localhost-only HTTP server. Tests import the Windows desktop adapter and therefore require Windows and Pillow.

To add a provider, implement `build_request(connection, context, images)` and `decode_response(body)` in a provider class, provide `default_url`, `default_model`, and `env_key`, then register it in `PROVIDERS`. Return a validated `Decision` through `parse_decision`; provider code must not execute model-generated code.

## References

- [KiCad IPC API coverage](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/)
- [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision)
- [Claude vision](https://platform.claude.com/docs/en/build-with-claude/vision)
- [Claude tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)

KiCAD Codesign is an independent project, not an official KiCad, OpenAI, or Anthropic product. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for practice-design library attribution.
