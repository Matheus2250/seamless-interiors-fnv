"""Stable formID allocation: the same logical object keeps the same formID across builds, so a
new version of the plugin does not break saves made with an older one.

Keys are strings such as "GSGeneralStore:copy:0010801C". The registry lives in
tools/formids.json and is only ever appended to.
"""

import json
import os

FIRST = 0x01000800


class Registry:
    def __init__(self, path):
        self.path = path
        self.ids = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.ids = {k: int(v, 16) for k, v in json.load(fh).items()}
        self.used = set()

    def get(self, key):
        if key not in self.ids:
            self.ids[key] = max(self.ids.values(), default=FIRST - 1) + 1
        self.used.add(key)
        return self.ids[key]

    def next_object_id(self):
        """One past the highest formID this build actually wrote. Using the whole registry here
        would make the release ESP change every time an experiment allocates an id."""
        return max((self.ids[k] for k in self.used), default=FIRST - 1) + 1

    def save(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({k: "%08X" % v for k, v in sorted(self.ids.items(), key=lambda kv: kv[1])},
                      fh, indent=1)
