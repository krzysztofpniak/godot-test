#!/usr/bin/env python3
"""
Crash Bandicoot (1, EU/PSX) NSF -> glTF converter.

Reads an .NSF file (as extracted from the game's WAD), pulls out all
"Old Scenery" entries (level/world geometry, entry type 3 in Crash 1),
and writes a single binary glTF (.glb) file containing one mesh per
entry, with vertex positions, vertex colors, and (where available)
decoded PSX textures (4bpp/8bpp CLUT-indexed and 16bpp direct color).

This intentionally does NOT handle:
 - Models (type 2) / animations
 - Crash 2/3 scenery formats (NewSceneryEntry)
 - "Sky" handling, model-struct overlays, etc.

Ported directly from the relevant parts of CrashEdit (NSF chunk reader,
OldSceneryEntry loader, OldSceneryTexture, and the crash1-generic
fragment shader which performs the VRAM/CLUT texel lookups).
"""

import sys
import struct
import json
import zlib
import base64

CHUNK_LEN = 0x10000
TEX_PAGE_W = 512
TEX_PAGE_H = 128


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
    modelstruct = (worda >> 8) & 0xFFF
    page = (worda >> 5) & 0x7
    return vertexa, vertexb, vertexc, modelstruct, page


def parse_old_scenery_texture(data):
    """Parse an 8-byte 'textured' model-struct (OldSceneryTexture)."""
    blendmode = (data[3] >> 5) & 0x3
    clutx = data[3] & 0xF
    texinfo = struct.unpack_from('<I', data, 4)[0]
    uvindex = (texinfo >> 22) & 0x3FF
    colormode = (texinfo >> 20) & 0x3
    segment = (texinfo >> 18) & 0x3
    xoffu = (texinfo >> 13) & 0x1F
    cluty = (texinfo >> 6) & 0x7F
    yoffu = texinfo & 0x1F

    w = 4 << (uvindex % 5)
    h = 4 << ((uvindex // 5) % 5)
    xoff = (64 << (2 - colormode)) * segment + (2 << (2 - colormode)) * xoffu
    yoff = yoffu * 4
    winding = uvindex // 25

    def bit(mask, n):
        return (mask >> n) & 1

    u1 = w * bit(0x30FF0C, winding) + xoff
    u2 = w * bit(0x8799E1, winding) + xoff
    u3 = w * bit(0x4B66D2, winding) + xoff
    v1 = h * bit(0xF3CC30, winding) + yoff
    v2 = h * bit(0x9E7186, winding) + yoff
    v3 = h * bit(0x6DB249, winding) + yoff

    return {
        "clutx": clutx, "cluty": cluty, "colormode": colormode,
        "blendmode": blendmode, "xoff": xoff, "yoff": yoff, "w": w, "h": h,
        "uv": ((u1, v1), (u2, v2), (u3, v3)),
    }


def extract_nsf(data):
    """Return (scenery_entries, texture_chunks) from raw NSF data.

    scenery_entries: list of dicts with keys eid/vertices/polygons/structs/
                      offset/tpags
    texture_chunks: dict mapping EID -> raw 64KiB texture-page chunk bytes
    """
    entries = []
    texture_chunks = {}
    seen_eids = set()
    for chunk in read_chunks(data):
        ctype = struct.unpack_from('<h', chunk, 2)[0]
        if ctype == 1:
            eid = struct.unpack_from('<i', chunk, 4)[0]
            texture_chunks.setdefault(eid, chunk)
            continue
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
            tpagcount = struct.unpack_from('<i', info, 0x18)[0]
            tpags = [
                struct.unpack_from('<i', info, 0x20 + 4 * i)[0]
                for i in range(tpagcount)
            ]
            entries.append({
                "eid": eid,
                "vertices": vertices,
                "polygons": polygons,
                "info": info,
                "tpags": tpags,
                "offset": (xoff, yoff, zoff),
            })
    return entries, texture_chunks


def get_struct_texture(info, modelstruct):
    """Return parsed OldSceneryTexture dict for polygon.ModelStruct, or None."""
    off = 0x40 + modelstruct * 4
    if off + 4 > len(info):
        return None
    if (info[off + 3] & 0x80) == 0:
        return None  # untextured (OldSceneryColor)
    if off + 8 > len(info):
        return None
    return parse_old_scenery_texture(info[off:off + 8])


# --- PSX texture page (VRAM) decoding -------------------------------------

def texel4(page, u, v):
    b = page[(v % TEX_PAGE_H) * TEX_PAGE_W + ((u // 2) % TEX_PAGE_W)]
    return (b & 0xF) if (u & 1) == 0 else ((b >> 4) & 0xF)


def texel8(page, u, v):
    return page[(v % TEX_PAGE_H) * TEX_PAGE_W + (u % TEX_PAGE_W)]


def texel16(page, u, v):
    base = (v % TEX_PAGE_H) * TEX_PAGE_W + ((u * 2) % TEX_PAGE_W)
    lo = page[base]
    hi = page[base + 1]
    t = lo | (hi << 8)
    return t & 0x1F, (t >> 5) & 0x1F, (t >> 10) & 0x1F, (t >> 15) & 0x1


def decode_tile(page, cmode, cx, cy, xoff, yoff, w, h, blendmode):
    """Decode a w x h texel tile of a texture page to RGBA8888 bytes."""
    pixels = bytearray(w * h * 4)
    for vy in range(h):
        v = yoff + vy
        for vx in range(w):
            u = xoff + vx
            if cmode == 0:
                idx = texel4(page, u, v)
                r, g, b, a = texel16(page, cx + idx, cy)
            elif cmode == 1:
                idx = texel8(page, u, v)
                r, g, b, a = texel16(page, cx + idx, cy)
            else:
                r, g, b, a = texel16(page, u, v)

            R = (r * 255) // 31
            G = (g * 255) // 31
            B = (b * 255) // 31
            if r == 0 and g == 0 and b == 0 and a == 0:
                A = 0  # color-keyed transparent
            elif blendmode == 0 and a == 1:
                A = 128  # semi-transparent
            else:
                A = 255

            off = (vy * w + vx) * 4
            pixels[off:off + 4] = bytes((R, G, B, A))
    return bytes(pixels)


def write_png(width, height, rgba):
    def chunk(tag, payload):
        c = tag + payload
        return struct.pack('>I', len(payload)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        raw.extend(rgba[y * stride:(y + 1) * stride])
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b'IHDR', ihdr) + chunk(b'IDAT', idat) + chunk(b'IEND', b'')


# --- glTF building ----------------------------------------------------------

class GLTFBuilder:
    def __init__(self):
        self.buf = bytearray()
        self.accessors = []
        self.buffer_views = []
        self.meshes = []
        self.nodes = []
        self.images = []
        self.textures = []
        self.materials = []
        self.samplers = [{
            "magFilter": 9728, "minFilter": 9728,  # NEAREST
            "wrapS": 33071, "wrapT": 33071,  # CLAMP_TO_EDGE
        }]
        self.material_cache = {}
        self.default_material = None

    def add_view(self, blob, target):
        while len(self.buf) % 4:
            self.buf.append(0)
        offset = len(self.buf)
        self.buf.extend(blob)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(blob)}
        if target is not None:
            view["target"] = target
        self.buffer_views.append(view)
        return len(self.buffer_views) - 1

    def get_default_material(self):
        if self.default_material is None:
            self.default_material = len(self.materials)
            self.materials.append({
                "pbrMetallicRoughness": {
                    "baseColorFactor": [1, 1, 1, 1],
                    "metallicFactor": 0,
                    "roughnessFactor": 1,
                },
                "doubleSided": True,
            })
        return self.default_material

    def get_material(self, key, page_bytes):
        if key in self.material_cache:
            return self.material_cache[key]
        _tpag, cmode, cx, cy, xoff, yoff, w, h, bmode = key
        rgba = decode_tile(page_bytes, cmode, cx, cy, xoff, yoff, w, h, bmode)

        alphas = rgba[3::4]
        if any(a == 0 for a in alphas):
            alpha_mode = "MASK"
        elif any(a == 128 for a in alphas):
            alpha_mode = "BLEND"
        else:
            alpha_mode = "OPAQUE"

        png = write_png(w, h, rgba)
        img_index = len(self.images)
        self.images.append({
            "uri": "data:image/png;base64," + base64.b64encode(png).decode('ascii')
        })
        tex_index = len(self.textures)
        self.textures.append({"source": img_index, "sampler": 0})

        mat_index = len(self.materials)
        material = {
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": tex_index},
                "baseColorFactor": [1, 1, 1, 1],
                "metallicFactor": 0,
                "roughnessFactor": 1,
            },
            "doubleSided": True,
        }
        if alpha_mode != "OPAQUE":
            material["alphaMode"] = alpha_mode
            if alpha_mode == "MASK":
                material["alphaCutoff"] = 0.5
        self.materials.append(material)

        self.material_cache[key] = mat_index
        return mat_index

    def add_primitive(self, tris, scale, with_uv, material_index):
        """tris: list of (pos_a, pos_b, pos_c, col_a, col_b, col_c, [uv_a, uv_b, uv_c])"""
        if not tris:
            return None

        positions = bytearray()
        colors = bytearray()
        uvs = bytearray() if with_uv else None
        xs, ys, zs = [], [], []

        for tri in tris:
            for i in range(3):
                x, y, z = tri["pos"][i]
                fx, fy, fz = x * scale, y * scale, z * scale
                positions += struct.pack('<3f', fx, fy, fz)
                xs.append(fx)
                ys.append(fy)
                zs.append(fz)
                r, g, b = tri["col"][i]
                # PSX vertex colors are scaled x2 by the in-game shader
                # (128 == "neutral"/white); approximate that here.
                cr = min(1.0, (r / 255.0) * 2.0)
                cg = min(1.0, (g / 255.0) * 2.0)
                cb = min(1.0, (b / 255.0) * 2.0)
                colors += struct.pack('<3f', cr, cg, cb)
                if with_uv:
                    u, v = tri["uv"][i]
                    uvs += struct.pack('<2f', u, v)

        count = len(tris) * 3
        pos_view = self.add_view(bytes(positions), 34962)
        col_view = self.add_view(bytes(colors), 34962)

        pos_accessor = len(self.accessors)
        self.accessors.append({
            "bufferView": pos_view, "componentType": 5126, "count": count,
            "type": "VEC3",
            "min": [min(xs), min(ys), min(zs)],
            "max": [max(xs), max(ys), max(zs)],
        })
        col_accessor = len(self.accessors)
        self.accessors.append({
            "bufferView": col_view, "componentType": 5126, "count": count,
            "type": "VEC3",
        })

        attributes = {"POSITION": pos_accessor, "COLOR_0": col_accessor}

        if with_uv:
            uv_view = self.add_view(bytes(uvs), 34962)
            uv_accessor = len(self.accessors)
            self.accessors.append({
                "bufferView": uv_view, "componentType": 5126, "count": count,
                "type": "VEC2",
            })
            attributes["TEXCOORD_0"] = uv_accessor

        primitive = {"attributes": attributes, "mode": 4}
        if material_index is not None:
            primitive["material"] = material_index
        return primitive

    def add_entry(self, entry, texture_chunks, scale):
        eid = entry["eid"]
        vertices = entry["vertices"]
        polygons = entry["polygons"]
        info = entry["info"]
        tpags = entry["tpags"]
        xoff, yoff, zoff = entry["offset"]

        if not vertices or not polygons:
            return

        # group triangles by material key
        groups = {}  # key -> list of tri dicts; key None => untextured
        for va, vb, vc, modelstruct, page in polygons:
            if va >= len(vertices) or vb >= len(vertices) or vc >= len(vertices):
                continue

            tex = get_struct_texture(info, modelstruct)
            key = None
            uv = None
            if tex is not None and page < len(tpags) and tpags[page] in texture_chunks:
                tpag_eid = tpags[page]
                w, h = tex["w"], tex["h"]
                (u1, v1), (u2, v2), (u3, v3) = tex["uv"]
                key = (tpag_eid, tex["colormode"], tex["clutx"] * 16, tex["cluty"],
                       tex["xoff"], tex["yoff"], w, h, tex["blendmode"])
                # vertex order: A<-(U3,V3), B<-(U2,V2), C<-(U1,V1)
                uv = (
                    ((u3 - tex["xoff"]) / w, (v3 - tex["yoff"]) / h),
                    ((u2 - tex["xoff"]) / w, (v2 - tex["yoff"]) / h),
                    ((u1 - tex["xoff"]) / w, (v1 - tex["yoff"]) / h),
                )

            va_, vb_, vc_ = vertices[va], vertices[vb], vertices[vc]
            tri = {
                "pos": ((va_[0], va_[1], va_[2]), (vb_[0], vb_[1], vb_[2]), (vc_[0], vc_[1], vc_[2])),
                "col": ((va_[3], va_[4], va_[5]), (vb_[3], vb_[4], vb_[5]), (vc_[3], vc_[4], vc_[5])),
            }
            if uv is not None:
                tri["uv"] = uv
            groups.setdefault(key, []).append(tri)

        primitives = []
        for key, tris in groups.items():
            if key is None:
                primitive = self.add_primitive(tris, scale, with_uv=False,
                                                 material_index=self.get_default_material())
            else:
                tpag_eid = key[0]
                page_bytes = texture_chunks[tpag_eid]
                material_index = self.get_material(key, page_bytes)
                primitive = self.add_primitive(tris, scale, with_uv=True,
                                                 material_index=material_index)
            if primitive is not None:
                primitives.append(primitive)

        if not primitives:
            return

        mesh_index = len(self.meshes)
        self.meshes.append({"name": eid_to_ename(eid), "primitives": primitives})

        translation = [xoff * scale, yoff * scale, zoff * scale]
        node = {"mesh": mesh_index, "name": eid_to_ename(eid)}
        if any(translation):
            node["translation"] = translation
        self.nodes.append(node)

    def build(self):
        gltf = {
            "asset": {"version": "2.0", "generator": "crash_to_gltf.py"},
            "scene": 0,
            "scenes": [{"nodes": list(range(len(self.nodes)))}],
            "nodes": self.nodes,
            "meshes": self.meshes,
            "accessors": self.accessors,
            "bufferViews": self.buffer_views,
            "buffers": [{"byteLength": len(self.buf)}],
        }
        if self.materials:
            gltf["materials"] = self.materials
        if self.textures:
            gltf["textures"] = self.textures
        if self.images:
            gltf["images"] = self.images
        if self.samplers and self.textures:
            gltf["samplers"] = self.samplers

        json_bytes = json.dumps(gltf).encode('utf-8')
        while len(json_bytes) % 4:
            json_bytes += b' '

        bin_bytes = bytes(self.buf)
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

    entries, texture_chunks = extract_nsf(data)
    print(f"Found {len(entries)} scenery entries, {len(texture_chunks)} texture pages")
    for entry in entries:
        print(f"  {eid_to_ename(entry['eid'])}: {len(entry['vertices'])} verts, "
              f"{len(entry['polygons'])} polys, offset={entry['offset']}, "
              f"tpags={[eid_to_ename(t) for t in entry['tpags']]}")

    builder = GLTFBuilder()
    for entry in entries:
        builder.add_entry(entry, texture_chunks, scale)

    glb = builder.build()
    with open(out_path, 'wb') as f:
        f.write(glb)
    print(f"Wrote {out_path} ({len(glb)} bytes), "
          f"{len(builder.materials)} materials, {len(builder.images)} textures")


if __name__ == '__main__':
    main()
