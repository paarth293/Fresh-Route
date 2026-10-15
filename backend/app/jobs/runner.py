"""Isolated native-solver process used by the local persisted job queue."""
import argparse
import os
import threading
from app.jobs.worker import execute_job_synchronously


def watch_parent(parent_pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        handle = kernel.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            os._exit(75)
        def alive():
            return kernel.WaitForSingleObject(handle, 0) == 258
    else:
        def alive():
            return os.getppid() == parent_pid
    if not alive():
        os._exit(75)
    def monitor():
        tick = threading.Event()
        while alive():
            tick.wait(.2)
        os._exit(75)
    threading.Thread(target=monitor, daemon=True, name='worker-parent-watch').start()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    args = parser.parse_args()
    watch_parent(args.parent_pid)
    execute_job_synchronously(args.job_id, db_path=args.database)
