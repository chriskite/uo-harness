#!/usr/bin/env python3
"""
mrt_parse.py - NativeAOT NativeFormat (.mrt) reflection metadata parser.

Parses ClassicUO.exe (NativeAOT build) and produces mrt_map.json:
    method name -> method RVA (+ declaring type, VA, flags, metadata offset)

Format references (dotnet/runtime, main):
  - tools/Common/Internal/NativeFormat/NativeFormatReader*.cs   (compressed ints, strings)
  - tools/Common/Internal/Metadata/NativeFormat/NativeFormatReaderGen.cs (record schema)
  - tools/Common/Internal/Metadata/NativeFormat/NativeMetadataReader.cs  (0xDEADDFFD header)
  - tools/Common/Internal/Runtime/ModuleHeaders.cs / MetadataBlob.cs     (RTR header, blob ids)
  - tools/aot/ILCompiler.Compiler/Compiler/DependencyAnalysis/ReflectionInvokeMapNode.cs
  - tools/aot/ILCompiler.Reflection.ReadyToRun/NativeHashtable.cs        (hashtable layout)
  - nativeaot/Common/src/Internal/Runtime/TypeLoader/ExternalReferencesTable.cs (rel32 fixups)

Layout summary:
  PE (.rdata) contains one or more ReadyToRunHeader records:
      u32 'RTR\0' (0x00525452), u16 major, u16 minor, u32 flags,
      u16 nSections, u8 entrySize(=16), u8 entryType(=1),
      nSections * { i32 sectionId, i32 size, u64 startVA }
  Section id = 300 + ReflectionMapBlob id:
      301 TypeMap, 306 InvokeMap, 308 CommonFixupsTable, 313 EmbeddedMetadata, ...

  EmbeddedMetadata blob: u32 0xDEADDFFD, then ScopeDefinitionHandleCollection at offset 4.
  Handle value = (HandleType << 25) | recordOffset; recordOffset is 25 bits.
  Typed record-ref fields in records: compressed plain record offset.
  Polymorphic Handle fields: compressed (offset << 7) | handleType.
  Collections: compressed count, then per-element compressed offsets (inline in record).
  ConstantStringValue record: compressed byteCount + UTF-8 bytes.

  InvokeMap blob: NativeHashtable:
      u8 header (bits 2.. = bucket shift, bits 0..1 = entryIndexSize-1),
      (2^shift) bucket entries of (entryIndexSize) bytes each (relative to base=after header),
      then entry stream: per entry: u8 lowHashcode, signed delta (self-relative) -> vertex offset.
  InvokeMap vertex (InvokeTableFlags):
      flags, methodMetadataOffset (& 0x1FFFFFF), declaringTypeFixupIndex,
      [entrypointFixupIndex if HasEntrypoint=0x20],
      [dynamicInvokeCookie if !NeedsParameterInterpretation=0x80],
      [generic args if IsGenericMethod=0x02]

  CommonFixupsTable: array of i32 self-relative pointers:
      targetVA = blobVA + 4*index + (i32)value
"""
import json
import os
import struct
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))

EXE_PATH = os.path.join(ROOT, "ClassicUO.exe")
OUT_PATH = os.path.join(ROOT, "mrt_map.json")

RTR_SIGNATURE = 0x00525452          # 'RTR'
MD_SIGNATURE = 0xDEADDFFD           # NativeFormat metadata signature
METADATA_OFFSET_MASK = 0x1FFFFFF    # MetadataManager.MetadataOffsetMask

# ReflectionMapBlob -> ReadyToRunSectionType (300 + blobId)
SEC_TYPE_MAP = 301
SEC_INVOKE_MAP = 306
SEC_COMMON_FIXUPS = 308
SEC_EMBEDDED_METADATA = 313
SEC_RUNTIME_FUNCTIONS = 102

# InvokeTableFlags
F_HasVirtualInvoke = 0x01
F_IsGenericMethod = 0x02
F_IsDefaultConstructor = 0x08
F_RequiresInstArg = 0x10
F_HasEntrypoint = 0x20
F_NeedsParameterInterpretation = 0x80
F_CallingConventionMask = 0x7000

HANDLE_TYPE = {
    0x1A: 'ConstantStringValue', 0x21: 'CustomAttribute', 0x22: 'Event', 0x23: 'Field',
    0x26: 'GenericParameter', 0x27: 'MemberReference', 0x28: 'Method',
    0x29: 'MethodInstantiation', 0x2A: 'MethodSemantics', 0x2B: 'MethodSignature',
    0x2F: 'NamespaceDefinition', 0x30: 'NamespaceReference', 0x31: 'Parameter',
    0x33: 'Property', 0x36: 'QualifiedMethod', 0x38: 'ScopeDefinition',
    0x39: 'ScopeReference', 0x3A: 'TypeDefinition', 0x3B: 'TypeForwarder',
    0x3D: 'TypeReference', 0x3E: 'TypeSpecification',
}


class BadImage(Exception):
    pass


# ---------------------------------------------------------------- PE loading

class PEImage:
    def __init__(self, path):
        self.data = open(path, 'rb').read()
        d = self.data
        pe = struct.unpack_from('<I', d, 0x3C)[0]
        if struct.unpack_from('<I', d, pe)[0] != 0x4550:
            raise BadImage('not a PE')
        nsec = struct.unpack_from('<H', d, pe + 6)[0]
        opt = pe + 24
        magic = struct.unpack_from('<H', d, opt)[0]
        assert magic == 0x20B, 'expected PE32+'
        self.image_base = struct.unpack_from('<Q', d, opt + 24)[0]
        soff = opt + 240
        self.sections = []
        for i in range(nsec):
            b = soff + 40 * i
            name = d[b:b + 8].rstrip(b'\x00').decode()
            vsz, va, rsz, rptr = struct.unpack_from('<4I', d, b + 8)
            self.sections.append((name, va, vsz, rptr, rsz))

    def rva_to_off(self, rva):
        for name, va, vsz, rptr, rsz in self.sections:
            if va <= rva < va + max(vsz, rsz):
                return rptr + (rva - va)
        return None

    def section_bounds(self, name):
        for n, va, vsz, rptr, rsz in self.sections:
            if n == name:
                return va, va + vsz
        return None


# ------------------------------------------------- NativeFormat decoders

def decode_unsigned(buf, pos):
    """NativePrimitiveDecoder.DecodeUnsigned -> (value, new_pos)."""
    b0 = buf[pos]
    if (b0 & 1) == 0:
        return b0 >> 1, pos + 1
    if (b0 & 2) == 0:
        return (b0 >> 2) | (buf[pos + 1] << 6), pos + 2
    if (b0 & 4) == 0:
        return (b0 >> 3) | (buf[pos + 1] << 5) | (buf[pos + 2] << 13), pos + 3
    if (b0 & 8) == 0:
        return ((b0 >> 4) | (buf[pos + 1] << 4) | (buf[pos + 2] << 12)
                | (buf[pos + 3] << 20)), pos + 4
    if (b0 & 16) == 0:
        return struct.unpack_from('<I', buf, pos + 1)[0], pos + 5
    raise BadImage('bad unsigned encoding at %#x' % pos)


def decode_signed(buf, pos):
    b0 = buf[pos]
    if (b0 & 1) == 0:
        v = b0 >> 1
        if b0 & 0x80:
            v -= 0x80
        return v, pos + 1
    if (b0 & 2) == 0:
        v = (b0 >> 2) | (buf[pos + 1] << 6)
        if buf[pos + 1] & 0x80:
            v -= 0x4000
        return v, pos + 2
    if (b0 & 4) == 0:
        v = (b0 >> 3) | (buf[pos + 1] << 5) | (buf[pos + 2] << 13)
        if buf[pos + 2] & 0x80:
            v -= 0x200000
        return v, pos + 3
    if (b0 & 8) == 0:
        v = ((b0 >> 4) | (buf[pos + 1] << 4) | (buf[pos + 2] << 12)
             | (buf[pos + 3] << 20))
        if buf[pos + 3] & 0x80:
            v -= 0x10000000
        return v, pos + 4
    if (b0 & 16) == 0:
        return struct.unpack_from('<i', buf, pos + 1)[0], pos + 5
    raise BadImage('bad signed encoding at %#x' % pos)


def skip_int(buf, pos):
    return decode_unsigned(buf, pos)[1]


def read_poly_handle(buf, pos):
    """Polymorphic Handle field: compressed (offset << 7) | handleType."""
    raw, pos = decode_unsigned(buf, pos)
    return (raw >> 7, raw & 0x7F), pos   # (offset, type)


def read_string(buf, pos):
    n, p = decode_unsigned(buf, pos)
    if n == 0:
        return '', p
    return buf[p:p + n].decode('utf-8', 'replace'), p + n


def skip_collection(buf, pos):
    """Collection field: compressed count + count compressed elements. Returns new pos."""
    count, pos = decode_unsigned(buf, pos)
    for _ in range(count):
        _, pos = decode_unsigned(buf, pos)
    return pos


def read_offset_collection(buf, pos):
    """Collection of typed handles -> (list of record offsets, new pos)."""
    count, pos = decode_unsigned(buf, pos)
    out = []
    for _ in range(count):
        v, pos = decode_unsigned(buf, pos)
        out.append(v)
    return out, pos
def skip_bytes_collection(buf, pos):
    """ByteCollection field: compressed count + count raw bytes."""
    count, pos = decode_unsigned(buf, pos)
    return pos + count



# ------------------------------------------------- metadata blob walking

class MetadataBlob:
    def __init__(self, blob):
        self.buf = blob
        if struct.unpack_from('<I', blob, 0)[0] != MD_SIGNATURE:
            raise BadImage('bad metadata signature')
        self.types = {}        # typedef offset -> {'name','ns','methods':[offs]}
        self.methods = {}      # method record offset -> {'name','type_off'}
        self.ns_names = {}     # namespace def offset -> name string
        self.ns_parent = {}    # namespace def offset -> (parentOffset, parentType)
        self.scope_names = []  # assembly names

    def string_at(self, off):
        if off == 0:
            return ''
        s, _ = read_string(self.buf, off)
        return s

    def walk(self):
        buf = self.buf
        scopes, _ = read_offset_collection(buf, 4)
        for sc_off in scopes:
            self._walk_scope(sc_off)
        # resolve full type names
        for off, t in self.types.items():
            t['fullname'] = self._type_fullname(off)

    def _walk_scope(self, off):
        buf = self.buf
        pos = off
        _, pos = decode_unsigned(buf, pos)              # flags
        name_off, pos = decode_unsigned(buf, pos)       # name
        _, pos = decode_unsigned(buf, pos)              # hashAlgorithm
        for _ in range(4):
            _, pos = decode_unsigned(buf, pos)          # version parts
        pos = skip_bytes_collection(buf, pos)           # publicKey
        _, pos = decode_unsigned(buf, pos)              # culture
        root_ns, pos = decode_unsigned(buf, pos)        # rootNamespaceDefinition
        # entryPoint, globalModuleType, customAttributes, moduleName, mvid,
        # moduleCustomAttributes - skip
        self.scope_names.append(self.string_at(name_off))
        if root_ns:
            self._walk_namespace(root_ns, [])

    def _walk_namespace(self, off, ns_path):
        buf = self.buf
        if off in self.ns_names:
            return
        pos = off
        (p_off, p_type), pos = read_poly_handle(buf, pos)   # parentScopeOrNamespace
        name_off, pos = decode_unsigned(buf, pos)           # name
        td_off = pos
        type_defs, pos = read_offset_collection(buf, pos)   # typeDefinitions
        _, pos = read_offset_collection(buf, pos)           # typeForwarders
        sub_ns, pos = read_offset_collection(buf, pos)      # namespaceDefinitions
        name = self.string_at(name_off)
        self.ns_names[off] = name
        self.ns_parent[off] = (p_off, p_type)
        my_path = ns_path + ([name] if name else [])
        for td in type_defs:
            self._walk_typedef(td, my_path, enclosing=None)
        for sub in sub_ns:
            self._walk_namespace(sub, my_path)

    def _walk_typedef(self, off, ns_path, enclosing):
        buf = self.buf
        if off in self.types:
            return
        pos = off
        flags, pos = decode_unsigned(buf, pos)              # flags
        _, pos = read_poly_handle(buf, pos)                 # baseType
        _, pos = decode_unsigned(buf, pos)                  # namespaceDefinition
        name_off, pos = decode_unsigned(buf, pos)           # name
        _, pos = decode_unsigned(buf, pos)                  # size
        _, pos = decode_unsigned(buf, pos)                  # packingSize
        enc_off, pos = decode_unsigned(buf, pos)            # enclosingType
        nested, pos = read_offset_collection(buf, pos)      # nestedTypes
        methods, pos = read_offset_collection(buf, pos)     # methods
        # fields, properties, events, genericParameters, interfaces, customAttributes
        name = self.string_at(name_off)
        tinfo = {
            'name': name, 'ns_path': ns_path, 'enclosing': enc_off or None,
            'flags': flags, 'methods': [],
        }
        self.types[off] = tinfo
        for m in methods:
            mname = self._method_name(m)
            if mname is not None:
                tinfo['methods'].append(m)
                self.methods[m] = {'name': mname, 'type_off': off}
        for n in nested:
            self._walk_typedef(n, ns_path, enclosing=off)

    def _method_name(self, off):
        buf = self.buf
        if off == 0 or off >= len(buf):
            return None
        try:
            pos = off
            _, pos = decode_unsigned(buf, pos)          # flags
            _, pos = decode_unsigned(buf, pos)          # implFlags
            name_off, pos = decode_unsigned(buf, pos)   # name
            return self.string_at(name_off)
        except (BadImage, IndexError, struct.error):
            return None

    def _ns_fullname(self, ns_off):
        parts = []
        seen = set()
        while ns_off and ns_off not in seen:
            seen.add(ns_off)
            nm = self.ns_names.get(ns_off)
            if nm is None:
                # walk on demand (namespace referenced but not yet visited)
                break
            if nm:
                parts.append(nm)
            p_off, p_type = self.ns_parent.get(ns_off, (0, 0))
            if p_type != 0x2F:   # parent is ScopeDefinition -> stop
                break
            ns_off = p_off
        return '.'.join(reversed(parts))

    def _type_fullname(self, off):
        t = self.types.get(off)
        if t is None:
            return '?'
        name = t['name']
        enc = t['enclosing']
        while enc:
            e = self.types.get(enc)
            if e is None:
                break
            name = e['name'] + '+' + name
            enc = e['enclosing']
        ns = '.'.join(t['ns_path'])
        return (ns + '.' + name) if ns else name


# ------------------------------------------------- hashtable (invoke map)

def enum_hashtable(buf):
    """NativeHashtable: yields vertex offsets."""
    header = buf[0]
    base = 1
    shift = header >> 2
    if shift > 31:
        raise BadImage('bad hashtable header')
    bucket_mask = (1 << shift) - 1
    idx_size_code = header & 3
    if idx_size_code > 2:
        raise BadImage('bad entry index size')
    idx_size = (1, 2, 4)[idx_size_code]  # 0->u8, 1->u16, 2->u32

    def read_idx(i):
        o = base + i * idx_size
        if idx_size == 1:
            return buf[o]
        if idx_size == 2:
            return struct.unpack_from('<H', buf, o)[0]
        return struct.unpack_from('<I', buf, o)[0]

    # Bucket array has bucketMask+2 slots: slot b holds the stream start of
    # bucket b, slot b+1 its end (see NativeHashtable.GetParserForBucket).
    for b in range(bucket_mask + 1):
        start = read_idx(b)
        end = read_idx(b + 1)
        pos = base + start
        region_end = base + end
        while pos < region_end:
            pos += 1                      # lowHashcode byte
            delta_pos = pos
            delta, pos = decode_signed(buf, pos)
            yield delta_pos + delta


# ------------------------------------------------- main

def find_rtr_headers(img):
    d = img.data
    out = []
    pos = 0
    sig = struct.pack('<I', RTR_SIGNATURE)
    while True:
        pos = d.find(sig, pos)
        if pos < 0:
            break
        pos0 = pos
        pos += 1
        try:
            major, minor = struct.unpack_from('<HH', d, pos0 + 4)
            nsec = struct.unpack_from('<H', d, pos0 + 12)[0]
            esz, etype = d[pos0 + 14], d[pos0 + 15]
            # EntrySize is 16 on older toolchains ({i32 id, i32 size, ptr start});
            # newer NativeAOT toolchains emit 24-byte rows
            # {i32 id, i32 type=1, ptr start, ptr end} (start/end pointers).
            if not (1 <= nsec <= 64 and esz in (16, 24) and minor == 0
                    and 1 <= major <= 64):
                continue
            rows = []
            ok = True
            for i in range(nsec):
                ro = pos0 + 16 + i * esz
                sid, f4 = struct.unpack_from('<ii', d, ro)
                start = struct.unpack_from('<Q', d, ro + 8)[0]
                rva = start - img.image_base
                if esz == 24:
                    end = struct.unpack_from('<Q', d, ro + 16)[0]
                    size = end - start if end >= start else 0
                else:
                    size = f4
                if start < img.image_base or img.rva_to_off(rva) is None or size < 0 or size > 0x4000000:
                    ok = False
                    break
                rows.append((sid, size, rva))
            if ok:
                out.append({'file_off': pos0, 'major': major, 'minor': minor,
                            'entry_size': esz, 'sections': rows})
        except struct.error:
            continue
    return out
def resolve_stub(img, rva):
    """If the entrypoint is a jmp thunk / unboxing stub, follow it.
    Returns (final_rva, stub_kind or None)."""
    kind = None
    for _ in range(4):
        off = img.rva_to_off(rva)
        if off is None:
            break
        b = img.data
        if b[off] == 0xE9:                                  # jmp rel32
            rel = struct.unpack_from('<i', b, off + 1)[0]
            rva = rva + 5 + rel
            kind = kind or 'jmpThunk'
        elif b[off:off + 4] == b'\x48\x83\xc1\x08' and b[off + 4] == 0xE9:
            # add rcx, 8; jmp rel32  (unboxing stub)
            rel = struct.unpack_from('<i', b, off + 5)[0]
            rva = rva + 9 + rel
            kind = kind or 'unboxingStub'
        else:
            break
    return rva, kind



def main():
    img = PEImage(EXE_PATH)
    print('image base %#x' % img.image_base)
    text_lo, text_hi = img.section_bounds('.text')
    print('.text RVA range %#x - %#x' % (text_lo, text_hi))

    headers = find_rtr_headers(img)
    print('found %d ReadyToRunHeader candidate(s)' % len(headers))
    for h in headers:
        ids = sorted(s[0] for s in h['sections'])
        print('  header @ file %#x v%d.%d sections: %s'
              % (h['file_off'], h['major'], h['minor'], ids))

    all_methods = defaultdict(list)     # name -> [entry]
    stats = {'invoke_entries': 0, 'with_entrypoint': 0, 'rva_in_text': 0,
             'resolved_names': 0, 'unresolved': 0, 'generic': 0, 'stubs': 0}

    for h in headers:
        secs = {sid: (size, rva) for sid, size, rva in h['sections']}
        if SEC_EMBEDDED_METADATA not in secs:
            print('  !! no EmbeddedMetadata section, skipping header')
            continue
        md_size, md_rva = secs[SEC_EMBEDDED_METADATA]
        md_off = img.rva_to_off(md_rva)
        blob = img.data[md_off:md_off + md_size]
        md = MetadataBlob(blob)
        md.walk()
        print('  metadata @ RVA %#x size %#x: scopes=%d (%s...) types=%d methods=%d'
              % (md_rva, md_size, len(md.scope_names), ', '.join(md.scope_names[:5]),
                 len(md.types), len(md.methods)))

        fixups = None
        if SEC_COMMON_FIXUPS in secs:
            fx_size, fx_rva = secs[SEC_COMMON_FIXUPS]
            fx_off = img.rva_to_off(fx_rva)
            fixups = (fx_off, fx_size // 4)

        def fixup_target_rva(index):
            fx_off, count = fixups
            if index >= count:
                return None
            rel = struct.unpack_from('<i', img.data, fx_off + 4 * index)[0]
            return fx_rva + 4 * index + rel   # RVA (self-relative)

        if SEC_INVOKE_MAP not in secs:
            print('  !! no InvokeMap section')
            continue
        im_size, im_rva = secs[SEC_INVOKE_MAP]
        im_off = img.rva_to_off(im_rva)
        im_blob = img.data[im_off:im_off + im_size]

        n_entries = 0
        for voff in enum_hashtable(im_blob):
            n_entries += 1
            stats['invoke_entries'] += 1
            try:
                pos = voff
                flags, pos = decode_unsigned(im_blob, pos)
                md_offset, pos = decode_unsigned(im_blob, pos)
                dt_idx, pos = decode_unsigned(im_blob, pos)
                entry = {'mdOffset': md_offset, 'invokeFlags': flags}
                if fixups is not None:
                    dt_rva = fixup_target_rva(dt_idx)
                    if dt_rva is not None:
                        entry['eeTypeRva'] = dt_rva
                if flags & F_IsGenericMethod:
                    stats['generic'] += 1
                    entry['generic'] = True
                if flags & F_HasEntrypoint:
                    ep_idx, pos = decode_unsigned(im_blob, pos)
                    stats['with_entrypoint'] += 1
                    if fixups is not None:
                        rva = fixup_target_rva(ep_idx)
                        if rva is not None:
                            entry['rva'] = rva
                            entry['va'] = img.image_base + rva
                            if text_lo <= rva < text_hi:
                                stats['rva_in_text'] += 1
                                final_rva, stub = resolve_stub(img, rva)
                                if stub:
                                    entry['stub'] = stub
                                    entry['codeRva'] = final_rva
                                    entry['codeVa'] = img.image_base + final_rva
                                    stats['stubs'] += 1
                            else:
                                entry['rvaOutOfText'] = True
                if flags & F_IsDefaultConstructor:
                    entry['defaultCtor'] = True
                # resolve name via metadata
                mi = md.methods.get(md_offset)
                if mi is not None:
                    t = md.types.get(mi['type_off'], {})
                    entry['name'] = mi['name']
                    entry['type'] = t.get('fullname', '?')
                    entry['typeOffset'] = mi['type_off']
                    stats['resolved_names'] += 1
                    all_methods[mi['name']].append(entry)
                else:
                    stats['unresolved'] += 1
                    entry['name'] = None
                    all_methods['<unresolved>'].append(entry)
            except (BadImage, IndexError, struct.error):
                stats['unresolved'] += 1
        print('  invoke map @ RVA %#x size %#x: %d entries'
              % (im_rva, im_size, n_entries))

        if SEC_RUNTIME_FUNCTIONS in secs:
            rf_size, rf_rva = secs[SEC_RUNTIME_FUNCTIONS]
            print('  RuntimeFunctions @ RVA %#x size %#x (%d triples)'
                  % (rf_rva, rf_size, rf_size // 12))

    # ------------------------------------------------------- emit map
    out = {
        'imageBase': img.image_base,
        'textRange': [text_lo, text_hi],
        'stats': stats,
        'methods': {},
        'byRva': {},
    }
    total = 0
    for name, entries in sorted(all_methods.items()):
        if name == '<unresolved>':
            continue
        recs = []
        for e in entries:
            rec = {
                'type': e.get('type'),
                'rva': e.get('rva'),
                'va': ('%#x' % e['va']) if 'va' in e else None,
                'eeTypeRva': e.get('eeTypeRva'),
                'mdOffset': e['mdOffset'],
                'invokeFlags': e['invokeFlags'],
            }
            if e.get('generic'):
                rec['generic'] = True
            if e.get('rvaOutOfText'):
                rec['rvaOutOfText'] = True
            if e.get('stub'):
                rec['stub'] = e['stub']
                rec['codeRva'] = e['codeRva']
                rec['codeVa'] = '%#x' % e['codeVa']
            recs.append(rec)
            total += 1
            if e.get('rva') is not None:
                out['byRva'][str(e['rva'])] = {'name': name, 'type': e.get('type')}
        out['methods'][name] = recs
    out['stats']['total_named_entries'] = total

    with open(OUT_PATH, 'w') as f:
        json.dump(out, f, indent=1)
    print('wrote %s: %d distinct names, %d entries' % (OUT_PATH, len(out['methods']), total))

    # -------------------------------------------- cross-check vs .pdata
    # Every reflectable entrypoint should be a RuntimeFunction begin address.
    pdata = img.section_bounds('.pdata')
    if pdata:
        pdata_off = img.rva_to_off(pdata[0])
        n_funcs = (pdata[1] - pdata[0]) // 12
        begins = set()
        for i in range(n_funcs):
            begins.add(struct.unpack_from('<I', img.data, pdata_off + 12 * i)[0])
        checked = matched = 0
        for entries in out['methods'].values():
            for e in entries:
                if e.get('rva') is not None:
                    checked += 1
                    if e['rva'] in begins:
                        matched += 1
        print('pdata cross-check: %d/%d entrypoint RVAs are RuntimeFunction begins (%d functions in .pdata)'
              % (matched, checked, n_funcs))
        stats['pdata_matched'] = matched
        stats['pdata_checked'] = checked
        with open(OUT_PATH, 'w') as f:
            json.dump(out, f, indent=1)
    print('stats:', stats)

    # ------------------------------------------------------- spot checks
    for probe in ('OutlandsServerPacket', 'Send_TimeSyncPingReq', 'Send_WalkRequest',
                  'BuildPacketTable', 'OutlandsItemNameResponse'):
        hits = out['methods'].get(probe)
        if hits:
            for hh in hits[:3]:
                print('  SPOT %-28s -> %s.%s rva=%s'
                      % (probe, hh['type'], probe, hex(hh['rva']) if hh['rva'] else None))
        else:
            # maybe it is a type, not a method
            print('  SPOT %-28s -> not a method name in map' % probe)


if __name__ == '__main__':
    main()
