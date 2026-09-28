# NativeFormat (.mrt) metadata parse of ClassicUO.exe — notes

## Deliverables

- `mrt_parse.py` — stdlib-only parser. Run: `python mrt_parse.py` (paths hardcoded at top).
- `mrt_map.json` — the name → method-RVA map:
  - `methods`: `{ "<MethodName>": [ {type, rva, va, eeTypeRva, mdOffset, invokeFlags, ...} ] }`
    (38,195 distinct names, 95,389 entries — overloads/nested types share names, hence lists)
  - `byRva`: reverse map `rva -> {name, type}` for entrypoints
  - per entry: `rva`/`va` = entrypoint; `stub` = `jmpThunk` | `unboxingStub` with `codeRva`/`codeVa`
    = resolved real body; `eeTypeRva` = declaring type's MethodTable (EEType) address;
    `mdOffset` = Method record offset inside the EmbeddedMetadata blob; `generic` = generic def.
  - `imageBase` = 0x140000000, `textRange` = [0x1000, 0x1786638).

## How the metadata was located

1. Scanned the PE for the NativeAOT module header signature `'RTR\0'` (0x00525452).
   One real header at **file offset 0x38fcce8**, format v16.0, 33 sections.
2. This toolchain emits **24-byte section rows** `{ i32 id, i32 type(=1), u64 start, u64 end }`
   (start/end *pointers*; EntrySize=24). Older toolchains emit 16-byte rows
   `{ i32 id, i32 size, u64 start }` — parser handles both.
   Row id 204 (TypeManagerIndirection) has type=0 and no end pointer.
3. Section id = 300 + ReflectionMapBlob id. Present and used:
   - **313 EmbeddedMetadata** @ RVA 0x1787f40, size 0x72870d (7.4 MB) — the `.mrt` blob
   - **306 InvokeMap** @ RVA 0x2d47c94, size 0x17d34c — method→entrypoint hashtable
   - **308 CommonFixupsTable** @ RVA 0x303effc, size 0x79a1c (124,935 rel32 slots)
   - also present: 301 TypeMap, 302/303/304 array/pointer/funcptr maps, 307 VirtualInvokeMap,
     309 FieldAccessMap, 310 CCtorContextMap, 311 ByRefTypeMap, 316/317 struct+delegate
     marshalling stub maps, 318/319 GVM tables, 321/322 type/method template maps,
     324 ResourceIndex, 325 ResourceData, 327 StackTraceEmbeddedMetadata,
     330–336 native layout blobs, 201/202/205/206/213 runtime sections.

## NativeFormat encoding (as verified against this binary)

- Integers: NativePrimitiveDecoder variable-length (low-tag bits: 1/2/3/4-byte forms,
  0x?F-prefixed 5th form = raw u32/u64 follows). Signed form sign-extends the top payload bit.
- Metadata blob: `u32 0xDEADDFFD`, then ScopeDefinitionHandleCollection at offset 4.
- Handle value = `(HandleType << 25) | recordOffset` (offset = 25 bits, HandleType.Method=0x28,
  TypeDefinition=0x3A, ScopeDefinition=0x38, NamespaceDefinition=0x2F, ConstantStringValue=0x1A).
- Typed record-ref fields: compressed **plain record offset**.
- Polymorphic `Handle` fields (e.g. TypeDefinition.BaseType, NamespaceDefinition.Parent):
  compressed `(offset << 7) | handleType`.
- Handle collections: compressed count + per-element compressed offsets, inline in the record.
- **ByteCollection** (publicKey, mvid): compressed count + **raw bytes** (not compressed ints!) —
  this was the one deviation from naive expectation.
- ConstantStringValue record = compressed byteCount + UTF-8 bytes at the record offset.
  (This is the "string heap" seen at file offsets ~0x1860000–0x1a20000.)
- Record layouts walked: ScopeDefinition → root NamespaceDefinition → (recurse) →
  TypeDefinition (flags, baseType, ns, name, size, packing, enclosing, nested[], methods[], …)
  → Method (flags, implFlags, name, signature, parameters[], genericParams[], customAttrs[]).

## InvokeMap (method → RVA)

- NativeHashtable: `u8 header` (bits≥2 = bucket-shift, bits 0–1 = index size code where
  0→u8, 1→u16, 2→u32 — **code 2 means 4-byte slots**), then (2^shift)+1 slot array,
  then entry stream: per entry `u8 lowHashbyte, signed self-relative delta → vertex`.
- Vertex grammar (ReflectionInvokeMapNode.cs): `flags, methodMdOffset (& 0x1FFFFFF),
  declaringTypeFixupIdx, [entrypointFixupIdx if HasEntrypoint=0x20],
  [dynInvokeCookie if !NeedsParameterInterpretation=0x80], [generic args if IsGenericMethod=0x02]`.
- Fixup index → address: CommonFixupsTable slot i is an **i32 self-relative pointer**:
  `targetRVA = fixupsRVA + 4*i + (i32)slot`. Entrypoints land in .text; declaring-type
  fixups land on EETypes (MethodTable) in .data/.rdata.

## Verification

- 95,389/95,389 invoke entries resolved to a Method record + name (0 unresolved).
- 86,780 entries have entrypoints; **all RVAs inside .text** [0x1000, 0x1786638).
- .pdata cross-check (163,935 RuntimeFunctions): 77,401 entrypoints are exact function
  begins; the remaining 9,379 are stubs (`E9 rel32` jmp thunk or `48 83 C1 08; E9 rel32`
  unboxing stub) whose resolved target is a RuntimeFunction begin in **100%** of cases.
  (A handful of stubs chain to `FF 25 [rip+x]` IAT thunks for P/Invoke — no managed body;
  those entrypoints are themselves real pdata functions.)
- 8,609 entries without entrypoints = abstract/interface declarations (HasEntrypoint unset).
- Spot checks:
  - `ClassicUO.Network.NetClientExt.Send_TimeSyncPingReq` → RVA 0x17e940
  - `ClassicUO.Network.NetClientExt.Send_WalkRequest` → RVA 0x179c60
  - `ClassicUO.Network.PacketsTable.BuildPacketTable` → RVA 0x1a1140
  - `Assistant.PacketHandlers.OutlandsServerPacket` → RVA 0x64eb0
  - `Assistant.PacketHandlers.OutlandsItemNameResponse` → RVA 0x64f20

## Corrections to earlier assumptions

- The two dword tables at VA 0x141ef3520 / 0x141f02564 are **not** RuntimeFunctions; they sit
  inside module section 325 (BlobIdResourceData, RVA 0x1eb064d–0x2d0c946). Real function
  boundaries: PE `.pdata` (EXCEPTION dir, 163,935 triples).
- The acceptance range ".text up to 0x19ee400" was off; actual .text ends at RVA 0x1786638
  (VSZ 0x1785638). All recovered RVAs land inside the true .text.

## Residual gaps

- Generic-method entries map to the generic *definition* (instantiation args not decoded —
  not needed for name→RVA).
- `VirtualInvokeMap` (307), `TypeMap` (301) and other blobs are located but not parsed
  (TypeMap would give EEType → TypeDefinition directly; we already get types via metadata).
- Fields/properties/events are walked only for skipping, not emitted.
- Reference C# sources consulted are cached in `ref/` (dotnet/runtime@main); the binary was
  built with an RTR format v16 toolchain (24-byte rows), parsed with the current spec.
