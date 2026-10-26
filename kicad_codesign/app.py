"""Native Windows companion. KiCad remains the actual editor and undo owner."""
from . import desktop as desk  # Set DPI awareness before importing wx.
import wx
import wx.lib.scrolledpanel
from PIL import Image
from pathlib import Path
import io
import os
import json
import time
import threading
import subprocess
from datetime import datetime
from dataclasses import asdict, replace
from .providers import Connection, PROVIDERS, request_decision
from .protocol import Action

ROOT = Path(__file__).resolve().parent.parent
BG = "#F1F5F8"
INK = "#16283D"
ACCENT = "#117F86"

def bitmap(im):
    rgb = im.convert("RGB")
    return wx.Bitmap.FromBuffer(rgb.width, rgb.height, rgb.tobytes())

class Preview(wx.Panel):
    def __init__(self,parent):
        super().__init__(parent, size=(-1,150))
        self.image = None
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT,self.paint)
        self.Bind(wx.EVT_SIZE,lambda e:self.Refresh())
    def set_image(self, im): self.image=im; self.Refresh()
    def paint(self,e):
        dc=wx.AutoBufferedPaintDC(self); dc.SetBackground(wx.Brush("#172637")); dc.Clear()
        w,h=self.GetClientSize()
        if self.image:
            im=self.image.copy(); im.thumbnail((max(1,w-10),max(1,h-10)))
            dc.DrawBitmap(bitmap(im),(w-im.width)//2,(h-im.height)//2)
        else:
            dc.SetTextForeground("#CBD9E6"); dc.DrawText("Your circled selection appears here",18,max(8,h//2-8))

class CircleOverlay(wx.Frame):
    def __init__(self, owner, snapshot, callback):
        super().__init__(None,title="Circle a region — KiCAD Codesign",style=wx.FRAME_NO_TASKBAR|wx.STAY_ON_TOP|wx.BORDER_NONE)
        self.snapshot=snapshot; self.callback=callback; self.strokes=[]; self.drawing=False; self.finished=False
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT,self.paint)
        self.Bind(wx.EVT_LEFT_DOWN,self.down); self.Bind(wx.EVT_MOTION,self.motion); self.Bind(wx.EVT_LEFT_UP,self.up)
        self.Bind(wx.EVT_CHAR_HOOK,self.key)
        self.Bind(wx.EVT_CLOSE,lambda e:self.finish(False))
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST,lambda e:setattr(self,"drawing",False))
        l,t,r,b=snapshot.rect
        self.SetSize((r-l,b-t)); self.Show()
        desk.U.SetWindowPos(self.GetHandle(),-1,l,t,r-l,b-t,0x0040)
        self.Raise(); self.SetFocus()
    def point(self,e):
        w,h=self.GetClientSize(); return (max(0,min(1,e.GetX()/max(1,w))),max(0,min(1,e.GetY()/max(1,h))))
    def down(self,e):
        self.drawing=True; self.strokes.append([self.point(e)]); self.CaptureMouse()
    def motion(self,e):
        if self.drawing:
            self.strokes[-1].append(self.point(e)); self.Refresh()
    def up(self,e):
        if self.drawing:
            self.strokes[-1].append(self.point(e)); self.drawing=False
            if self.HasCapture(): self.ReleaseMouse()
            self.Refresh()
    def key(self,e):
        if e.GetKeyCode()==wx.WXK_ESCAPE: self.finish(False)
        elif e.GetKeyCode()==wx.WXK_RETURN and any(len(s)>2 for s in self.strokes): self.finish(True)
        elif e.GetKeyCode() in (wx.WXK_BACK,wx.WXK_DELETE): self.strokes=[]; self.Refresh()
        else: e.Skip()
    def paint(self,e):
        dc=wx.AutoBufferedPaintDC(self); w,h=self.GetClientSize()
        im=self.snapshot.image.resize((max(1,w),max(1,h)),Image.Resampling.BILINEAR)
        dc.DrawBitmap(bitmap(im),0,0)
        dc.SetPen(wx.Pen("#F6924A",4)); dc.SetBrush(wx.TRANSPARENT_BRUSH)
        for i,stroke in enumerate(self.strokes):
            pts=[wx.Point(round(x*w),round(y*h)) for x,y in stroke]
            if len(pts)>1:
                if not self.drawing or i < len(self.strokes)-1: pts.append(pts[0])
                dc.DrawLines(pts)
        dc.SetPen(wx.TRANSPARENT_PEN); dc.SetBrush(wx.Brush(INK)); dc.DrawRectangle(12,12,min(w-24,660),40)
        dc.SetTextForeground(wx.WHITE); dc.DrawText("Draw around anything  •  Enter: use selection  •  Backspace: clear  •  Esc: cancel",24,23)
    def finish(self,accepted):
        if self.finished:return
        self.finished=True
        if self.HasCapture():self.ReleaseMouse()
        self.Hide(); self.Destroy()
        wx.CallAfter(self.callback,self.snapshot,self.strokes if accepted else None)

class ConnectionDialog(wx.Dialog):
    def __init__(self,parent,connection,keys):
        super().__init__(parent,title="LLM connection",size=(560,430))
        self.keys=keys.copy(); self.old_provider=connection.provider
        box=wx.BoxSizer(wx.VERTICAL)
        def row(label,control):
            box.Add(wx.StaticText(self,label=label),0,wx.LEFT|wx.RIGHT|wx.TOP,12)
            box.Add(control,0,wx.EXPAND|wx.ALL,10)
            return control
        self.provider=row("Provider",wx.Choice(self,choices=list(PROVIDERS)))
        self.provider.SetStringSelection(connection.provider)
        self.model=row("Vision-capable model ID",wx.TextCtrl(self,value=connection.model))
        self.api_key=row("API key · kept only until this app closes",wx.TextCtrl(self,value=connection.api_key,style=wx.TE_PASSWORD))
        self.url=row("API base URL",wx.TextCtrl(self,value=connection.base_url))
        self.url.Enable(connection.provider=="OpenAI-compatible")
        self.provider.Bind(wx.EVT_CHOICE,self.changed)
        note=wx.StaticText(self,label="OpenAI and Claude use their official APIs. Compatible endpoints\nmust support images and function calling. API usage is billed by your provider.")
        box.Add(note,0,wx.ALL,12)
        box.Add(self.CreateButtonSizer(wx.OK|wx.CANCEL),0,wx.ALIGN_RIGHT|wx.ALL,12)
        self.SetSizerAndFit(box)
    def changed(self,e):
        self.keys[self.old_provider]=self.api_key.GetValue()
        name=self.provider.GetStringSelection(); p=PROVIDERS[name]
        self.model.SetValue(p.default_model); self.url.SetValue(p.default_url)
        self.api_key.SetValue(self.keys.get(name,os.getenv(p.env_key,"")))
        self.url.Enable(name=="OpenAI-compatible"); self.old_provider=name
    def connection(self):
        return Connection(self.provider.GetStringSelection(),self.model.GetValue().strip(),self.api_key.GetValue().strip(),self.url.GetValue().strip())

class CodesignFrame(wx.Frame):
    def __init__(self):
        super().__init__(None,title="KiCAD Codesign")
        self.SetSize(self.FromDIP((500,880)))
        self.SetMinSize(self.FromDIP((455,650)))
        self.connection=Connection(api_key=os.getenv("OPENAI_API_KEY",""))
        self.keys={}; self.editors=[]; self.editor=None; self.reference=None; self.strokes=[]
        self.busy=False; self.running=False; self.generation=0; self.pending=None; self.snapshot=None
        self.history=[]; self.steps=0; self.session=None; self.goal=""; self.overlay=None
        self.stop_event=threading.Event(); self.hotkey_id=wx.NewIdRef(); self.circle_id=wx.NewIdRef()
        panel=wx.lib.scrolledpanel.ScrolledPanel(self); panel.SetBackgroundColour(BG)
        outer=wx.BoxSizer(wx.VERTICAL)
        head=wx.Panel(panel); head.SetBackgroundColour(INK); hs=wx.BoxSizer(wx.VERTICAL)
        name=wx.StaticText(head,label="KiCAD Codesign"); name.SetForegroundColour(wx.WHITE)
        name.SetFont(wx.Font(23,wx.FONTFAMILY_SWISS,wx.FONTSTYLE_NORMAL,wx.FONTWEIGHT_BOLD))
        sub=wx.StaticText(head,label="CIRCLE IT. DESCRIBE IT. WATCH IT CHANGE."); sub.SetForegroundColour("#B5CEDB")
        hs.Add(name,0,wx.LEFT|wx.TOP,18); hs.Add(sub,0,wx.LEFT|wx.BOTTOM|wx.TOP,18); head.SetSizer(hs)
        outer.Add(head,0,wx.EXPAND)
        content=wx.BoxSizer(wx.VERTICAL)
        def label(s):
            t=wx.StaticText(panel,label=s); t.SetForegroundColour(INK)
            t.SetFont(wx.Font(10,wx.FONTFAMILY_SWISS,wx.FONTSTYLE_NORMAL,wx.FONTWEIGHT_BOLD)); content.Add(t,0,wx.TOP|wx.BOTTOM,7); return t
        top=wx.BoxSizer(wx.HORIZONTAL)
        self.provider_label=wx.StaticText(panel,label="OpenAI · gpt-5.4")
        top.Add(self.provider_label,1,wx.ALIGN_CENTER_VERTICAL)
        self.connect=wx.Button(panel,label="Connection…"); self.connect.Bind(wx.EVT_BUTTON,self.configure); top.Add(self.connect)
        content.Add(top,0,wx.EXPAND|wx.TOP,12)
        label("1   Choose the live editor")
        row=wx.BoxSizer(wx.HORIZONTAL)
        self.choice=wx.Choice(panel); self.choice.SetMinSize((60,-1)); self.choice.Bind(wx.EVT_CHOICE,self.selected)
        self.refresh_button=wx.Button(panel,label="Refresh",size=(75,-1)); self.refresh_button.Bind(wx.EVT_BUTTON,self.refresh_editors)
        row.Add(self.choice,1,wx.RIGHT,6); row.Add(self.refresh_button); content.Add(row,0,wx.EXPAND)
        self.arrange_button=wx.Button(panel,label="Arrange side by side"); self.arrange_button.Bind(wx.EVT_BUTTON,self.arrange)
        self.circle_button=wx.Button(panel,label="Circle in KiCad   Ctrl+Shift+Space"); self.circle_button.Bind(wx.EVT_BUTTON,self.circle)
        content.Add(self.arrange_button,0,wx.EXPAND|wx.TOP,6)
        content.Add(self.circle_button,0,wx.EXPAND|wx.TOP,6)
        self.preview=Preview(panel); content.Add(self.preview,0,wx.EXPAND|wx.TOP,8)
        self.selection_label=wx.StaticText(panel,label="Layout · Schematic · Footprint editor"); content.Add(self.selection_label,0,wx.TOP,5)
        label("2   Describe the change")
        self.prompt=wx.TextCtrl(panel,style=wx.TE_MULTILINE,size=(-1,78)); self.prompt.SetHint("For example: Set the circled resistor to 10k, then rotate it 90 degrees.")
        content.Add(self.prompt,0,wx.EXPAND)
        self.auto=wx.CheckBox(panel,label="Continue automatically (up to 40 actions)"); content.Add(self.auto,0,wx.TOP,8)
        row=wx.BoxSizer(wx.HORIZONTAL)
        self.ask_button=wx.Button(panel,label="Ask / Start"); self.ask_button.Bind(wx.EVT_BUTTON,self.start)
        self.step_button=wx.Button(panel,label="Apply next action"); self.step_button.Bind(wx.EVT_BUTTON,self.apply_pending)
        self.stop_button=wx.Button(panel,label="Stop"); self.stop_button.Bind(wx.EVT_BUTTON,self.stop)
        row.Add(self.ask_button,1,wx.RIGHT,5); row.Add(self.step_button,1,wx.RIGHT,5); row.Add(self.stop_button)
        content.Add(row,0,wx.EXPAND|wx.TOP,9)
        self.state=wx.StaticText(panel,label="Open a KiCad editor, then press Refresh.")
        self.state.SetForegroundColour(ACCENT); content.Add(self.state,0,wx.EXPAND|wx.TOP,9)
        label("3   Follow each action")
        self.log=wx.TextCtrl(panel,style=wx.TE_MULTILINE|wx.TE_READONLY|wx.TE_RICH2,size=(-1,175))
        content.Add(self.log,1,wx.EXPAND)
        row=wx.BoxSizer(wx.HORIZONTAL)
        self.undo_button=wx.Button(panel,label="Undo in KiCad"); self.undo_button.Bind(wx.EVT_BUTTON,self.undo)
        self.history_button=wx.Button(panel,label="History folder"); self.history_button.Bind(wx.EVT_BUTTON,self.open_history)
        self.demo_button=wx.Button(panel,label="Open demo"); self.demo_button.Bind(wx.EVT_BUTTON,self.open_demo)
        row.Add(self.undo_button,1,wx.RIGHT,5); row.Add(self.history_button,1,wx.RIGHT,5); row.Add(self.demo_button,1)
        content.Add(row,0,wx.EXPAND|wx.TOP,7)
        note=wx.StaticText(panel,label="Ask sends the reference + current editor view to your provider.\nLocal history includes screenshots. Save changes in KiCad.\nStop: Ctrl+Shift+F12, or hold Esc. Circle: Ctrl+Shift+Space.")
        note.SetForegroundColour("#566779"); content.Add(note,0,wx.TOP|wx.BOTTOM,10)
        outer.Add(content,1,wx.EXPAND|wx.LEFT|wx.RIGHT,15); panel.SetSizer(outer); panel.SetupScrolling(scroll_x=False)
        # wx logs a modal error on a shortcut conflict unless logging is suppressed here.
        with wx.LogNull():
            stop_hotkey=self.RegisterHotKey(self.hotkey_id,wx.MOD_CONTROL|wx.MOD_SHIFT,wx.WXK_F12)
            circle_hotkey=self.RegisterHotKey(self.circle_id,wx.MOD_CONTROL|wx.MOD_SHIFT,wx.WXK_SPACE)
        self.Bind(wx.EVT_HOTKEY,lambda e:self.stop() if e.GetId()==self.hotkey_id else self.circle())
        self.Bind(wx.EVT_CLOSE,self.close)
        self.timer=wx.Timer(self); self.Bind(wx.EVT_TIMER,self.tick,self.timer); self.timer.Start(80)
        self.refresh_editors(); self.controls()
        self.record("Ready. OpenAI and Claude connections are available. Circle a region directly over KiCad, then describe your change.")
        if not stop_hotkey or not circle_hotkey:self.record("A global shortcut is already in use. The Circle and Stop buttons still work; hold Esc to stop.")
    def record(self,message,event="info"):
        self.log.AppendText(f"{datetime.now():%H:%M:%S}  {message}\n\n")
        if self.session:
            try:
                with (self.session/"events.jsonl").open("a",encoding="utf-8") as f:
                    f.write(json.dumps({"time":datetime.now().isoformat(),"event":event,"message":message},ensure_ascii=False)+"\n")
            except OSError:
                self.log.AppendText("History could not be written to disk.\n")
    def status(self,text): self.state.SetLabel(text); self.state.Wrap(max(310,self.GetClientSize().width-45))
    def controls(self):
        locked=self.busy or self.running or self.pending is not None
        for c in (self.choice,self.refresh_button,self.connect,self.arrange_button,self.circle_button,self.demo_button): c.Enable(not locked)
        self.ask_button.Enable(not locked); self.prompt.Enable(not locked)
        self.step_button.Enable(self.pending is not None and not self.busy)
        self.stop_button.Enable(locked); self.undo_button.Enable(self.editor is not None and not locked)
    def configure(self,e=None):
        dlg=ConnectionDialog(self,self.connection,self.keys)
        if dlg.ShowModal()==wx.ID_OK:
            self.connection=dlg.connection(); self.keys=dlg.keys; self.keys[self.connection.provider]=self.connection.api_key
            self.provider_label.SetLabel(f"{self.connection.provider} · {self.connection.model}")
        dlg.Destroy()
    def refresh_editors(self,e=None):
        try:
            old=self.editor.hwnd if self.editor else None
            self.editors=desk.list_editors(); self.choice.Clear()
            for ed in self.editors:self.choice.Append(f"{ed.view}  |  {ed.title}")
            if self.editors:
                index=next((i for i,x in enumerate(self.editors) if x.hwnd==old),0)
                self.choice.SetSelection(index); self.selected()
            else:self.editor=None; self.status("Open a KiCad layout, schematic or footprint editor, then Refresh.")
        except Exception as ex:self.fail(ex)
    def selected(self,e=None):
        index=self.choice.GetSelection()
        if index==wx.NOT_FOUND:return
        chosen=self.editors[index]
        if self.editor is None or chosen.hwnd!=self.editor.hwnd:
            self.reference=None; self.strokes=[]; self.preview.set_image(None)
        self.editor=chosen; self.status(f"Connected to {chosen.view}. Circle a region to begin."); self.controls()
    def arrange(self,e=None):
        if not self.editor:return
        try:desk.arrange(self.editor,self.GetHandle()); self.reference=None; self.preview.set_image(None)
        except Exception as ex:self.fail(ex)
    def circle(self,e=None):
        if self.busy or self.running or self.pending or self.overlay:return
        if not self.editor:self.status("Open a KiCad editor and press Refresh first.");return
        try:
            snap=desk.capture(self.editor)
            if snap.hwnd!=self.editor.hwnd:raise desk.DesktopError("Close the KiCad dialog before circling the design.")
            self.overlay=CircleOverlay(self,snap,self.circled)
        except Exception as ex:self.fail(ex)
    def circled(self,snap,strokes):
        if not self:return
        self.overlay=None
        if strokes is not None:
            self.reference=snap; self.strokes=[s for s in strokes if len(s)>2]
            self.preview.set_image(desk.annotate(snap.image,self.strokes))
            self.selection_label.SetLabel(f"{self.editor.view} · {len(self.strokes)} circled region(s) · frozen reference")
            self.status("Selection ready. Describe the change and press Ask.")
        self.Raise(); self.prompt.SetFocus()
    def start(self,e=None):
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
        if not self.running:return
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
            self.busy=True; self.controls(); self.status(f"{self.connection.provider} is inspecting KiCad…")
            gen=self.generation; connection=replace(self.connection)
            def worker():
                try:decision=request_decision(connection,context,images); wx.CallAfter(self.received,gen,decision,None)
                except Exception as ex:wx.CallAfter(self.received,gen,None,ex)
            threading.Thread(target=worker,daemon=True).start()
        except Exception as ex:self.fail(ex)
    def received(self,gen,decision,error):
        if gen!=self.generation or not self.running:return
        self.busy=False
        if error:self.fail(error);return
        if decision.plan:self.record("Plan\n"+"\n".join(f"• {p}" for p in decision.plan),"plan")
        self.record(decision.summary,"decision")
        if decision.status in {"done","ask"}:
            self.running=False; self.pending=None; self.reference=None
            self.status("Model reports completion — inspect the result in KiCad." if decision.status=="done" else "Needs your input. Stop here, clarify, and circle again.")
            self.controls(); return
        self.pending=decision.action
        self.record("Next: "+decision.action.describe(),"proposed")
        self.status("Review the next action, then Apply. Stop cancels it.")
        self.controls()
        if self.auto.GetValue():
            self.status("Applying the next action in 1.2 seconds…")
            wx.CallLater(1200,lambda:self.apply_pending() if gen==self.generation and self.auto.GetValue() else None)
    def apply_pending(self,e=None):
        if self.pending is None or self.busy or not self.running:return
        action=self.pending; self.pending=None; self.busy=True; self.controls()
        try:
            snap=desk.verify_snapshot(self.editor,self.snapshot)
            desk.execute(self.editor,action,snap,self.stop_event.is_set)
            self.steps+=1; self.history.append({"step":self.steps,"action":action.to_dict(),"result":"Input sent; inspect current screenshot to verify its effect."})
            self.record(f"{self.steps:02d} · Sent: {action.describe()}","action")
            after=desk.capture(self.editor); after.image.save(self.session/f"{self.steps:03d}-after.png")
            self.preview.set_image(after.image); self.selection_label.SetLabel(f"{self.editor.view} · result after action {self.steps}")
            self.busy=False; self.controls(); gen=self.generation
            wx.CallLater(300,lambda:self.next_decision() if gen==self.generation else None)
        except Exception as ex:self.fail(ex)
    def stop(self,e=None):
        self.stop_event.set(); self.generation+=1; self.running=False; self.busy=False; self.pending=None
        self.status("Stopped. KiCad stays exactly where the last action left it.")
        self.record("Stopped. Pending model responses are ignored.","stop"); self.controls()
    def fail(self,error):
        self.stop_event.set(); self.generation+=1; self.running=False; self.busy=False; self.pending=None
        self.status(str(error)); self.record(str(error),"error"); self.controls()
    def tick(self,e):
        if (self.running or self.busy or self.pending) and desk.emergency_pressed():self.stop()
    def undo(self,e=None):
        try:
            snap=desk.capture(self.editor)
            if snap.hwnd!=self.editor.hwnd:raise desk.DesktopError("Close the property dialog before using Undo.")
            desk.execute(self.editor,Action(kind="key",key="ctrl+z"),snap)
            self.reference=None; self.preview.set_image(desk.capture(self.editor).image)
            self.record("Sent one native KiCad Undo. Circle the current view before your next request.","undo")
        except Exception as ex:self.fail(ex)
    def open_history(self,e=None):
        p=self.session or ROOT/"sessions"; p.mkdir(exist_ok=True); os.startfile(str(p))
    def open_demo(self,e=None):
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
        wx.CallLater(2500,self.refresh_editors)
    def close(self,e):
        self.stop_event.set(); self.generation+=1; self.running=False
        self.timer.Stop(); self.UnregisterHotKey(self.hotkey_id); self.UnregisterHotKey(self.circle_id)
        if self.overlay:self.overlay.finish(False)
        self.Destroy()

def main():
    app=wx.App(False)
    app.SetAppName("KiCAD Codesign")
    frame=CodesignFrame(); frame.Show(); app.MainLoop()

if __name__=="__main__":main()
