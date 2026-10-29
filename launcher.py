from __future__ import annotations
import hashlib, json, os, shutil, subprocess, webbrowser, zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP="LEDERG Launcher"
HOST, PORT="LEDERG05.aternos.me", 26820
PROFILE=Path("versions/Forge 1.12.2/Forge 1.12.2.json")
EXPECTED_SHA256="EC4ED88F9A2502B37EBC56C99F1E5F90B87FD023AFB8E4D0951E5C977158B34C"
MAX_ZIP, MAX_EXPANDED=3*1024**3, 5*1024**3

def data_dir():
    root=Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())/"LEDERG Launcher"
    root.mkdir(parents=True,exist_ok=True)
    return root

def drive_id(url):
    p=urlparse(url.strip())
    if (p.hostname or "").lower() not in {"drive.google.com","docs.google.com"}: return None
    import re
    m=re.search(r"/file/d/([A-Za-z0-9_-]+)",p.path)
    if m: return m.group(1)
    ident=parse_qs(p.query).get("id",[None])[0]
    return ident if ident and re.fullmatch(r"[A-Za-z0-9_-]+",ident) else None

def direct_url(url):
    ident=drive_id(url)
    if not ident: raise ValueError("Нужна ссылка Google Drive на ZIP-файл.")
    return f"https://drive.google.com/uc?export=download&id={ident}"

def sha256(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda:f.read(1024*1024),b""): h.update(part)
    return h.hexdigest().upper()

def validate_zip(path, expected=EXPECTED_SHA256):
    if not path.is_file() or path.stat().st_size<4 or path.stat().st_size>MAX_ZIP: raise ValueError("ZIP отсутствует, пустой или слишком большой.")
    if expected and sha256(path)!=expected.upper(): raise ValueError("SHA-256 не совпал с проверенной сборкой LEDERG.")
    with zipfile.ZipFile(path) as z:
        names=set(); total=0
        if not z.infolist(): raise ValueError("ZIP пуст.")
        for e in z.infolist():
            p=PurePosixPath(e.filename)
            if p.is_absolute() or ".." in p.parts or "\\" in e.filename or "\x00" in e.filename: raise ValueError(f"Небезопасный путь: {e.filename}")
            if ((e.external_attr>>16)&0xffff)&0o170000==0o120000: raise ValueError("Символические ссылки в ZIP запрещены.")
            total+=e.file_size
            if total>MAX_EXPANDED: raise ValueError("Распакованный клиент превышает 5 ГБ.")
            names.add(e.filename.lstrip("./"))
        if not any(x.endswith("TLauncher.exe") for x in names): raise ValueError("В ZIP не найден TLauncher.exe.")
        if not any(x.endswith("versions/Forge 1.12.2/Forge 1.12.2.json") for x in names): raise ValueError("В ZIP не найден профиль Forge 1.12.2.")
        if z.testzip(): raise ValueError("ZIP повреждён.")

def extract_zip(archive_path, destination):
    validate_zip(archive_path)
    if destination.exists(): raise FileExistsError("Папка клиента уже есть. Переименуй её вручную перед переустановкой.")
    root=destination.resolve(); root.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as z:
        for e in z.infolist():
            p=PurePosixPath(e.filename); out=(root/Path(*p.parts)).resolve()
            if out!=root and root not in out.parents: raise ValueError(f"Путь выходит из папки клиента: {e.filename}")
            if e.is_dir(): out.mkdir(parents=True,exist_ok=True)
            else:
                out.parent.mkdir(parents=True,exist_ok=True)
                with z.open(e) as src, out.open("wb") as dst: shutil.copyfileobj(src,dst,1024*1024)

def locate(root):
    for p in [root]+[x for x in root.iterdir() if x.is_dir()]:
        if (p/"TLauncher.exe").is_file() and (p/PROFILE).is_file(): return p
    raise ValueError("В распакованном клиенте нет TLauncher.exe или профиля Forge 1.12.2.")

def set_server(profile):
    obj=json.loads(profile.read_text(encoding="utf-8-sig")); args=obj.get("minecraftArguments")
    if not isinstance(args,str): raise ValueError("Некорректный Forge-профиль.")
    tokens=args.split()
    for flag,value in (("--server",HOST),("--port",str(PORT))):
        if flag in tokens:
            i=tokens.index(flag)
            if i+1<len(tokens): tokens[i+1]=value
            else: tokens.append(value)
        else: tokens += [flag,value]
    obj["minecraftArguments"]=" ".join(tokens)
    tmp=profile.with_suffix(".json.tmp"); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); tmp.replace(profile)

class App:
    def __init__(self):
        self.root=tk.Tk(); self.root.title("LEDERG STALKER | Launcher")
        self.root.geometry("740x500"); self.root.minsize(680,480); self.root.configure(bg="#080808")
        self.base=data_dir(); self.client=self.base/"client"
        try: self.saved=json.loads((self.base/"settings.json").read_text(encoding="utf-8"))
        except Exception: self.saved={}
        self.build()
    def build(self):
        s=ttk.Style()
        try:s.theme_use("clam")
        except tk.TclError:pass
        s.configure("Red.Horizontal.TProgressbar",troughcolor="#252525",background="#d71920",bordercolor="#252525")
        h=tk.Frame(self.root,bg="#0d0d0d",height=108); h.pack(fill="x"); h.pack_propagate(False)
        tk.Label(h,text="LEDERG",bg="#0d0d0d",fg="#e3262e",font=("Segoe UI",28,"bold")).pack(anchor="w",padx=26,pady=(14,0))
        tk.Label(h,text="STALKER ZONE / MINECRAFT FORGE 1.12.2",bg="#0d0d0d",fg="#b9b9b9",font=("Segoe UI",10,"bold")).pack(anchor="w",padx=29)
        b=tk.Frame(self.root,bg="#080808"); b.pack(fill="both",expand=True,padx=26,pady=17)
        tk.Label(b,text="ССЫЛКА НА КЛИЕНТ",bg="#080808",fg="#eee",font=("Segoe UI",14,"bold")).pack(anchor="w")
        tk.Label(b,text="Открой ссылку в браузере, скачай ZIP, затем выбери его кнопкой установки.",bg="#080808",fg="#a5a5a5",font=("Segoe UI",9)).pack(anchor="w",pady=(4,8))
        row=tk.Frame(b,bg="#080808"); row.pack(fill="x")
        self.url=tk.StringVar(value=self.saved.get("client_url",""))
        tk.Entry(row,textvariable=self.url,bg="#171717",fg="#eee",insertbackground="#e3262e",relief="flat").pack(side="left",fill="x",expand=True,ipady=10,padx=(0,8))
        tk.Button(row,text="СОХРАНИТЬ",command=self.save,bg="#292929",fg="white",relief="flat",padx=12,pady=8).pack(side="right")
        box=tk.Frame(b,bg="#111",highlightbackground="#272727",highlightthickness=1);box.pack(fill="x",pady=(15,13))
        tk.Label(box,text="СЕРВЕР",bg="#111",fg="#999",font=("Segoe UI",9,"bold")).pack(anchor="w",padx=14,pady=(9,2))
        tk.Label(box,text=f"{HOST}:{PORT}",bg="#111",fg="#eee",font=("Consolas",15,"bold")).pack(anchor="w",padx=14)
        tk.Label(box,text="Forge 1.12.2 • Java 8 • LEDERG Client",bg="#111",fg="#b7b7b7",font=("Segoe UI",9)).pack(anchor="w",padx=14,pady=(3,9))
        self.bar=ttk.Progressbar(b,style="Red.Horizontal.TProgressbar",maximum=100);self.bar.pack(fill="x",pady=(0,7))
        self.status=tk.StringVar(value="ГОТОВ К УСТАНОВКЕ")
        tk.Label(b,textvariable=self.status,bg="#080808",fg="#d71920",font=("Segoe UI",9,"bold"),anchor="w").pack(fill="x")
        row=tk.Frame(b,bg="#080808");row.pack(fill="x",pady=(12,0))
        tk.Button(row,text="СКАЧАТЬ В БРАУЗЕРЕ",command=self.open_link,bg="#b9141b",fg="white",relief="flat",pady=12,font=("Segoe UI",9,"bold")).pack(side="left",fill="x",expand=True,padx=(0,6))
        tk.Button(row,text="УСТАНОВИТЬ ZIP",command=self.install,bg="#292929",fg="white",relief="flat",pady=12,font=("Segoe UI",9,"bold")).pack(side="left",fill="x",expand=True,padx=(0,6))
        tk.Button(row,text="ИГРАТЬ ▶",command=self.play,bg="#282828",fg="white",relief="flat",pady=12,font=("Segoe UI",9,"bold")).pack(side="left",fill="x",expand=True)
        tk.Label(b,text="Архив проверяется по SHA-256. TLauncher может потребовать вход.",bg="#080808",fg="#717171",font=("Segoe UI",8)).pack(anchor="w",pady=(10,0))
    def save(self):
        val=self.url.get().strip()
        if val:
            try:direct_url(val)
            except ValueError as e:messagebox.showerror("Ссылка",str(e),parent=self.root);return False
        self.saved["client_url"]=val; p=self.base/"settings.json"; tmp=p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.saved,ensure_ascii=False,indent=2),encoding="utf-8");tmp.replace(p)
        self.status.set("ССЫЛКА СОХРАНЕНА");return True
    def open_link(self):
        if not self.save():return
        try:webbrowser.open(direct_url(self.url.get()))
        except Exception as e:messagebox.showerror("Ссылка",str(e),parent=self.root)
        self.status.set("ЗАГРУЗИ ZIP В БРАУЗЕРЕ, ЗАТЕМ НАЖМИ УСТАНОВИТЬ")
    def install(self):
        file=filedialog.askopenfilename(title="Выбери скачанный LEDERG client ZIP",filetypes=[("ZIP archive","*.zip")],parent=self.root)
        if not file:return
        try:
            validate_zip(Path(file)); self.status.set("РАСПАКОВКА…");self.root.update_idletasks()
            extract_zip(Path(file),self.client); client=locate(self.client);set_server(client/PROFILE)
            self.bar.configure(value=100);self.status.set("КЛИЕНТ УСТАНОВЛЕН")
            messagebox.showinfo("Готово","Клиент установлен. Нажми «Играть».",parent=self.root)
        except Exception as e:messagebox.showerror("Установка",str(e),parent=self.root);self.status.set("ОШИБКА УСТАНОВКИ")
    def play(self):
        try:
            client=locate(self.client);set_server(client/PROFILE)
            launcher=client/"TLauncher.exe"
            if os.name=="nt":os.startfile(str(launcher))
            else:subprocess.Popen([str(launcher)],cwd=str(client))
            self.status.set("TLAUNCHER ОТКРЫТ • FORGE 1.12.2")
        except Exception as e:messagebox.showerror("Запуск",str(e),parent=self.root)
    def run(self):self.root.mainloop()

if __name__=="__main__":App().run()
