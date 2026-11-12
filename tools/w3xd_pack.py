"""Re-pack a Reforged 3.0 .w3xd campaign map around a modified MPQ (for testing translations).

Needs the community toolkit https://github.com/mythic-p/w3xd-toolkit (MIT) for the crypto; pass its folder with
--toolkit or the W3XD_TOOLKIT environment variable. The header, nonce and trailer of the ORIGINAL container are kept;
only the payload (AES-256-CCM of the MPQ), its tag and the sizes change. Output is encrypted for the same account
license as the input, so it must never be published.

    python tools/w3xd_pack.py roundtrip <orig.w3xd>                  # decrypt + re-encrypt, must be byte-identical
    python tools/w3xd_pack.py repack <orig.w3xd> <new.mpq|w3x> <out.w3xd>
"""
import os, sys, struct, argparse


def _import_toolkit(path):
    path = path or os.environ.get("W3XD_TOOLKIT")
    if not path:
        raise SystemExit("set --toolkit or W3XD_TOOLKIT to the w3xd-toolkit folder")
    sys.path.insert(0, path)
    global container, ccm, xorshift
    from w3xd import container, ccm, xorshift


def repack_bytes(raw, new_mpq):
    c = container.Container(raw)
    c.decrypt_payload()
    t = c.trailer
    hdr = bytearray(c.layer1[:t["hdr_size"]])
    nonce = bytes(hdr[c.nonce_off:c.nonce_off + ccm.NONCE_LEN])
    ct, tag = ccm.encrypt(c.aes, nonce, new_mpq)
    hdr[c.tag_off:c.tag_off + ccm.TAG_LEN] = tag
    tr = bytearray(c.layer1[-container.TRAILER_LEN:])
    struct.pack_into("<Q", tr, 6, len(ct))
    plain = bytes(hdr) + ct + bytes(tr)
    return xorshift.xor(plain, xorshift.seed_from(len(plain), t["license"])), c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--toolkit")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("roundtrip"); a.add_argument("orig")
    b = sp.add_parser("repack"); b.add_argument("orig"); b.add_argument("mpq"); b.add_argument("out")
    args = ap.parse_args()
    _import_toolkit(args.toolkit)
    raw = open(args.orig, "rb").read()
    if args.cmd == "roundtrip":
        c = container.Container(raw)
        mpq = c.decrypt_payload()
        out, _ = repack_bytes(raw, mpq)
        same = out == raw
        print("byte-identical:", same, len(raw), len(out))
        if not same:
            d = [i for i in range(min(len(raw), len(out))) if raw[i] != out[i]]
            print("first diffs:", d[:10], "count", len(d))
    else:
        out, _ = repack_bytes(raw, open(args.mpq, "rb").read())
        open(args.out, "wb").write(out)
        print("written", args.out, len(out))


if __name__ == "__main__":
    main()
