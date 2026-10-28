"""Offline desktop toy. Reads only bundled artwork; no accounts or services."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

from sprite_art import load_skin

TITLE = '梅西玩偶 · 独立循环版'
CYCLE = ('idle', 'walk-right', 'ears', 'trophy', 'mail', 'bobo', 'walk-left', 'depleted')
LABELS = {'idle':'站一会儿', 'walk-right':'向右带球', 'ears':'捂耳庆祝',
          'trophy':'亲吻金杯', 'mail':'举起大信封', 'bobo':'说两句',
          'walk-left':'向左带球', 'depleted':'疑惑表情'}


class Playback:
    def __init__(self, now, interval=6):
        self.interval, self.started = interval, now
        self.index, self.automatic = 0, True

    @property
    def action(self):
        return CYCLE[self.index]

    def tick(self, now):
        if self.automatic and now-self.started >= self.interval:
            count = int((now-self.started)//self.interval)
            self.index = (self.index+count) % len(CYCLE)
            self.started += count*self.interval

    def next(self, now):
        self.index = (self.index+1) % len(CYCLE)
        self.started = now

    def choose(self, action, now):
        self.index, self.started, self.automatic = CYCLE.index(action), now, False

    def set_automatic(self, value, now):
        self.automatic, self.started = bool(value), now


def dpi_awareness():
    if sys.platform == 'win32':
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass


def work_area(root):
    if sys.platform == 'win32':
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),
                        ('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD)]
        user = ctypes.windll.user32
        user.GetAncestor.argtypes = [wintypes.HWND,wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.MonitorFromWindow.argtypes = [wintypes.HWND,wintypes.DWORD]
        user.MonitorFromWindow.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
        info=MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        hwnd=user.GetAncestor(root.winfo_id(),2)
        if user.GetMonitorInfoW(user.MonitorFromWindow(hwnd,2),ctypes.byref(info)):
            r=info.rcWork
            return r.left,r.top,r.right,r.bottom
    return 0,0,root.winfo_screenwidth(),root.winfo_screenheight()-48


def clamp(x,y,area,width,height):
    left,top,right,bottom=area
    return max(left,min(x,max(left,right-width))), max(top,min(y,max(top,bottom-height)))


def move(root,x,y):
    x,y=round(x),round(y)
    if sys.platform=='win32':
        user=ctypes.windll.user32
        user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT]
        user.GetAncestor.restype=wintypes.HWND
        user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,
                                   ctypes.c_int,ctypes.c_int,wintypes.UINT]
        if user.SetWindowPos(user.GetAncestor(root.winfo_id(),2),None,x,y,0,0,0x0015):
            return
    root.geometry(f'+{max(0,x)}+{max(0,y)}')


class DesktopToy:
    WIDTH, HEIGHT, KEY = 280, 238, '#ff00ff'

    def __init__(self, root, skin, args):
        self.root,self.skin,self.args=root,skin,args
        self.started=time.monotonic()
        self.playback=Playback(self.started,args.interval)
        self.previous=self.started
        self.closed=False
        self.drag=None
        self.menu_open=False
        self.pause_until=0
        self.hover=False
        self.position=None
        self.marker_time=0
        self.automatic=tk.BooleanVar(root,value=True)
        self.roaming=tk.BooleanVar(root,value=not args.no_roam)
        self.topmost=tk.BooleanVar(root,value=True)
        root.withdraw()
        root.title(TITLE)
        root.tk.call('tk','scaling',96/72)
        root.resizable(False,False)
        self.transparent=False
        if sys.platform=='win32':
            root.attributes('-transparentcolor',self.KEY)
            self.transparent=True
            root.iconbitmap(str(skin.path.parent/'messi.ico'))
        root.configure(bg=self.KEY if self.transparent else '#edf3f7')
        root.overrideredirect(self.transparent)
        root.attributes('-topmost',True)
        root.geometry(f'{self.WIDTH}x{self.HEIGHT}+80+180')
        fonts=set(tkfont.families(root))
        self.font=next((f for f in ('Microsoft YaHei UI','Segoe UI','Arial') if f in fonts),'TkDefaultFont')
        self.canvas=tk.Canvas(root,width=self.WIDTH,height=self.HEIGHT,
            bg=self.KEY if self.transparent else '#edf3f7',highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<ButtonPress-1>',self.press)
        self.canvas.bind('<B1-Motion>',self.dragging)
        self.canvas.bind('<ButtonRelease-1>',self.release)
        self.canvas.bind('<Button-3>',self.show_menu)
        root.bind('<Escape>',lambda _event:self.close())
        root.protocol('WM_DELETE_WINDOW',self.close)
        self.menu=tk.Menu(root,tearoff=False)
        self.menu.add_command(label='独立循环版 · 离线玩偶',state='disabled')
        self.menu.add_separator()
        self.menu.add_command(label='下一个动作',command=self.next_action)
        self.menu.add_checkbutton(label=f'自动切换动作（每 {args.interval:g} 秒）',variable=self.automatic,
            command=lambda:self.playback.set_automatic(self.automatic.get(),time.monotonic()))
        actions=tk.Menu(self.menu,tearoff=False)
        for action in CYCLE:
            actions.add_command(label=LABELS[action],command=lambda a=action:self.choose(a))
        self.menu.add_cascade(label='选择并保持动作',menu=actions)
        self.menu.add_checkbutton(label='带球时在桌面移动',variable=self.roaming)
        self.menu.add_checkbutton(label='保持置顶',variable=self.topmost,
            command=lambda:root.attributes('-topmost',self.topmost.get()))
        self.menu.add_separator()
        self.menu.add_command(label='退出玩偶',command=self.close)
        self.menu.bind('<Unmap>',lambda _event:self.menu_closed())
        root.update_idletasks()
        self.area=work_area(root)
        initial=args.position or (80,180)
        self.position=clamp(*initial,self.area,self.WIDTH,self.HEIGHT)
        move(root,*self.position)
        root.deiconify()
        self.animate()
        self.write_marker()
        if args.quit_after:
            root.after(round(args.quit_after*1000),self.close)

    def choose(self,action):
        self.playback.choose(action,time.monotonic())
        self.automatic.set(False)

    def next_action(self):
        self.playback.next(time.monotonic())

    def press(self,event):
        if self.hover and 258<=event.x<=278 and 3<=event.y<=25:
            self.close()
            return
        self.drag=(event.x_root-self.root.winfo_x(),event.y_root-self.root.winfo_y(),
                   event.x_root,event.y_root)

    def dragging(self,event):
        if self.drag:
            move(self.root,event.x_root-self.drag[0],event.y_root-self.drag[1])

    def release(self,event):
        if not self.drag:
            return
        clicked=abs(event.x_root-self.drag[2])+abs(event.y_root-self.drag[3])<5
        self.drag=None
        self.area=work_area(self.root)
        self.position=clamp(self.root.winfo_x(),self.root.winfo_y(),self.area,self.WIDTH,self.HEIGHT)
        move(self.root,*self.position)
        self.pause_until=time.monotonic()+2
        if clicked:
            self.show_menu(event)

    def show_menu(self,event):
        self.menu_open=True
        try:
            self.menu.tk_popup(event.x_root,event.y_root)
        finally:
            self.menu.grab_release()

    def menu_closed(self):
        self.menu_open=False
        self.pause_until=time.monotonic()+.5

    def draw_overlay(self,action):
        self.canvas.delete('overlay')
        message=self.skin.presentation.bubbles.get(action)
        if message:
            self.canvas.create_rectangle(16,6,258,43,fill='#fff9eb',outline='#42342e',width=2,tags='overlay')
            self.canvas.create_polygon(129,42,140,51,151,42,fill='#fff9eb',outline='',tags='overlay')
            self.canvas.create_text(137,25,text=message,font=(self.font,12,'bold'),fill='#42342e',tags='overlay')
        if self.hover:
            self.canvas.create_oval(258,3,278,25,fill='#fff9eb',outline='#42342e',tags='overlay')
            self.canvas.create_text(268,14,text='×',font=(self.font,12),fill='#42342e',tags='overlay')

    def animate(self):
        if self.closed:
            return
        now=time.monotonic()
        dt=min(.1,max(0,now-self.previous))
        self.previous=now
        self.playback.tick(now)
        action=self.playback.action
        px,py=self.root.winfo_pointerxy()
        x,y=px-self.root.winfo_x(),py-self.root.winfo_y()
        self.hover=0<=x<=self.WIDTH and 0<=y<=self.HEIGHT
        paused=self.hover or self.drag or self.menu_open or now<self.pause_until
        if self.roaming.get() and not paused and action in ('walk-left','walk-right'):
            self.area=work_area(self.root)
            direction=1 if action=='walk-right' else -1
            self.position=clamp(self.position[0]+direction*32*dt,self.position[1],
                                self.area,self.WIDTH,self.HEIGHT)
            move(self.root,*self.position)
        self.skin.draw(self.canvas,'normal',now-self.started,action=action,
            action_phase=now-self.playback.started,transparent_key=self.KEY if self.transparent else None)
        self.draw_overlay(action)
        if now-self.marker_time>=1:
            self.write_marker()
        self.root.after(80,self.animate)

    def write_marker(self):
        self.marker_time=time.monotonic()
        if self.args.ready_file:
            value={'pid':os.getpid(),'visible':not self.closed,'variant':'standalone-offline',
                   'action':self.playback.action,'automatic':self.playback.automatic,
                   'position':[self.root.winfo_x(),self.root.winfo_y()]}
            try:
                Path(self.args.ready_file).write_text(json.dumps(value),encoding='utf-8')
            except OSError:
                pass

    def close(self):
        if not self.closed:
            self.closed=True
            self.write_marker()
            self.root.destroy()


def acquire_instance():
    if sys.platform!='win32':
        return True
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
    kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateMutexW(None,False,'Local\\L2M_Messi_Standalone_Pet_v1')
    if not handle:
        raise OSError('Cannot create application instance lock')
    if ctypes.get_last_error()==183:
        kernel.CloseHandle(handle)
        return None
    return handle


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-roam',action='store_true')
    parser.add_argument('--position',nargs=2,type=int,metavar=('X','Y'))
    parser.add_argument('--interval',type=float,default=6,help='Seconds per action; default 6')
    parser.add_argument('--quit-after',type=float,help='Close after N seconds, for smoke checks')
    parser.add_argument('--ready-file',help='Optional local diagnostic marker')
    args=parser.parse_args()
    if not math.isfinite(args.interval) or args.interval<.1:
        parser.error('--interval must be finite and >= 0.1')
    if args.quit_after is not None and (not math.isfinite(args.quit_after) or args.quit_after<=0):
        parser.error('--quit-after must be finite and positive')
    handle=acquire_instance()
    if handle is None:
        return 0
    try:
        dpi_awareness()
        skin=load_skin(Path(__file__).resolve().parent/'assets/messi/skin.json')
        root=tk.Tk()
        DesktopToy(root,skin,args)
        root.mainloop()
    finally:
        if sys.platform=='win32':
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            kernel.CloseHandle(handle)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
