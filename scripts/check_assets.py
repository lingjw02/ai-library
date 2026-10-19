import os
import sqlite3
import glob

def check_db(path):
    if not os.path.exists(path):
        return "Not found"
    try:
        conn = sqlite3.connect(path)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        res = {}
        for t in tables:
            cnt = conn.execute(f"SELECT count(*) FROM {t[0]}").fetchone()[0]
            res[t[0]] = cnt
        return res
    except Exception as e:
        return str(e)

print("=== Antigravity v1 summary db ===")
print(check_db(r"C:\Users\ling5\.gemini\antigravity\conversation_summaries.db"))

print("\n=== CodEx Archives ===")
codex_dir = r"C:\Users\ling5\.codex"
if os.path.exists(codex_dir):
    print("Files in .codex:", len(os.listdir(codex_dir)))
    dirs = [d for d in os.listdir(codex_dir) if os.path.isdir(os.path.join(codex_dir, d))]
    print("Subdirs in .codex:", dirs)

print("\n=== Project Directories on D: ===")
for p in glob.glob(r"D:\*"):
    print("D root:", p)

print("\n=== Antigravity CLI Conversation DBs ===")
cli_dbs = glob.glob(r"C:\Users\ling5\.gemini\antigravity-cli\conversations\*.db")
print("Total .db files:", len(cli_dbs))
if cli_dbs:
    c = sqlite3.connect(cli_dbs[0])
    tbls = [t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("Sample DB tables:", tbls)
    for t in tbls:
        cnt = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {cnt}")


