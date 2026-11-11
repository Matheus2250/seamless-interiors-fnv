"""Compile the two SLGSStore scripts with a fake resolver and print SCDA hex plus an annotated listing."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scriptc  # noqa: E402

ENTER = """scn SLGSStoreEnterSCRIPT
float fx
float fy
float fz
float fa
Begin OnActivate
  if IsActionRef player == 1
    set fx to SLGSStoreDestInREF.GetPos x
    set fy to SLGSStoreDestInREF.GetPos y
    set fz to SLGSStoreDestInREF.GetPos z
    set fa to SLGSStoreDestInREF.GetAngle z
    player.SetPos x fx
    player.SetPos y fy
    player.SetPos z fz
    player.SetAngle z fa
  else
    Activate
  endif
End
"""

EXIT = ENTER.replace("SLGSStoreEnterSCRIPT", "SLGSStoreExitSCRIPT").replace("SLGSStoreDestInREF", "SLGSStoreDestOutREF")

FAKE = {"slgsstoredestinref": 0x01000801, "slgsstoredestoutref": 0x01000802}


def resolve(edid):
    return FAKE.get(edid.lower())


def main():
    for src in (ENTER, EXIT):
        res = scriptc.compile_script(src, resolve, script_type=0)
        print("=" * 72)
        print(res["edid"], "SCDA %d bytes" % len(res["scda"]))
        print("SCHR", res["schr"].hex(" "))
        print("SCRO", ["%08X" % x for x in res["scro"]], "SCRV", res["scrv"], "vars", res["vars"])
        print("SCDA hex:")
        h = res["scda"].hex(" ")
        for i in range(0, len(h), 48):
            print("   " + h[i:i + 48])
        print("Annotated:")
        for line in scriptc.annotate(res["scda"], res["scro"], res["scrv"], res["vars"]):
            print("   " + line)


if __name__ == "__main__":
    main()
