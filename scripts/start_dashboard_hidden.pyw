"""Start Job Letter Studio without opening a terminal window.

The optional safe-test mode serves the dashboard with a disposable empty
database and profile. It never opens the user's data or starts external work.
"""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8765


def show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            None,
            message,
            "Job Letter Studio non si è avviato",
            0x10 | 0x1000,
        )


def run_safe_test(port: int) -> int:
    """Serve an empty dashboard using data only under a disposable temp dir."""
    from linkedin_connector.dashboard import DashboardHTTPServer
    from linkedin_connector.database import JobStore

    with tempfile.TemporaryDirectory(prefix="job-letter-studio-safe-") as temp_dir:
        data_dir = Path(temp_dir)
        profile_dir = data_dir / "profile"
        profile_dir.mkdir()
        store = JobStore(data_dir / "jobs.sqlite3")
        server = DashboardHTTPServer(
            ("127.0.0.1", port), store, profile_directory=profile_dir
        )
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            server.server_close()
    return 0


def wait_for_dashboard(process: subprocess.Popen[bytes], port: int, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (OSError, URLError):
            time.sleep(0.2)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--safe-test", type=int, metavar="PORT")
    args = parser.parse_args()
    if args.safe_test is not None:
        if not 1 <= args.safe_test <= 65535:
            parser.error("--safe-test must be a TCP port between 1 and 65535")
        return run_safe_test(args.safe_test)

    python = ROOT / ".venv" / "Scripts" / "python.exe"
    entrypoint = ROOT / "main.py"
    if not python.is_file() or not entrypoint.is_file():
        show_error(
            "Manca l'ambiente Python del progetto. Apri la cartella di Job Letter "
            "Studio e installa i requisiti del progetto, poi riprova."
        )
        return 1

    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        process = subprocess.Popen(
            [str(python), str(entrypoint), "--dashboard"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startup,
            creationflags=flags,
        )
    except OSError:
        show_error(
            "Non riesco ad avviare Job Letter Studio. Controlla l'ambiente Python "
            "del progetto e consulta Impostazioni → Diagnostica e log."
        )
        return 1

    if wait_for_dashboard(process, DEFAULT_PORT):
        return 0
    exit_code = process.poll()
    if exit_code is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
    show_error(
        "La dashboard locale non ha risposto entro 12 secondi. "
        f"Codice di uscita: {exit_code if exit_code is not None else 'timeout'}. "
        "Controlla Impostazioni → Diagnostica e log e verifica che la porta "
        f"{DEFAULT_PORT} sia disponibile."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
