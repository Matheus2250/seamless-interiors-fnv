"""Apply the FNV 4GB Patch (Nexus 62552, v1.4) to the Steam FalloutNV.exe.

Same byte patches as the published FalloutNVpatch.cpp, applied here instead of running its exe:
large-address-aware flag, new entry point, and a stub in the SteamStub section that loads
nvse_steam_loader.dll. Keeps FalloutNV_backup.exe. Usage: py -I patch4gb.py [--restore]
"""
import hashlib, os, shutil, struct, sys

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas"
VERSIONS = {"D068F394521A67C6E74FE572F59BD1BE71E855F3": 0, "07AFFDA66C89F09B0876A50C77759640BC416673": 0,
            "3980940522F0264ED9AF14AEA1773BB19F5160AB": 1, "F65049B0957D83E61ECCCACC730015AE77FB4C8B": 1,
            "5394B94A18FFA6FA846E1D6033AD7F81919F13AC": 2, "ACA83D5A12A64AF8854E381752FE989692D46E04": 2}
P3 = [bytes([0x3E, 0xF9, 0xFC]), bytes([0x88, 0xC4, 0xFC]), bytes([0x15, 0x43, 0xFD])]
P8 = [b"2238", b"2238", b"2249"]
P6 = bytes.fromhex(
    "9050508BC450B84000000050B804000000508B850CFCFFFF050C02000050FF559C8B850CFCFFFF05"
    "0C020000C600748BC4508B44240450B804000000508B850CFCFFFF050C02000050FF559C5858FFA5"
    "0CFCFFFF0000000000000000000000005068 00A64701FF15B0F0FD00585D5F5E5A595BFFE0000000"
    "0000000000000000".replace(" ", "")) + b"nvse_steam_loader.dll"
P7 = bytes.fromhex("6068C8A6470168E0A64701FF15F4F1FD0068C8A6470168D0A64701FF15F4F1FD0061E9A7ECF8FF")
P9 = b"0\0\0\0SteamAppId\0\0\0\0\0\0SteamGameId"


def main(argv):
    exe, bak = os.path.join(GAME, "FalloutNV.exe"), os.path.join(GAME, "FalloutNV_backup.exe")
    if "--restore" in argv:
        shutil.copyfile(bak, exe)
        print("restored from backup")
        return 0
    data = bytearray(open(exe, "rb").read())
    sha = hashlib.sha1(data).hexdigest().upper()
    if sha not in VERSIONS:
        print("unknown exe (already patched?):", sha)
        return 1
    v = VERSIONS[sha]
    assert len(P6) == 149 and len(P7) == 39 and len(P9) == 31
    if not os.path.exists(bak):
        shutil.copyfile(exe, bak)
    for off, p in ((0x136, b"\x22"), (0x148, bytes([0x20, 0xA6, 0x07])), (0x178, P3[v]),
                   (0xF57277, bytes([0xE8, 0x04, 0xFD, 0x06, 0x00, 0x90])),
                   (0xF57385, bytes([0xE9, 0x56, 0xFC, 0x06, 0x00])),
                   (0xFC6F80, P6), (0xFC7020, P7), (0xFC70C8, P8[v]), (0xFC70CC, P9)):
        data[off:off + len(p)] = p
    open(exe, "wb").write(data)
    print("patched version %d; new sha1 %s" % (v, hashlib.sha1(data).hexdigest().upper()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
