"""Native companion controller. KiCad owns the document and its undo history."""
from . import desktop as desk
import wx
from pathlib import Path
import io
import os
import json
import threading
import subprocess
from datetime import datetime
from dataclasses import replace
from .providers import Connection, PROVIDERS, request_decision
from .protocol import Action
from .dialogs import CircleOverlay, ConnectionDialog
from .layout import build_interface, build_menu
from .presentation import controls_for, fit_window
from .ui import enable_button, font, palette_for_system

ROOT = Path(__file__).resolve().parent.parent


class CodesignFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="KiCAD Codesign")
        self.theme = palette_for_system()
        self.SetBackgroundColour(self.theme.background)
        self.SetFont(font())
        index = wx.Display.GetFromWindow(self)
        work = wx.Display(index if index != wx.NOT_FOUND else 0).GetClientArea()
        self.SetSize(fit_window(work.width, work.height, self.GetDPIScaleFactor()))
        self.SetMinSize((min(self.FromDIP(440), work.width), min(self.FromDIP(600), work.height)))
        self.connection = Connection(api_key=os.getenv("OPENAI_API_KEY", ""))
        self.profiles = {}
        self.editors = []
        self.editor = self.reference = self.pending = self.snapshot = None
        self.strokes = []
        self.busy = self.running = self.closing = False
        self.generation = self.steps = 0
        self.history = []
        self.session = self.overlay = self.auto_delay = None
        self.goal = ""
        self.state_title, self.state_detail, self.state_tone = "Ready", "Choose a KiCad editor to start.", "normal"
        self.stop_event = threading.Event()
        self.hotkey_id, self.circle_id = wx.NewIdRef(), wx.NewIdRef()
        build_menu(self)
        build_interface(self)
        with wx.LogNull():
            stop_hotkey = self.RegisterHotKey(self.hotkey_id, wx.MOD_CONTROL | wx.MOD_SHIFT, wx.WXK_F12)
            circle_hotkey = self.RegisterHotKey(self.circle_id, wx.MOD_CONTROL | wx.MOD_SHIFT, wx.WXK_SPACE)
        self.registered_hotkeys = [identifier for identifier, registered in
                                   ((self.hotkey_id, stop_hotkey), (self.circle_id, circle_hotkey)) if registered]
        self.Bind(wx.EVT_HOTKEY, lambda e: self.stop() if e.GetId() == self.hotkey_id else self.circle())
        self.Bind(wx.EVT_CLOSE, self.close)
        self.Bind(wx.EVT_CHAR_HOOK, self.key)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.tick, self.timer)
        self.timer.Start(100)
        self.refresh_editors()
        self.controls()
        self.record("Welcome. Select a KiCad editor, circle a region, and describe your change.")
        if not stop_hotkey or not circle_hotkey:
            self.record("A global shortcut is already in use. Use the Circle region and Stop buttons, or hold Esc to stop.")

    def record(self,message,event="info"):
        self.log.AppendText(f"{datetime.now():%H:%M:%S}  {message}\n\n")
        if self.session:
            try:
                with (self.session/"events.jsonl").open("a",encoding="utf-8") as f:
                    f.write(json.dumps({"time":datetime.now().isoformat(),"event":event,"message":message},ensure_ascii=False)+"\n")
            except OSError:
                self.log.AppendText("History could not be written to disk.\n")
    def status(self, detail, title="Ready", tone="normal"):
        self.state_title, self.state_detail, self.state_tone = title, detail, tone
        self.state.update(title, detail, tone, self.steps, self.busy)
        self.root.Layout()

    def controls(self):
        if self.closing:
            return
        state = controls_for(has_editor=self.editor is not None, has_selection=self.reference is not None,
                             has_prompt=bool(self.prompt.GetValue().strip()), busy=self.busy, running=self.running,
                             pending=self.pending is not None, overlay=self.overlay is not None)
        for control in (self.choice, self.refresh_button, self.connect):
            control.Enable(state.can_select)
        self.connection_menu.Enable(state.can_select)
        self.GetMenuBar().Enable(wx.ID_OPEN, state.can_select)
        self.circle_button.Enable(state.can_circle)
        self.arrange_button.Enable(state.can_circle)
        enable_button(self.ask_button, state.can_ask)
        self.prompt.Enable(state.can_select)
        self.step_button.Show(self.pending is not None)
        self.ask_button.Show(self.pending is None)
        enable_button(self.step_button, state.can_apply)
        self.stop_button.Enable(state.can_stop)
        self.undo_button.Enable(state.can_undo)
        self.undo_menu.Enable(state.can_undo)
        self.action_panel.Show(self.pending is not None)
        self.state.update(self.state_title, self.state_detail, self.state_tone, self.steps, self.busy)
        self.root.Layout()

    def configure(self, event=None):
        if self.busy or self.running or self.overlay:
            return
        with ConnectionDialog(self, self.connection, self.profiles) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            self.connection = dialog.connection()
            self.profiles = dialog.profiles
        self.provider_label.SetLabel(f"{self.connection.provider} · {self.connection.model}")
        self.provider_label.SetToolTip(self.provider_label.GetLabel())
        self.status("Your connection is saved for this session.", "Connection ready", "success")
        self.controls()

    def clear_selection(self):
        self.reference = None
        self.strokes = []
        self.preview.set_image(None)
        self.selection_label.SetLabel("No region yet")

    def refresh_editors(self, event=None):
        if self.busy or self.running:
            return
        try:
            old = self.editor.hwnd if self.editor else None
            self.editors = desk.list_editors()
            self.choice.Clear()
            self.choice.Append("Select an open KiCad editor" if self.editors else "No KiCad editors found")
            for editor in self.editors:
                self.choice.Append(f"{editor.view}  ·  {editor.title}")
            index = next((i + 1 for i, editor in enumerate(self.editors) if editor.hwnd == old), 0)
            self.choice.SetSelection(index)
            if index:
                self.selected()
            else:
                self.editor = None
                self.clear_selection()
                self.status("Choose an editor above, or open a practice design from File." if self.editors else
                            "Open a design in KiCad, then Refresh. You can also open a practice design from File.",
                            "Choose your editor" if self.editors else "No editor connected")
                self.controls()
        except Exception as error:
            self.fail(error)

    def selected(self, event=None):
        index = self.choice.GetSelection() - 1
        if index < 0 or index >= len(self.editors):
            self.editor = None
            self.clear_selection()
            self.status("Choose an editor above to start.", "Choose your editor")
            self.controls()
            return
        chosen = self.editors[index]
        if self.editor is None or chosen.hwnd != self.editor.hwnd:
            self.clear_selection()
        self.editor = chosen
        self.choice.SetToolTip(chosen.title)
        self.status(f"Circle a region in the {chosen.view.lower()} editor to begin.", "Editor connected", "success")
        self.controls()

    def arrange(self, event=None):
        if not self.editor or self.running or self.busy:
            return
        try:
            desk.arrange(self.editor, self.GetHandle())
            self.clear_selection()
            self.status("Circle a region to continue.", "Workspace ready")
            self.controls()
        except Exception as error:
            self.fail(error)

    def circle(self,e=None):
        if self.busy or self.running or self.pending or self.overlay:return
        if not self.editor:self.status("Open a KiCad editor and press Refresh first.");return
        try:
            snap=desk.capture(self.editor)
            if snap.hwnd!=self.editor.hwnd:raise desk.DesktopError("Close the KiCad dialog before circling the design.")
            self.overlay=CircleOverlay(self,snap,self.circled)
            self.controls()
        except Exception as ex:self.fail(ex)
    def circled(self, snapshot, strokes):
        if self.closing or not self:
            return
        self.overlay = None
        if strokes is not None:
            self.reference, self.strokes = snapshot, strokes
            count = len(strokes)
            self.preview.set_image(desk.annotate(snapshot.image, strokes), f"{count} selected regions in {self.editor.view}")
            self.selection_label.SetLabel(f"{count} region{'s' if count != 1 else ''}")
            self.status("Describe the change, then choose Ask assistant.", "Selection ready", "success")
        self.Raise()
        self.controls()
        self.prompt.SetFocus()
        self.design.ScrollChildIntoView(self.prompt)

    def start(self,e=None):
        if self.busy or self.running or self.overlay:return
        if not self.editor or not self.reference:self.status("Circle a region in the selected KiCad editor first.");return
        if not self.prompt.GetValue().strip():self.status("Describe what you want changed.");return
        if not self.connection.model or (self.connection.provider!="OpenAI-compatible" and not self.connection.api_key):
            self.configure()
            if not self.connection.model or (self.connection.provider!="OpenAI-compatible" and not self.connection.api_key):return
        try:
            # The region must still refer to the view the user circled.
            desk.verify_snapshot(self.editor,self.reference)
            self.session=ROOT/"sessions"/datetime.now().strftime("%Y%m%d-%H%M%S-%f"); self.session.mkdir(parents=True)
            desk.annotate(self.reference.image,self.strokes).save(self.session/"selection.png")
            self.goal=self.prompt.GetValue().strip(); self.history=[]; self.steps=0; self.pending=None
            self.generation+=1; self.stop_event.clear(); self.running=True
            self.record(f"{self.connection.provider} / {self.connection.model}\nTask: {self.goal}","task")
            self.next_decision()
        except Exception as ex:self.fail(ex)
    def next_decision(self):
        if not self.running or self.closing:return
        if self.steps>=40:self.stop(); self.record("40-action limit reached. Inspect the editor and circle again to continue.");return
        try:
            self.snapshot=desk.capture(self.editor)
            self.snapshot.image.save(self.session/f"{self.steps:03d}-before.png")
            marked=desk.annotate(self.reference.image,self.strokes)
            stream=io.BytesIO(); marked.save(stream,"PNG")
            context=json.dumps({"task":self.goal,"editor":self.editor.view,"current_window":self.snapshot.title,
                "current_image_size":list(self.snapshot.image.size),"region_polygons_normalized_0_to_1":self.strokes,
                "actions_so_far":self.history,"actions_remaining":40-self.steps},ensure_ascii=False)
            images=[("Original user-circled reference (not current coordinates)",stream.getvalue()),("CURRENT editor or property dialog — act here",self.snapshot.png())]
            self.busy=True; self.controls(); self.status(f"{self.connection.provider} is inspecting your design. You can stop while it works.", "Thinking…", "working")
            gen=self.generation; connection=replace(self.connection)
            def worker():
                try:decision=request_decision(connection,context,images); wx.CallAfter(self.received,gen,decision,None)
                except Exception as ex:wx.CallAfter(self.received,gen,None,ex)
            threading.Thread(target=worker,daemon=True).start()
        except Exception as ex:self.fail(ex)
    def received(self,gen,decision,error):
        if self.closing or gen!=self.generation or not self.running:return
        self.busy=False
        if error:self.fail(error);return
        if decision.plan:self.record("Plan\n"+"\n".join(f"• {p}" for p in decision.plan),"plan")
        self.record(decision.summary,"decision")
        if decision.status in {"done","ask"}:
            self.running=False; self.pending=None; self.reference=None
            self.selection_label.SetLabel("Last captured view")
            self.preview.set_image(self.snapshot.image, "Last captured editor view")
            self.status("Inspect and save the result in KiCad." if decision.status=="done" else decision.summary[:220], "Ready for your review" if decision.status=="done" else "Your input is needed", "success" if decision.status=="done" else "warning")
            self.controls(); return
        self.pending=decision.action
        self.action_title.set_text(decision.action.describe()[:100])
        self.action_description.set_text(decision.summary[:220])
        self.record("Next: "+decision.action.describe(),"proposed")
        self.status("Review the proposed action, then choose Apply action.", "Your approval", "working")
        self.controls()
        self.schedule_automatic()
    def apply_pending(self,e=None):
        if self.pending is None or self.busy or not self.running:return
        self.cancel_automatic()
        action=self.pending; self.pending=None; self.busy=True
        self.status(action.describe()[:140], "Applying action…", "working"); self.controls()
        try:
            snap=desk.verify_snapshot(self.editor,self.snapshot)
            desk.execute(self.editor,action,snap,self.stop_event.is_set)
            self.steps+=1; self.history.append({"step":self.steps,"action":action.to_dict(),"result":"Input sent; inspect current screenshot to verify its effect."})
            self.record(f"{self.steps:02d} · Sent: {action.describe()}","action")
            after=desk.capture(self.editor); after.image.save(self.session/f"{self.steps:03d}-after.png")
            self.preview.set_image(after.image); self.selection_label.SetLabel(f"After action {self.steps}")
            self.busy=False; self.controls(); gen=self.generation
            wx.CallLater(300,lambda:self.next_decision() if gen==self.generation else None)
        except Exception as ex:self.fail(ex)
    def stop(self, event=None):
        if self.closing or not (self.running or self.busy or self.pending):
            return
        self.stop_event.set()
        self.cancel_automatic()
        self.generation += 1
        self.running = self.busy = False
        self.pending = None
        if self.steps:
            self.reference = None
            self.selection_label.SetLabel("Last captured view")
        self.status("Changes already made remain in KiCad. Use Undo if needed, then select a region to continue.", "Stopped")
        self.record("Stopped. Pending model responses will be ignored.", "stop")
        self.controls()

    def fail(self, error):
        if self.closing:
            return
        self.stop_event.set()
        self.cancel_automatic()
        self.generation += 1
        self.running = self.busy = False
        self.pending = None
        if isinstance(error, desk.DesktopError):
            self.reference = None
            self.selection_label.SetLabel("Select a new region")
        self.status(str(error), "Needs attention", "error")
        self.record(str(error), "error")
        self.controls()

    def tick(self, event):
        if self.busy:
            self.state.gauge.Pulse()
        if (self.running or self.busy or self.pending) and desk.emergency_pressed():
            self.stop()

    def key(self, event):
        if event.GetKeyCode() == wx.WXK_RETURN and event.ControlDown():
            self.apply_pending() if self.pending else self.start()
        elif event.GetKeyCode() == wx.WXK_ESCAPE and self.running:
            self.stop()
        else:
            event.Skip()

    def cancel_automatic(self):
        if self.auto_delay:
            self.auto_delay.Stop()
            self.auto_delay = None

    def schedule_automatic(self):
        self.cancel_automatic()
        if self.auto.GetValue() and self.pending is not None and self.running:
            generation = self.generation
            self.status("The action will run in 1.2 seconds. Choose Stop or turn off automatic mode to pause.", "Automatic mode", "working")
            self.auto_delay = wx.CallLater(1200, lambda: self.apply_pending()
                                           if generation == self.generation and self.auto.GetValue() else None)

    def automatic_changed(self, event):
        if self.pending is not None:
            if self.auto.GetValue():
                self.schedule_automatic()
            else:
                self.cancel_automatic()
                self.status("Review the proposed action, then choose Apply action.", "Your approval", "working")

    def shortcuts(self, event=None):
        wx.MessageBox("Circle: Ctrl+Shift+Space\nAsk or apply: Ctrl+Enter\nStop: Ctrl+Shift+F12 or hold Esc\nConnection: Ctrl+,\n\nSelection overlay\nKeep: Enter\nClear: Backspace\nCancel: Esc\nSelect without drawing: Tab to Entire view, then Space",
                      "Keyboard shortcuts", wx.OK | wx.ICON_INFORMATION, self)

    def undo(self,e=None):
        if not self.editor or self.running or self.busy or self.overlay:return
        try:
            snap=desk.capture(self.editor)
            if snap.hwnd!=self.editor.hwnd:raise desk.DesktopError("Close the property dialog before using Undo.")
            desk.execute(self.editor,Action(kind="key",key="ctrl+z"),snap)
            self.reference=None; self.preview.set_image(desk.capture(self.editor).image)
            self.record("Sent one native KiCad Undo. Circle the current view before your next request.","undo")
            self.selection_label.SetLabel("After Undo")
            self.status("Circle the current view before your next request.", "Undo sent")
            self.controls()
        except Exception as ex:self.fail(ex)
    def open_history(self,e=None):
        p=self.session or ROOT/"sessions"; p.mkdir(exist_ok=True); os.startfile(str(p))
    def open_demo(self,e=None):
        if self.running or self.busy or self.overlay:return
        # Only opens the included disposable project; no model-supplied file paths.
        candidates=sorted(Path(os.environ.get("ProgramFiles",r"C:\Program Files")).glob("KiCad/*/bin/pcbnew.exe"),reverse=True)
        if not candidates:self.status("KiCad was not found under Program Files.");return
        options=["PCB layout", "Schematic"]
        with wx.SingleChoiceDialog(self,"Open the included practice design. For Footprint view, use KiCad’s Footprint Editor toolbar button.","Practice project",options) as d:
            if d.ShowModal()!=wx.ID_OK:return
            board=d.GetSelection()==0
        exe=candidates[0] if board else candidates[0].with_name("eeschema.exe")
        path=ROOT/"demo"/("Codesign-Practice.kicad_pcb" if board else "Codesign-Practice.kicad_sch")
        subprocess.Popen([str(exe),str(path)],cwd=str(path.parent))
        self.status("Finish any KiCad setup dialog, then press Refresh.")
        wx.CallLater(2500,lambda:self.refresh_editors() if not self.closing else None)
    def close(self, event):
        self.closing = True
        self.stop_event.set()
        self.cancel_automatic()
        self.generation += 1
        self.running = False
        self.timer.Stop()
        for identifier in self.registered_hotkeys:
            self.UnregisterHotKey(identifier)
        if self.overlay:
            self.overlay.finish(False)
        self.Destroy()

def main():
    app=wx.App(False)
    app.SetAppName("KiCAD Codesign")
    frame=CodesignFrame(); frame.Show(); app.MainLoop()

if __name__=="__main__":main()
