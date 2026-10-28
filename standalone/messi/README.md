# Messi standalone source

[Five-language user guides](../../docs/messi/README.md)

This app loads bundled artwork and loops eight poses. It has no Codex, Gmail, account, quota, or network integration. The `skin_events.py` metadata reader is reused by the sprite loader; live-state selectors are not called here.

Run the source with Python 3.10+ and Tk after installing Pillow:

```powershell
python -m pip install Pillow
python standalone_pet.py
```

Build on Windows from this folder (Python 3.14.7 was used for the published executable):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name Messi-Offline --icon assets/messi/messi.ico --add-data "assets;assets" standalone_pet.py
```

The output is `dist/Messi-Offline.exe`. It includes Python, Tk, Pillow and the local skin. The published download also includes the five language guides. Build paths and private test markers are not shipped.
