"""JARVIS.exe — thin desktop launcher. Starts the packaged app (venv pythonw -m jarvis.app) with the right
working directory and no console window. Kept tiny so it builds in seconds and has no heavy imports."""
import ctypes
import os
import subprocess
import sys


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)))
    pyw = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(pyw):
        ctypes.windll.user32.MessageBoxW(None, f"Python runtime not found:\n{pyw}\n\nRun launcher\install.ps1 first.", "JARVIS", 0x10)
        return 1
    env = dict(os.environ, PYTHONUTF8="1", JARVIS_ROOT=root)
    subprocess.Popen([pyw, "-m", "jarvis.app"], cwd=root, env=env, creationflags=0x00000008 | 0x08000000)  # DETACHED | NO_WINDOW
    return 0


if __name__ == "__main__":
    sys.exit(main())
