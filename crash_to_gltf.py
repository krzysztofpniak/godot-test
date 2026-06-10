#!/usr/bin/env python3
"""
Simplest possible Crash Bandicoot (1, EU/PSX) NSF -> glTF converter.

Reads an .NSF file (as extracted from the game's WAD), pulls out all
"Old Scenery" entries (level/world geometry, entry type 3 in Crash 1),
and writes a single binary glTF (.glb) file containing one mesh per
entry, with vertex positions and per-vertex colors.

This intentionally does NOT handle:
 - Models (type 2) / animations
 - Textures / UVs
 - Crash 2/3 scenery formats (NewSceneryEntry)
 - "Sky" handling, model-struct overlays, etc.

It is meant as a minimal starting point, ported directly from the
relevant parts of CrashEdit (NSF chunk reader + OldSceneryEntry loader).
"""

import sys
import struct
import json

CHUNK_LEN = 0x10000


def to_int16(val):
    val &= 0xFFFF
    if val >= 0x8000:
        val -= 0x10000
    return val


def read_chunks(data):
    """Yield 64KiB decompressed chunk buffers from raw NSF data."""
    offset = 0
    while offset < len(data):
        magic = struct.unpack_from('<H', data, offset)[0]
        if magic == 0x1234 or magic == 0:
            chunk = data[offset:offset + CHUNK_LEN]
            if len(chunk) < CHUNK_LEN:
                break
            offset += CHUNK_LEN
            yield chunk
        elif magic == 0x1235:
            zero, length, skip = struct.unpack_from('<hii', data, offset + 2)
            offset += 12
            result = bytearray(CHUNK_LEN)
            pos = 0
            while pos < length:
                prefix = data[offset]
                offset += 1
                if prefix & 0x80:
                    prefix &= 0x7F
                    seekbyte = data[offset]
                    offset += 1
                    span = seekbyte & 7
                    seek = seekbyte >> 3
                    seek |= prefix << 5
                    span = 64 if span == 7 else span + 3
                    for i in range(span):
                        result[pos + i] = result[pos - seek + i]
                    pos += span
                else:
                    result[pos:pos + prefix] = data[offset:offset + prefix]
                    offset += prefix
                    pos += prefix
            offset += skip
            tail = CHUNK_LEN - length
            result[pos:pos + tail] = data[offset:offset + tail]
            offset += tail
            yield bytes(result)
        else:
            raise ValueError(f"Unknown chunk magic {magic:#06x} at offset {offset:#x}")


def parse_entry_chunk(chunk):
    """Yield (eid, type, items) for each entry in an EntryChunk (type 0)."""
    entrycount = struct.unpack_from('<i', chunk, 8)[0]
    for i in range(entrycount):
        start = struct.unpack_from('<i', chunk, 16 + i * 4)[0]
        end = struct.unpack_from('<i', chunk, 20 + i * 4)[0]
        entry = chunk[start:end]
        if len(entry) < 16:
            continue
        eid = struct.unpack_from('<i', entry, 4)[0]
        etype = struct.unpack_from('<i', entry, 8)[0]
        itemcount = struct.unpack_from('<i', entry, 12)[0]
        items = []
        for j in range(itemcount):
            istart = struct.unpack_from('<i', entry, 16 + j * 4)[0]
            iend = struct.unpack_from('<i', entry, 20 + j * 4)[0]
            items.append(entry[istart:iend])
        yield eid, etype, items


ENAME_CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_!"


def eid_to_ename(eid):
    eid >>= 1
    out = []
    for _ in range(5):
        out.append(ENAME_CHARS[eid & 0x3F])
        eid >>= 6
    return ''.join(reversed(out))


def parse_old_scenery_vertex(data):
    red, green, blue = data[0], data[1], data[2]
    x = to_int16(struct.unpack_from('<H', data, 4)[0] & 0xFFF8)
    y = to_int16(struct.unpack_from('<H', data, 6)[0] & 0xFFF8)
    zhigh = data[6] & 7
    zmid = (data[4] & 6) >> 1
    zlow = data[3]
    z = to_int16((zhigh << 13) | (zmid << 11) | (zlow << 3))
    return x, y, z, red, green, blue


def parse_old_scenery_polygon(data):
    worda = struct.unpack_from('<I', data, 0)[0]
    wordb = struct.unpack_from('<I', data, 4)[0]
    vertexa = (worda >> 20) & 0xFFF
    vertexb = (wordb >> 8) & 0xFFF
    vertexc = (wordb >> 20) & 0xFFF
    return vertexa, vertexb, vertexc


def extract_old_scenery_entries(data):
    """Find every OldSceneryEntry (type 3) in the NSF data."""
    entries = []
    seen_eids = set()
    for chunk in read_chunks(data):
        ctype = struct.unpack_from('<h', chunk, 2)[0]
        if ctype != 0:  # only "Normal" entry chunks contain scenery
            continue
        for eid, etype, items in parse_entry_chunk(chunk):
            if etype != 3:
                continue
            if eid in seen_eids:
                continue
            if len(items) < 3 or len(items[0]) < 0x40:
                continue
            seen_eids.add(eid)
            info = items[0]
            polygoncount = struct.unpack_from('<i', info, 0xC)[0]
            vertexcount = struct.unpack_from('<i', info, 0x10)[0]
            if len(items[1]) < polygoncount * 8 or len(items[2]) < vertexcount * 8:
                continue

            vertices = [
                parse_old_scenery_vertex(items[2][i * 8:i * 8 + 8])
                for i in range(vertexcount)
            ]
            polygons = [
                parse_old_scenery_polygon(items[1][i * 8:i * 8 + 8])
                for i in range(polygoncount)
            ]
            xoff, yoff, zoff = struct.unpack_from('<3i', info, 0)
            entries.append((eid, vertices, polygons, (xoff, yoff, zoff)))
    return entries


def build_glb(entries, scale):
    buf = bytearray()
    accessors = []
    buffer_views = []
    meshes = []
    nodes = []

    def add_view(blob, target):
        # keep everything 4-byte aligned
        while len(buf) % 4:
            buf.append(0)
        offset = len(buf)
        buf.extend(blob)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(blob)}
        if target is not None:
            view["target"] = target
        buffer_views.append(view)
        return len(buffer_views) - 1

    for eid, vertices, polygons, (xoff, yoff, zoff) in entries:
        if not vertices or not polygons:
            continue

        # filter out degenerate / out-of-range polygons
        tris = [
            p for p in polygons
            if p[0] < len(vertices) and p[1] < len(vertices) and p[2] < len(vertices)
        ]
        if not tris:
            continue

        positions = bytearray()
        colors = bytearray()
        xs, ys, zs = [], [], []
        for x, y, z, r, g, b in vertices:
            fx, fy, fz = x * scale, y * scale, z * scale
            positions += struct.pack('<3f', fx, fy, fz)
            colors += struct.pack('<3f', r / 255.0, g / 255.0, b / 255.0)
            xs.append(fx)
            ys.append(fy)
            zs.append(fz)

        indices = bytearray()
        for a, b, c in tris:
            indices += struct.pack('<3I', a, b, c)

        pos_view = add_view(bytes(positions), 34962)  # ARRAY_BUFFER
        col_view = add_view(bytes(colors), 34962)
        idx_view = add_view(bytes(indices), 34963)  # ELEMENT_ARRAY_BUFFER

        pos_accessor = len(accessors)
        accessors.append({
            "bufferView": pos_view, "componentType": 5126, "count": len(vertices),
            "type": "VEC3",
            "min": [min(xs), min(ys), min(zs)],
            "max": [max(xs), max(ys), max(zs)],
        })
        col_accessor = len(accessors)
        accessors.append({
            "bufferView": col_view, "componentType": 5126, "count": len(vertices),
            "type": "VEC3",
        })
        idx_accessor = len(accessors)
        accessors.append({
            "bufferView": idx_view, "componentType": 5125, "count": len(tris) * 3,
            "type": "SCALAR",
        })

        mesh_index = len(meshes)
        meshes.append({
            "name": eid_to_ename(eid),
            "primitives": [{
                "attributes": {"POSITION": pos_accessor, "COLOR_0": col_accessor},
                "indices": idx_accessor,
                "mode": 4,  # TRIANGLES
            }],
        })
        translation = [xoff * scale, yoff * scale, zoff * scale]
        node = {"mesh": mesh_index, "name": eid_to_ename(eid)}
        if any(translation):
            node["translation"] = translation
        nodes.append(node)

    gltf = {
        "asset": {"version": "2.0", "generator": "crash_to_gltf.py"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(buf)}],
    }

    json_bytes = json.dumps(gltf).encode('utf-8')
    while len(json_bytes) % 4:
        json_bytes += b' '

    bin_bytes = bytes(buf)
    while len(bin_bytes) % 4:
        bin_bytes += b'\x00'

    glb = bytearray()
    total_length = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    glb += struct.pack('<4sII', b'glTF', 2, total_length)
    glb += struct.pack('<I4s', len(json_bytes), b'JSON')
    glb += json_bytes
    glb += struct.pack('<I4s', len(bin_bytes), b'BIN\x00')
    glb += bin_bytes
    return bytes(glb)


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input.NSF> <output.glb> [scale]")
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2]
    scale = float(sys.argv[3]) if len(sys.argv) > 3 else 1 / 1024.0

    with open(in_path, 'rb') as f:
        data = f.read()

    entries = extract_old_scenery_entries(data)
    print(f"Found {len(entries)} scenery entries")
    for eid, vertices, polygons, offset in entries:
        print(f"  {eid_to_ename(eid)}: {len(vertices)} verts, {len(polygons)} polys, offset={offset}")

    glb = build_glb(entries, scale)
    with open(out_path, 'wb') as f:
        f.write(glb)
    print(f"Wrote {out_path} ({len(glb)} bytes)")


if __name__ == '__main__':
    main()
