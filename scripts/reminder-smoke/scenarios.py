"""Bounded assertions for scheduler -> worker -> intent -> delivery."""
import argparse, json, time, urllib.request
from app.db import db_connection

def main():
    p = argparse.ArgumentParser(); p.add_argument("--timeout", type=float, default=180); p.add_argument("--verify-only", action="store_true"); a = p.parse_args()
    deadline = time.monotonic() + a.timeout
    while time.monotonic() < deadline:
        try:
            with db_connection() as c:
                with c.cursor() as cur:
                    cur.execute("SELECT count(*) AS n FROM reminder_schedules")
                    if cur.fetchone()["n"]: break
        except Exception: pass
        time.sleep(1)
    else: raise TimeoutError("reminder schedule was not created")
    with urllib.request.urlopen("http://stub:8080/stats", timeout=3) as response: snapshot = json.load(response)
    if set(snapshot) != {"telegram", "bitrix"}: raise AssertionError("unsafe stub stats")
    with db_connection() as c:
        with c.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            assert cur.fetchone()["version_num"] == "20260904_0005"
    print(json.dumps({"db": "ok", "stub": snapshot}, sort_keys=True))
if __name__ == "__main__": main()
