"""Lossless transport of signed releases using small files and existing assets.

No signing key is read. The final envelope, including its existing signature,
must match the original release's SHA-256 byte for byte.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re

LIMIT = 16384
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def pack(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def members(text):
    """Return exact character spans of JSON object values, rejecting duplicates."""
    decoder = json.JSONDecoder()
    pos = 0
    def ws(p):
        while p < len(text) and text[p] in " \t\r\n":
            p += 1
        return p
    pos = ws(pos)
    if text[pos:pos+1] != "{":
        raise ValueError("Expected JSON object")
    pos = ws(pos + 1)
    result = {}
    while text[pos:pos+1] != "}":
        key, end = decoder.raw_decode(text, pos)
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid or duplicate JSON key")
        pos = ws(end)
        if text[pos:pos+1] != ":":
            raise ValueError("Expected colon")
        start = ws(pos + 1)
        _, end = decoder.raw_decode(text, start)
        result[key] = (start, end)
        pos = ws(end)
        if text[pos:pos+1] == "}":
            break
        if text[pos:pos+1] != ",":
            raise ValueError("Expected comma")
        pos = ws(pos + 1)
        if text[pos:pos+1] == "}":
            raise ValueError("Trailing comma")
    if ws(pos + 1) != len(text):
        raise ValueError("Trailing JSON data")
    return result


def envelope(data):
    raw = data.decode("utf-8")
    spans = members(raw)
    obj = json.loads(raw)
    if obj.get("format") != 2:
        raise ValueError("Expected signed envelope format 2")
    payload = base64.b64decode(obj["payload"], validate=True).decode("utf-8")
    if len(base64.b64decode(obj["signature"], validate=True)) != 64:
        raise ValueError("Expected 64-byte signature")
    start, end = spans["payload"]
    if raw[start:end] != json.dumps(obj["payload"]):
        raise ValueError("Payload must use an unescaped base64 string")
    inner_spans = members(payload)
    inner = json.loads(payload)
    if not VERSION.fullmatch(inner["version"]):
        raise ValueError("Invalid release version")
    a, b = inner_spans["files"]
    files = {key: (a + x, a + y) for key, (x, y) in members(payload[a:b]).items()}
    return raw[:start+1], raw[end-1:], payload, inner, files


def store_chunk(root, data):
    if not 0 < len(data) <= LIMIT:
        raise ValueError("Transport chunk exceeds 16 KiB")
    digest = sha(data)
    dest = root / "publishing/chunks" / (digest + ".txt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return digest


def read_chunk(root, digest):
    if not isinstance(digest, str) or not HASH.fullmatch(digest):
        raise ValueError("Invalid chunk identifier")
    data = (root / "publishing/chunks" / (digest + ".txt")).read_bytes()
    if len(data) > LIMIT or sha(data) != digest:
        raise ValueError("Transport chunk hash/size mismatch")
    return data


def prepare(root, base, target, latest=None):
    base = base.resolve()
    relative = base.relative_to(root.resolve()).as_posix()
    if not re.fullmatch(r"updates/[0-9]+\.[0-9]+\.[0-9]+\.json", relative):
        raise ValueError("Base must be an existing updates/VERSION.json")
    base_data, target_data = base.read_bytes(), target.read_bytes()
    _, _, old, _, old_files = envelope(base_data)
    prefix, suffix, new, new_obj, new_files = envelope(target_data)
    parts, cursor = [], 0
    def literal(text):
        if text and len(text.encode("utf-8")) <= 512:
            parts.append(["inline", text])
            return
        # At most four UTF-8 bytes per character.
        for i in range(0, len(text), 4096):
            parts.append(["text", store_chunk(root, text[i:i+4096].encode("utf-8"))])
    for name, (a, b) in new_files.items():
        previous = old_files.get(name)
        if previous and old[previous[0]:previous[1]] == new[a:b]:
            literal(new[cursor:a])
            parts.append(["copy", *previous])
            cursor = b
    literal(new[cursor:])
    blocks, block = [], []
    for part in parts:
        if block and len(pack(block + [part])) > 12000:
            blocks.append(store_chunk(root, pack(block)))
            block = []
        block.append(part)
    if block:
        blocks.append(store_chunk(root, pack(block)))
    request = {"format": 1, "mode": "publish" if latest else "verify",
               "base": relative, "base_sha256": sha(base_data),
               "version": new_obj["version"], "target_sha256": sha(target_data),
               "prefix": prefix, "suffix": suffix, "blocks": blocks}
    if latest:
        latest_data = latest.read_bytes()
        request["latest"] = latest_data.decode("utf-8")
        request["previous_latest_sha256"] = sha((root / "updates/latest.json").read_bytes())
        validate_latest(request)
    encoded = pack(request)
    if len(encoded) > LIMIT:
        raise ValueError("Request exceeds 16 KiB")
    path = root / "publishing/request.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    rebuilt, _ = assemble(root)
    if rebuilt != target_data:
        raise ValueError("Round-trip byte comparison failed")
    used = set(blocks)
    used.update(p[1] for p in parts if p[0] == "text")
    print(json.dumps({"mode": request["mode"], "version": request["version"],
                      "package_bytes": len(target_data), "transport_files": len(used) + 1,
                      "transport_bytes": len(encoded) + sum(len(read_chunk(root, h)) for h in used),
                      "max_file_bytes": max([len(encoded)] + [len(read_chunk(root, h)) for h in used]),
                      "sha256": sha(target_data), "byte_identical": True}))
    return request


def validate_latest(request):
    latest = json.loads(request["latest"])
    version = request["version"]
    expected_url = "https://raw.githubusercontent.com/vludber/unit-studio-updates/main/updates/" + version + ".json"
    if (latest.get("version") != version or latest.get("url") != expected_url or
            latest.get("digest") != "sha256:" + request["target_sha256"]):
        raise ValueError("latest.json does not match signed release")


def assemble(root):
    raw = (root / "publishing/request.json").read_bytes()
    if len(raw) > LIMIT:
        raise ValueError("Request exceeds 16 KiB")
    request = json.loads(raw)
    if request.get("format") != 1 or request.get("mode") not in ("verify", "publish"):
        raise ValueError("Unknown transport format/mode")
    if not VERSION.fullmatch(request["version"]):
        raise ValueError("Invalid version")
    if not re.fullmatch(r"updates/[0-9]+\.[0-9]+\.[0-9]+\.json", request["base"]):
        raise ValueError("Invalid base path")
    base_data = (root / request["base"]).read_bytes()
    if sha(base_data) != request["base_sha256"]:
        raise ValueError("Base package hash mismatch")
    _, _, old, _, _ = envelope(base_data)
    text, length = [], 0
    for block in request["blocks"]:
        for part in json.loads(read_chunk(root, block)):
            if part[0] == "text" and len(part) == 2:
                piece = read_chunk(root, part[1]).decode("utf-8")
            elif part[0] == "inline" and len(part) == 2 and isinstance(part[1], str):
                piece = part[1]
                if len(piece.encode("utf-8")) > 512:
                    raise ValueError("Inline text too large")
            elif part[0] == "copy" and len(part) == 3:
                a, b = part[1:]
                if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(old):
                    raise ValueError("Invalid copy range")
                piece = old[a:b]
            else:
                raise ValueError("Invalid transport operation")
            length += len(piece)
            if length > 64 * 1024 * 1024:
                raise ValueError("Payload too large")
            text.append(piece)
    payload = "".join(text).encode("utf-8")
    data = (request["prefix"] + base64.b64encode(payload).decode("ascii") + request["suffix"]).encode("utf-8")
    if sha(data) != request["target_sha256"]:
        raise ValueError("Reconstructed package hash mismatch")
    if envelope(data)[3]["version"] != request["version"]:
        raise ValueError("Package version mismatch")
    if request["mode"] == "publish":
        validate_latest(request)
    return data, request


def apply(root):
    data, request = assemble(root)
    if request["mode"] == "verify":
        print("Verified exact signed package; no release files changed.")
        return
    path = root / "updates" / (request["version"] + ".json")
    latest_path = root / "updates/latest.json"
    current = latest_path.read_bytes()
    new_latest = request["latest"].encode("utf-8")
    if path.exists() and path.read_bytes() == data and current == new_latest:
        print("Release already published; no changes.")
        return
    if path.exists():
        raise ValueError("Refusing to overwrite an existing release")
    if sha(current) != request["previous_latest_sha256"]:
        raise ValueError("latest.json changed since preparation; regenerate request")
    version = lambda s: tuple(int(x) for x in s.split("."))
    if version(request["version"]) <= version(json.loads(current)["version"]):
        raise ValueError("Refusing a downgrade")
    # GitHub Actions commits these two files together and pushes without force.
    path.write_bytes(data)
    latest_path.write_bytes(new_latest)
    print("Prepared verified release " + request["version"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("prepare")
    command.add_argument("--base", type=Path, required=True)
    command.add_argument("--target", type=Path, required=True)
    command.add_argument("--latest", type=Path, help="Enables publication; omit for verification only")
    sub.add_parser("apply")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.root, args.base, args.target, args.latest)
    else:
        apply(args.root)
