"""Minimal DeepSeek client used as a helper agent (code review, bulk mechanical tasks).

The key comes from DEEPSEEK_API_KEY (process env, or the user's registry Environment key on
Windows when the app was not restarted). It is never printed.

Usage:
  py -I deepseek.py --prompt-file task.md [--file a.py --file b.py] [--out answer.md]
"""

import argparse
import json
import os
import sys
import urllib.request

API = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"      # the user's choice: v4 flash, maximum effort
EFFORT = "max"


def api_key():
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key and sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            key = winreg.QueryValueEx(k, "DEEPSEEK_API_KEY")[0]
    if not key:
        raise SystemExit("DEEPSEEK_API_KEY not set")
    return key


def ask(prompt, system="You are a careful senior engineer.", timeout=600):
    body = {"model": MODEL, "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": prompt}],
            "reasoning_effort": EFFORT}
    req = urllib.request.Request(API, data=json.dumps(body).encode("utf-8"),
                                 headers={"Authorization": "Bearer " + api_key(),
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    usage = data.get("usage", {})
    return data["choices"][0]["message"]["content"], usage


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    prompt = open(args.prompt_file, encoding="utf-8").read()
    for path in args.file:
        prompt += "\n\n=== FILE %s ===\n%s" % (os.path.basename(path), open(path, encoding="utf-8").read())
    answer, usage = ask(prompt)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(answer)
    print(answer)
    print("\n[usage] %s" % json.dumps(usage), file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
