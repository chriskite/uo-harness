# Outlands client device identifier — Ghidra RE (DeviceIdRE)

Binary analysed: workspace copy `ClassicUO.exe` at the laptop's repo root (70,434,992 B). Per docs/NOTES.md this is
**1.0.2.544**, not the installed 1.0.2.550. All addresses below are 544 addresses. The runtime checks (§6) cover
captures from both 544 and 550 sessions.
Tooling: Ghidra 12.1.4 headless on the existing analysed project (`ghidra/UOProject`, `-readOnly`), script
`ghidra_scripts/DeviceIdRE.java` (modes: str/astr literal xrefs, ext import xrefs, fn/callers decompile, refs, revtree).
Names come from `mrt_map.json` where the method is reflectable. Other names were assigned by me from literals and
behaviour; the evidence for each is listed in §7. Raw run outputs: `%TEMP%/devre/out{1..12}.txt`.

Tags: **[V]** = seen in decompilation/xrefs/bytes; **[I]** = inference.

## 1. Answer

* **There is no TPM-based identifier.** [V] The `TPMManufacturer`/`TPMModel`/`TPMVersion` literals sit in the .NET
  `OidLookup` friendly-name table (`FUN_140a24120`, pairs `2.23.133.2.1/2/3` → names). That is runtime X.509 OID
  naming, not Outlands code. [V] `"Microsoft Platform Crypto Provider"` has exactly one xref. It is in the
  `CngProvider.MicrosoftPlatformCryptoProvider` getter `0x140a22010`, whose only caller is `CngKey.KeySize`
  `0x140a29e70` (literals `PublicKeyLength`/`Length`/`ECCCurveName`/`nistP192`). It only compares the provider and
  never opens the TPM KSP. [V] The imports are NCryptImportKey, OpenKey, OpenStorageProvider, GetProperty,
  SetProperty, FreeObject and DeleteKey. **No** NCryptCreatePersistedKey/FinalizeKey/SignHash/ExportKey and no
  Tbsi_* are imported, and none of those names occur in strings.txt. Every NCrypt wrapper's reverse call tree
  (depth 8) stays inside System.Security.Cryptography (CngKey/CngProvider/SafeNCrypt*) and ends in vtable/DATA refs.
* **The device identifier is a random GUID, persisted disguised as a shader cache:**
  `%APPDATA%\FNA3D\D3D11_Shader_Cache.blob`.
* **The GUID is sent as an HTTP header on every login-API request** (`X-Device-ID`, plus the derived
  `X-Request-Context`) to `https://login.uooutlands.com`.
* **It reaches the game server (:2593) indirectly.** [V runtime] The JWT the client forwards in the custom `0x91`
  packet has a `.../claims/hash` claim. That claim equals the locally stored device GUID, and it was identical in
  all 32 captured sessions (544 and 550). The client writes no device field into any game packet itself [V]. The
  login server puts the GUID into the signed JWT.

## 2. Device ID: generation, persistence, encryption

`LoginScene.GetDevice @ 0x1402d57c0` [V]:
```
dir  = Path.Combine(Environment.GetFolderPath(0x1a /*ApplicationData*/), "FNA3D");      // 14076bfc0 case 0x1a = FOLDERID_RoamingAppData {3EB685DB-…}, env "APPDATA"
if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
path = Path.Combine(dir, "D3D11_Shader_Cache.blob");
if (File.Exists(path)) {
    s = Decrypt(File.ReadAllText(path, <Encoding @DAT_143f8d7e0+8>));
    if (s.Length == 0x24) return s;                 // 36 = GUID "D" format
    File.Delete(path);
}
g = Guid.NewGuid().ToString("d");                  // 14079fe10 -> CoCreateGuid ; 14079fcd0 'd' -> len 0x24
File.WriteAllText(path, Encrypt(g), new UTF8Encoding(false));   // 140826cb0: codepage 0xfde9, no BOM
return g;
```
* `Encrypt @ 0x1402d5700`: `Convert.ToBase64String(ProtectedData.Protect(Encoding.Unicode.GetBytes(s), entropy, scope=1))` [V].
  * `0x140a33aa0`: `scope==1` → `CryptProtectData` flags `5` = UI_FORBIDDEN|**LOCAL_MACHINE** [V].
  * The same Protect/Unprotect helpers are also called by `DpapiProtectedConfigurationProvider.EncryptText/DecryptText` (mrt names) [V].
* `Decrypt @ 0x1402d5760`: the reverse (FromBase64String → Unprotect(scope 1) → Encoding.GetString) [V].
* Entropy: `*(LoginScene statics + 0x18)` (statics base slot `0x143f8c180`). No code writes it, so it is a
  preinitialised static. Preinit blob `0x1438cfda8`: field +0x10 = the HMAC key the code uses directly
  (`0x143f62c88`), field +0x18 = frozen `byte[6] {04 08 0F 10 17 2A}` at `0x143f62cb0` [V bytes].
  [I] Mapping the slot to the blob uses NativeAOT's relative-pointer slot format.
  [V runtime] That 6-byte entropy decrypts the real blob on this machine, and decryption without entropy fails (§6).
* Encodings: the plaintext GUID is UTF-16LE (Encoding.Unicode). [V runtime] It decrypts to 72 bytes = 36 UTF-16 chars.
  The file holds base64 text.
* Effect: DPAPI-LocalMachine with entropy. [I] A copied blob won't decrypt on another PC, so that PC gets a fresh
  GUID. Deleting the file also yields a new GUID. The ID is per Windows install, not per account. Nothing
  hardware-derived goes into it.
* Callers: only `LoginScene.Load @ 0x1402d3f90` (result discarded; called when the settings flag at +0x7e skips the
  HttpClient) and `LoginScene.CreateHttpClient @ 0x1402d4370` [V xrefs]. The literal `D3D11_Shader_Cache.blob` has
  one xref (GetDevice) [V].

## 3. HTTP transport (login.uooutlands.com)

`LoginScene.CreateHttpClient @ 0x1402d4370` [V]:
```
dev = GetDevice();
ver = <client System.Version @ *(DAT_143f8bec0+0x10)>;  verStr = ver.ToString(fieldCount(ver));
handler = new SocketsHttpHandler{…};  client = new HttpClient(handler, true);
client.DefaultRequestHeaders.Accept = application/json
client.DefaultRequestHeaders.Add("X-Device-ID",        dev);
client.DefaultRequestHeaders.Add("X-Client-Version",   verStr);
client.DefaultRequestHeaders.Add("X-GameFiles-Version",<int static DAT_143f7b1d0>.ToString());
client.DefaultRequestHeaders.Add("X-Request-Context",  GetRequestTraceContext(dev));
client.BaseAddress = new Uri(settings.loginserver contains "cloud" ? "https://login.uooutlands.com" : settings.loginserver);
```
* `GetRequestTraceContext @ 0x1402d54f0` [V]:
  `hex8 = Convert.ToHexString(HMACSHA256(key16 @0x143f62c88).ComputeHash(UTF8($"{ver.Major}{ver.Revision}{dev}")), 0, 8)`,
  then a string transform `FUN_1407e5ff0`. [I] That transform is ToLowerInvariant.
  * HMAC algorithm [V]: ctor `0x140a23450`, HashName "SHA256", block 0x40.
  * Version fields [V]: +8 Major, +0x14 Revision (`0x1407bce00` = Version field count).
  * The header is a 16-hex-char keyed integrity tag that binds the device ID to the client build. [I] Its purpose
    is to make forged X-Device-ID values detectable.
* `account/verifydevice` (MoveNext `0x1403b2ed0`) [V]: POST `VerifyDeviceModel{Identifier = LoginScene+0x80
  (DeviceVerificationIdentifier, issued by the server), Code = user-typed e-mail code}`.
  * JSON options [V]: `new JsonSerializerOptions(JsonSerializerDefaults.Web)`, `0x140a63590` case 1 = camelCase
    policy + case-insensitive. Wire names are therefore `identifier`/`code`.
  * The device GUID itself is not in the body. It rides in the headers.
  * Status handling: 401 / 418 / other → error strings.
* `account/checkverificationstatus?identifier=` (MoveNext `0x1403aebc0`) and `deviceverification` (`0x1403aa750`)
  make up the e-mail 2FA polling flow [V literals].
* Request models (source-gen PropInit literal sets) [V]:
  * `ClientLoginRequestModel{Email,Password}`
  * `WebLoginRequestModel{Email,Password}`
  * `GameLoginRequestModel{AccountName}`
  * `VerifyDeviceModel{Identifier,Code}`
  * Response `GameLoginResponse{Data}`
  * **No request model has a DeviceId/hardware field.**
* `LoginServer.Shared.LoginLogInfoDTO{Timestamp,Details,Ip,DeviceId}` (getter `0x1404d4310`, setter `0x1404d4320`)
  has only DATA (metadata) refs [V]. It belongs to `UserInformationResponseDTO.RecentLoginLogs`, a shared
  server/admin DTO. [I] The client does not use it.
* Every login-API request goes through this one HttpClient (`LoginScene+0x20`) [V].
  * Callers of `LoginScene.SendAsync 0x1402d5960`: 1403b0080, 1403b0560, 1403b1ce0 [V].
  * The getclientinfo MoveNext `0x1403b0bd0` rebuilds the client via `CreateHttpClient(this,1)` when AllowIPv6 is set [V].
  * [I] All endpoints get the X-* headers. That follows from .NET DefaultRequestHeaders semantics.

## 4. Game protocol (:2593)

* `0x91` `Send_GameLogin @ 0x1401480e0`: `0x91` + string(param_2) + string(param_3) [V]. The caller (MoveNext
  `0x1403b1ce0`) first POSTs `login/gamelogin` with `Authorization: Bearer <scene+0x28>` and
  `GameLoginRequestModel{AccountName}`. It stores `GameLoginResponse.Data` at NetClient+0x80, then calls
  `Send_GameLogin(account, Data)` [V]. Data is the game JWT (docs/CIPHER.md: `91 <len> <name>\0 <JWT>`).
  **No device field is written by the client**, but the JWT carries the device GUID as `.../claims/hash` (§6).
* `0xEF` `Send_Seed @ 0x1401474c0`: `0xEF` + big-endian u32 from a static (`DAT_143f7b308`) + zero padding,
  5 bytes in the Outlands table [V]. Nothing device-derived is visible. [I] The static is a session seed. I did not
  trace where it comes from.
* `0xFF` `Send_Info @ 0x140147ae0` (sent only from `PacketHandlers.OutlandsProtocol`): `0xFF` + `0x01000000` +
  padding [V]. It holds a constant and no identifiers.
* GetDevice has no other callers [V], so no other packet can include it directly.

## 5. Other machine identifiers (runtime vs Outlands code)

| Item | Where | Used by | Sent? |
|---|---|---|---|
| `TPMManufacturer/Model/Version` | OID table cctor `0x140a24120` | .NET crypto runtime | no [V] |
| `Microsoft Platform Crypto Provider` | `0x140a22010` ← `CngKey.KeySize 0x140a29e70` | .NET runtime (provider compare) | no [V] |
| NCrypt* imports | wrappers `0x140a1f530/6e0/980/ac0/c10/8d0`, `0x140a208a0` | CngKey/RSACng etc. (runtime); [I] reachable via TLS/JWT/X509 key import | no |
| GetAdaptersAddresses (MAC/IPs) | `0x14072ca60` (flags 0x1c0) ← `0x14070ea40` ← `0x14070e760` ← `SystemProxyInfo.ConstructSystemProxy 0x14070ae40` | System.Net.Http Windows proxy bypass (`<local>`) | no [V chain; I purpose] |
| GetComputerNameW (Environment.MachineName) | stub `0x14087b010` → `0x14087b080` → getter `0x14076c6e0` | `ClassicUO.Utility.Crypter.Encrypt/Decrypt/CalculateKey` (upstream settings.json password obfuscation), WinForms SystemInformation, PerformanceCounterLib | no (local key only) [V xrefs; I upstream semantics] |
| GetUserNameA | `Assistant.Platform.GetWindowsUserName 0x140073290` | Razor; only a DATA ref, no direct callers | [I] no |
| `ProcessorId` | `ProcessorIdCache` (threading runtime) | .NET runtime | no |
| `deviceManufacturerInfo`/`deviceModelInfo` | `0x140534330` | SixLabors.ImageSharp ICC profile parsing | no |
| volume serial / WMI / registry MachineGuid | not imported/no strings | — | — |
| Public IP | JWT `userdata` claim | issued by the server (from the connection IP) | server-side |

## 6. Runtime confirmation (this machine, read-only, values never printed)

1. `%APPDATA%\FNA3D\D3D11_Shader_Cache.blob` exists. It is 392 B of base64 → 294 B DPAPI blob (`01000000` header).
2. `CryptUnprotectData(entropy = 04 08 0F 10 17 2A)` succeeds and gives 72 B = UTF-16 GUID (36 chars).
   Without entropy it fails.
3. Decoded the JWT in the `0x91` packet of all 32 `logs/session_*.c2s.raw` captures that have one. The
   `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/hash` claim is GUID-shaped. It has **1 distinct value**
   across 32 sessions (32 distinct `jti`; versions 544 and 550), and it **equals the decrypted device GUID**.

## 7. Function identification evidence (non-mrt names)

* `0x14076bfc0` GetFolderPath: SpecialFolder switch, case 0x1a → RoamingAppData GUID + "APPDATA".
* `0x14079fe10`: calls CoCreateGuid.
* `0x14079fcd0`: Guid.ToString format switch (d/n/b/p/x → 0x24/0x20/0x26/0x44).
* `0x140826030`: Directory.Exists (attribute & 0x10).
* `0x140826e20`: File.Exists (rejects trailing `\`/`/`).
* `0x140827100`: WriteAllText (FileMode 2).
* `0x140826cb0`: UTF8Encoding(0xfde9) singleton.
* `0x140a33900`/`0x140a339d0`: ProtectedData.Protect/Unprotect. Shared with `DpapiProtectedConfigurationProvider`;
  the core calls CryptProtectData/CryptUnprotectData.
* `0x140788f20`/`0x1407896d0`: Base64.
* `0x140789c90`: ToHexString(offset,length) (arg names "offset"/"length").
* `0x14071eca0`/`0x14071d370`: HttpHeaders.GetHeaderDescriptor/Add.
* `0x140a63590`: JsonSerializerOptions(JsonSerializerDefaults) (Web: camelCase + case-insensitive + AllowReadingFromString).
* Literal decoding: frozen string = MT(8) + len(4) + UTF-16; code refs point at the object (= literal − 12).

## 8. Not determined

* The source of the `0xEF` seed static `DAT_143f7b308`. The address is shared/merged with many statics in Ghidra.
* The transform `FUN_1407e5ff0` after ToHexString. [I] It is lower-casing.
* Whether the server checks `X-Request-Context` (server-side).
* Whether the 550 build changed any of this. Only the runtime evidence covers 550: same JWT `hash`, so the same device GUID.
* The exact .NET call sites that reach the NCrypt/CngKey code through virtual dispatch (TLS/JWT/X509 import paths).
  Static xrefs end in vtables.

---
## Appendix A — reverse call trees (raw)
```
######## revtree 0x140a1f980 8
   edge d=1 140a1f980 <- <nofunc> via 14412c988 DATA
   edge d=1 140a1f980 <- 140a26310 via 140a26328 UNCONDITIONAL_CALL
   edge d=2 140a26310 <- <nofunc> via 14412d468 DATA
   edge d=2 140a26310 <- 140a29bc0 via 140a29be1 UNCONDITIONAL_CALL
   edge d=2 140a26310 <- 140a29a60 via 140a29a91 UNCONDITIONAL_CALL
   edge d=3 140a29bc0 <- <nofunc> via 14412d7c8 DATA
   edge d=3 140a29bc0 <- 140a20460 via 140a2055a UNCONDITIONAL_CALL
   edge d=3 140a29a60 <- <nofunc> via 14412d7b0 DATA
   edge d=3 140a29a60 <- 140a29a20 via 140a29a45 UNCONDITIONAL_CALL
   edge d=3 140a29a60 <- 141577260 via 141577473 UNCONDITIONAL_CALL
   edge d=4 140a20460 <- <nofunc> via 14412cafc DATA
   edge d=4 140a20460 <- 140a20360 via 140a2037e UNCONDITIONAL_CALL
   edge d=4 140a29a20 <- <nofunc> via 14412d7a4 DATA
   edge d=4 140a29a20 <- 140a27130 via 140a27165 UNCONDITIONAL_CALL
   edge d=4 140a29a20 <- 140a271e0 via 140a2720f UNCONDITIONAL_CALL
   edge d=4 141577260 <- <nofunc> via 1442313ac DATA
   edge d=4 141577260 <- 140a31d50 via 140a31db5 UNCONDITIONAL_CALL
   edge d=4 141577260 <- 140a31df0 via 140a31e58 UNCONDITIONAL_CALL
   edge d=5 140a20360 <- <nofunc> via 14412cae4 DATA
   edge d=5 140a20360 <- <nofunc> via 14331e6c8 DATA
   edge d=5 140a27130 <- <nofunc> via 14412d4f8 DATA
   edge d=5 140a27130 <- 140a27700 via 140a2771b UNCONDITIONAL_CALL
   edge d=5 140a27130 <- 140a27ce0 via 140a27cfb UNCONDITIONAL_CALL
   edge d=5 140a271e0 <- <nofunc> via 14412d504 DATA
   edge d=5 140a271e0 <- 140a276b0 via 140a276c5 UNCONDITIONAL_CALL
   edge d=5 140a271e0 <- 140a27c90 via 140a27ca5 UNCONDITIONAL_CALL
   edge d=5 140a31d50 <- <nofunc> via 14412e2cc DATA
   edge d=5 140a31d50 <- 141576fb0 via 141577096 UNCONDITIONAL_CALL
   edge d=5 140a31df0 <- <nofunc> via 14412e2d8 DATA
   edge d=5 140a31df0 <- 141576fb0 via 141577063 UNCONDITIONAL_CALL
   edge d=6 140a27700 <- <nofunc> via 14412d588 DATA
   edge d=6 140a27700 <- 140a27380 via 140a27489 UNCONDITIONAL_CALL
   edge d=6 140a27700 <- 140a27380 via 140a274ee UNCONDITIONAL_CALL
   edge d=6 140a27ce0 <- <nofunc> via 14412d624 DATA
   edge d=6 140a27ce0 <- 140a278f0 via 140a279f6 UNCONDITIONAL_CALL
   edge d=6 140a27ce0 <- 140a278f0 via 140a27a58 UNCONDITIONAL_CALL
   edge d=6 140a276b0 <- <nofunc> via 14412d570 DATA
   edge d=6 140a276b0 <- 140a27380 via 140a2751c UNCONDITIONAL_CALL
   edge d=6 140a276b0 <- 140a27380 via 140a2757c UNCONDITIONAL_CALL
   edge d=6 140a27c90 <- <nofunc> via 14412d60c DATA
   edge d=6 140a27c90 <- 140a278f0 via 140a27a83 UNCONDITIONAL_CALL
   edge d=6 140a27c90 <- 140a278f0 via 140a27ae0 UNCONDITIONAL_CALL
   edge d=6 141576fb0 <- <nofunc> via 14423137c DATA
   edge d=6 141576fb0 <- 140a2f220 via 140a2fa26 UNCONDITIONAL_CALL
   edge d=7 140a27380 <- <nofunc> via 14412d54c DATA
   edge d=7 140a27380 <- <nofunc> via 14331fb90 DATA
   edge d=7 140a278f0 <- <nofunc> via 14412d5dc DATA
   edge d=7 140a278f0 <- <nofunc> via 14331fc20 DATA
   edge d=7 140a2f220 <- <nofunc> via 14412df30 DATA
   edge d=7 140a2f220 <- <nofunc> via 1433204c0 DATA

######## revtree 0x140a1f530 8
   edge d=1 140a1f530 <- <nofunc> via 14412c91c DATA
   edge d=1 140a1f530 <- 140a29bc0 via 140a29bfb UNCONDITIONAL_CALL
   edge d=2 140a29bc0 <- <nofunc> via 14412d7c8 DATA
   edge d=2 140a29bc0 <- 140a20460 via 140a2055a UNCONDITIONAL_CALL
   edge d=3 140a20460 <- <nofunc> via 14412cafc DATA
   edge d=3 140a20460 <- 140a20360 via 140a2037e UNCONDITIONAL_CALL
   edge d=4 140a20360 <- <nofunc> via 14412cae4 DATA
   edge d=4 140a20360 <- <nofunc> via 14331e6c8 DATA

######## revtree 0x140a1fac0 8
   edge d=1 140a1fac0 <- <nofunc> via 14412c9ac DATA
   edge d=1 140a1fac0 <- 140a1fd30 via 140a1fd51 UNCONDITIONAL_CALL
   edge d=1 140a1fac0 <- 140a263a0 via 140a263d6 UNCONDITIONAL_CALL
   edge d=1 140a1fac0 <- 140a263a0 via 140a26459 UNCONDITIONAL_CALL
   edge d=1 140a1fac0 <- 140a27130 via 140a27193 UNCONDITIONAL_CALL
   edge d=1 140a1fac0 <- 140a271e0 via 140a2723d UNCONDITIONAL_CALL
   edge d=2 140a1fd30 <- <nofunc> via 14412ca00 DATA
   edge d=2 140a1fd30 <- 140a29e70 via 140a29e96 UNCONDITIONAL_CALL
   edge d=2 140a1fd30 <- 140a29e70 via 140a29eaf UNCONDITIONAL_CALL
   edge d=2 140a263a0 <- <nofunc> via 14412d480 DATA
   edge d=2 140a263a0 <- 140a29c90 via 140a29cbf UNCONDITIONAL_CALL
   edge d=2 140a263a0 <- 140a29e70 via 140a29f82 UNCONDITIONAL_CALL
   edge d=2 140a263a0 <- 140a29dc0 via 140a29def UNCONDITIONAL_CALL
   edge d=2 140a27130 <- <nofunc> via 14412d4f8 DATA
   edge d=2 140a27130 <- 140a27700 via 140a2771b UNCONDITIONAL_CALL
   edge d=2 140a27130 <- 140a27ce0 via 140a27cfb UNCONDITIONAL_CALL
   edge d=2 140a271e0 <- <nofunc> via 14412d504 DATA
   edge d=2 140a271e0 <- 140a276b0 via 140a276c5 UNCONDITIONAL_CALL
   edge d=2 140a271e0 <- 140a27c90 via 140a27ca5 UNCONDITIONAL_CALL
   edge d=3 140a29e70 <- <nofunc> via 14412d804 DATA
   edge d=3 140a29e70 <- 140a29d90 via 140a29dab UNCONDITIONAL_CALL
   edge d=3 140a29c90 <- <nofunc> via 14412d7d4 DATA
   edge d=3 140a29c90 <- 140a27750 via 140a27765 UNCONDITIONAL_CALL
   edge d=3 140a29c90 <- 140a29e70 via 140a29f0a UNCONDITIONAL_CALL
   edge d=3 140a29c90 <- 140a27d30 via 140a27d45 UNCONDITIONAL_CALL
   edge d=3 140a29dc0 <- <nofunc> via 14412d7f8 DATA
   edge d=3 140a29dc0 <- 140a29e70 via 140a29eca UNCONDITIONAL_CALL
   edge d=3 140a27700 <- <nofunc> via 14412d588 DATA
   edge d=3 140a27700 <- 140a27380 via 140a27489 UNCONDITIONAL_CALL
   edge d=3 140a27700 <- 140a27380 via 140a274ee UNCONDITIONAL_CALL
   edge d=3 140a27ce0 <- <nofunc> via 14412d624 DATA
   edge d=3 140a27ce0 <- 140a278f0 via 140a279f6 UNCONDITIONAL_CALL
   edge d=3 140a27ce0 <- 140a278f0 via 140a27a58 UNCONDITIONAL_CALL
   edge d=3 140a276b0 <- <nofunc> via 14412d570 DATA
   edge d=3 140a276b0 <- 140a27380 via 140a2751c UNCONDITIONAL_CALL
   edge d=3 140a276b0 <- 140a27380 via 140a2757c UNCONDITIONAL_CALL
   edge d=3 140a27c90 <- <nofunc> via 14412d60c DATA
   edge d=3 140a27c90 <- 140a278f0 via 140a27a83 UNCONDITIONAL_CALL
   edge d=3 140a27c90 <- 140a278f0 via 140a27ae0 UNCONDITIONAL_CALL
   edge d=4 140a29d90 <- <nofunc> via 14412d7ec DATA
   edge d=4 140a29d90 <- 140a27750 via 140a277b5 UNCONDITIONAL_CALL
   edge d=4 140a29d90 <- 140a27d30 via 140a27d8a UNCONDITIONAL_CALL
   edge d=4 140a27750 <- <nofunc> via 14412d5a0 DATA
   edge d=4 140a27750 <- 140a276b0 via 140a276d4 UNCONDITIONAL_CALL
   edge d=4 140a27750 <- 140a27700 via 140a2772a UNCONDITIONAL_CALL
   edge d=4 140a27750 <- 140a27620 via 140a2766f UNCONDITIONAL_CALL
   edge d=4 140a27d30 <- <nofunc> via 14412d63c DATA
   edge d=4 140a27d30 <- 140a27c90 via 140a27cb4 UNCONDITIONAL_CALL
   edge d=4 140a27d30 <- 140a27ce0 via 140a27d0a UNCONDITIONAL_CALL
   edge d=4 140a27d30 <- 140a27b90 via 140a27bd2 UNCONDITIONAL_CALL
   edge d=4 140a27380 <- <nofunc> via 14412d54c DATA
   edge d=4 140a27380 <- <nofunc> via 14331fb90 DATA
   edge d=4 140a278f0 <- <nofunc> via 14412d5dc DATA
   edge d=4 140a278f0 <- <nofunc> via 14331fc20 DATA
   edge d=5 140a27620 <- <nofunc> via 14412d558 DATA
   edge d=5 140a27620 <- 140a32c40 via 140a32c64 UNCONDITIONAL_CALL
   edge d=5 140a27b90 <- <nofunc> via 14412d5e8 DATA
   edge d=5 140a27b90 <- 140a32c00 via 140a32c24 UNCONDITIONAL_CALL
   edge d=6 140a32c40 <- <nofunc> via 14412e380 DATA
   edge d=6 140a32c40 <- 14168fb64 via 14168fb64 DATA
   edge d=6 140a32c00 <- <nofunc> via 14412e374 DATA
   edge d=6 140a32c00 <- 14168fb58 via 14168fb58 DATA
   edge d=7 14168fb64 <- 140a31df0 via 140a31e27 UNCONDITIONAL_CALL
   edge d=7 14168fb58 <- 140a31d50 via 140a31d87 UNCONDITIONAL_CALL
   edge d=8 140a31df0 <- <nofunc> via 14412e2d8 DATA
   edge d=8 140a31df0 <- 141576fb0 via 141577063 UNCONDITIONAL_CALL
   edge d=8 140a31d50 <- <nofunc> via 14412e2cc DATA
   edge d=8 140a31d50 <- 141576fb0 via 141577096 UNCONDITIONAL_CALL

######## revtree 0x140a1f6e0 8
   edge d=1 140a1f6e0 <- <nofunc> via 14412c940 DATA
   edge d=1 140a1f6e0 <- 140a267d0 via 140a26914 UNCONDITIONAL_CALL
   edge d=1 140a1f6e0 <- 140a29a60 via 140a29ae7 UNCONDITIONAL_CALL
   edge d=2 140a267d0 <- <nofunc> via 14412d498 DATA
   edge d=2 140a267d0 <- 140a29a60 via 140a29ab2 UNCONDITIONAL_CALL
   edge d=2 140a29a60 <- <nofunc> via 14412d7b0 DATA
   edge d=2 140a29a60 <- 140a29a20 via 140a29a45 UNCONDITIONAL_CALL
   edge d=2 140a29a60 <- 141577260 via 141577473 UNCONDITIONAL_CALL
   edge d=3 140a29a20 <- <nofunc> via 14412d7a4 DATA
   edge d=3 140a29a20 <- 140a27130 via 140a27165 UNCONDITIONAL_CALL
   edge d=3 140a29a20 <- 140a271e0 via 140a2720f UNCONDITIONAL_CALL
   edge d=3 141577260 <- <nofunc> via 1442313ac DATA
   edge d=3 141577260 <- 140a31d50 via 140a31db5 UNCONDITIONAL_CALL
   edge d=3 141577260 <- 140a31df0 via 140a31e58 UNCONDITIONAL_CALL
   edge d=4 140a27130 <- <nofunc> via 14412d4f8 DATA
   edge d=4 140a27130 <- 140a27700 via 140a2771b UNCONDITIONAL_CALL
   edge d=4 140a27130 <- 140a27ce0 via 140a27cfb UNCONDITIONAL_CALL
   edge d=4 140a271e0 <- <nofunc> via 14412d504 DATA
   edge d=4 140a271e0 <- 140a276b0 via 140a276c5 UNCONDITIONAL_CALL
   edge d=4 140a271e0 <- 140a27c90 via 140a27ca5 UNCONDITIONAL_CALL
   edge d=4 140a31d50 <- <nofunc> via 14412e2cc DATA
   edge d=4 140a31d50 <- 141576fb0 via 141577096 UNCONDITIONAL_CALL
   edge d=4 140a31df0 <- <nofunc> via 14412e2d8 DATA
   edge d=4 140a31df0 <- 141576fb0 via 141577063 UNCONDITIONAL_CALL
   edge d=5 140a27700 <- <nofunc> via 14412d588 DATA
   edge d=5 140a27700 <- 140a27380 via 140a27489 UNCONDITIONAL_CALL
   edge d=5 140a27700 <- 140a27380 via 140a274ee UNCONDITIONAL_CALL
   edge d=5 140a27ce0 <- <nofunc> via 14412d624 DATA
   edge d=5 140a27ce0 <- 140a278f0 via 140a279f6 UNCONDITIONAL_CALL
   edge d=5 140a27ce0 <- 140a278f0 via 140a27a58 UNCONDITIONAL_CALL
   edge d=5 140a276b0 <- <nofunc> via 14412d570 DATA
   edge d=5 140a276b0 <- 140a27380 via 140a2751c UNCONDITIONAL_CALL
   edge d=5 140a276b0 <- 140a27380 via 140a2757c UNCONDITIONAL_CALL
   edge d=5 140a27c90 <- <nofunc> via 14412d60c DATA
   edge d=5 140a27c90 <- 140a278f0 via 140a27a83 UNCONDITIONAL_CALL
   edge d=5 140a27c90 <- 140a278f0 via 140a27ae0 UNCONDITIONAL_CALL
   edge d=5 141576fb0 <- <nofunc> via 14423137c DATA
   edge d=5 141576fb0 <- 140a2f220 via 140a2fa26 UNCONDITIONAL_CALL
   edge d=6 140a27380 <- <nofunc> via 14412d54c DATA
   edge d=6 140a27380 <- <nofunc> via 14331fb90 DATA
   edge d=6 140a278f0 <- <nofunc> via 14412d5dc DATA
   edge d=6 140a278f0 <- <nofunc> via 14331fc20 DATA
   edge d=6 140a2f220 <- <nofunc> via 14412df30 DATA
   edge d=6 140a2f220 <- <nofunc> via 1433204c0 DATA

######## revtree 0x140a22010 8
   edge d=1 140a22010 <- <nofunc> via 14412cebc DATA
   edge d=1 140a22010 <- 140a29e70 via 140a29ed2 UNCONDITIONAL_CALL
   edge d=2 140a29e70 <- <nofunc> via 14412d804 DATA
   edge d=2 140a29e70 <- 140a29d90 via 140a29dab UNCONDITIONAL_CALL
   edge d=3 140a29d90 <- <nofunc> via 14412d7ec DATA
   edge d=3 140a29d90 <- 140a27750 via 140a277b5 UNCONDITIONAL_CALL
   edge d=3 140a29d90 <- 140a27d30 via 140a27d8a UNCONDITIONAL_CALL
   edge d=4 140a27750 <- <nofunc> via 14412d5a0 DATA
   edge d=4 140a27750 <- 140a276b0 via 140a276d4 UNCONDITIONAL_CALL
   edge d=4 140a27750 <- 140a27700 via 140a2772a UNCONDITIONAL_CALL
   edge d=4 140a27750 <- 140a27620 via 140a2766f UNCONDITIONAL_CALL
   edge d=4 140a27d30 <- <nofunc> via 14412d63c DATA
   edge d=4 140a27d30 <- 140a27c90 via 140a27cb4 UNCONDITIONAL_CALL
   edge d=4 140a27d30 <- 140a27ce0 via 140a27d0a UNCONDITIONAL_CALL
   edge d=4 140a27d30 <- 140a27b90 via 140a27bd2 UNCONDITIONAL_CALL
   edge d=5 140a276b0 <- <nofunc> via 14412d570 DATA
   edge d=5 140a276b0 <- 140a27380 via 140a2751c UNCONDITIONAL_CALL
   edge d=5 140a276b0 <- 140a27380 via 140a2757c UNCONDITIONAL_CALL
   edge d=5 140a27700 <- <nofunc> via 14412d588 DATA
   edge d=5 140a27700 <- 140a27380 via 140a27489 UNCONDITIONAL_CALL
   edge d=5 140a27700 <- 140a27380 via 140a274ee UNCONDITIONAL_CALL
   edge d=5 140a27620 <- <nofunc> via 14412d558 DATA
   edge d=5 140a27620 <- 140a32c40 via 140a32c64 UNCONDITIONAL_CALL
   edge d=5 140a27c90 <- <nofunc> via 14412d60c DATA
   edge d=5 140a27c90 <- 140a278f0 via 140a27a83 UNCONDITIONAL_CALL
   edge d=5 140a27c90 <- 140a278f0 via 140a27ae0 UNCONDITIONAL_CALL
   edge d=5 140a27ce0 <- <nofunc> via 14412d624 DATA
   edge d=5 140a27ce0 <- 140a278f0 via 140a279f6 UNCONDITIONAL_CALL
   edge d=5 140a27ce0 <- 140a278f0 via 140a27a58 UNCONDITIONAL_CALL
   edge d=5 140a27b90 <- <nofunc> via 14412d5e8 DATA
   edge d=5 140a27b90 <- 140a32c00 via 140a32c24 UNCONDITIONAL_CALL
   edge d=6 140a27380 <- <nofunc> via 14412d54c DATA
   edge d=6 140a27380 <- <nofunc> via 14331fb90 DATA
   edge d=6 140a32c40 <- <nofunc> via 14412e380 DATA
   edge d=6 140a32c40 <- 14168fb64 via 14168fb64 DATA
   edge d=6 140a278f0 <- <nofunc> via 14412d5dc DATA
   edge d=6 140a278f0 <- <nofunc> via 14331fc20 DATA
   edge d=6 140a32c00 <- <nofunc> via 14412e374 DATA
   edge d=6 140a32c00 <- 14168fb58 via 14168fb58 DATA
   edge d=7 14168fb64 <- 140a31df0 via 140a31e27 UNCONDITIONAL_CALL
   edge d=7 14168fb58 <- 140a31d50 via 140a31d87 UNCONDITIONAL_CALL
   edge d=8 140a31df0 <- <nofunc> via 14412e2d8 DATA
   edge d=8 140a31df0 <- 141576fb0 via 141577063 UNCONDITIONAL_CALL
   edge d=8 140a31d50 <- <nofunc> via 14412e2cc DATA
   edge d=8 140a31d50 <- 141576fb0 via 141577096 UNCONDITIONAL_CALL

######## revtree 0x14072ca60 8
   edge d=1 14072ca60 <- <nofunc> via 1440efd34 DATA
   edge d=1 14072ca60 <- 14070ea40 via 14070ede6 UNCONDITIONAL_CALL
   edge d=2 14070ea40 <- <nofunc> via 1440ee210 DATA
   edge d=2 14070ea40 <- 14070e960 via 14070e9da UNCONDITIONAL_CALL
   edge d=2 14070ea40 <- 14070e760 via 14070e92a UNCONDITIONAL_CALL
   edge d=3 14070e960 <- <nofunc> via 1440ee1ec DATA
   edge d=3 14070e960 <- 1416982a9 via 1416982a9 DATA
   edge d=3 14070e760 <- <nofunc> via 1440ee1e0 DATA
   edge d=3 14070e760 <- 14070ae40 via 14070ae6e UNCONDITIONAL_CALL
   edge d=4 1416982a9 <- 14070e760 via 14070e877 UNCONDITIONAL_CALL
   edge d=4 14070ae40 <- <nofunc> via 1440ee000 DATA
   edge d=4 14070ae40 <- 141698296 via 141698296 DATA
   edge d=5 141698296 <- 14070ae90 via 14070aeaa UNCONDITIONAL_CALL
   edge d=6 14070ae90 <- <nofunc> via 1440ee00c DATA
   edge d=6 14070ae90 <- <nofunc> via 143f7cd08 DATA

######## revtree 0x1402d57c0 4
   edge d=1 1402d57c0 <- <nofunc> via 144093628 DATA
   edge d=1 1402d57c0 <- 1402d3f90 via 1402d3fd4 UNCONDITIONAL_CALL
   edge d=1 1402d57c0 <- 1402d4370 via 1402d437c UNCONDITIONAL_CALL
   edge d=2 1402d3f90 <- <nofunc> via 1440935a4 DATA
   edge d=2 1402d3f90 <- <nofunc> via 14325a060 DATA
   edge d=2 1402d4370 <- <nofunc> via 1440935b0 DATA
   edge d=2 1402d4370 <- 1402d3f90 via 1402d3fe6 UNCONDITIONAL_CALL
   edge d=2 1402d4370 <- 1403b0bd0 via 1403b1430 UNCONDITIONAL_CALL
   edge d=3 1403b0bd0 <- <nofunc> via 1440a08f4 DATA
   edge d=3 1403b0bd0 <- 141435850 via 1414358ac UNCONDITIONAL_CALL
   edge d=3 1403b0bd0 <- 141277ac0 via 141277b06 UNCONDITIONAL_CALL
   edge d=3 1403b0bd0 <- 141277a20 via 141277a26 UNCONDITIONAL_CALL
   edge d=3 1403b0bd0 <- <nofunc> via 1417620e0 UNCONDITIONAL_CALL
   edge d=4 141435850 <- <nofunc> via 144221d64 DATA
   edge d=4 141435850 <- 1402d6b30 via 1402d6b72 UNCONDITIONAL_CALL
   edge d=4 141277ac0 <- <nofunc> via 1441eec2c DATA
   edge d=4 141277ac0 <- 141277a90 via 141277a90 UNCONDITIONAL_JUMP
   edge d=4 141277ac0 <- 141277aa0 via 141277aa2 UNCONDITIONAL_CALL
   edge d=4 141277a20 <- <nofunc> via 1441eebfc DATA
   edge d=4 141277a20 <- <nofunc> via 143f43cb8 DATA

######## revtree 0x140534330 6
   edge d=1 140534330 <- <nofunc> via 1440c74b0 DATA
   edge d=1 140534330 <- 1405243e0 via 140524469 UNCONDITIONAL_CALL
   edge d=2 1405243e0 <- <nofunc> via 1440c6304 DATA
   edge d=2 1405243e0 <- 1405261a0 via 1405261f2 UNCONDITIONAL_CALL
   edge d=3 1405261a0 <- <nofunc> via 1440c6448 DATA
   edge d=3 1405261a0 <- 1405248b0 via 140524ac5 UNCONDITIONAL_CALL
   edge d=4 1405248b0 <- <nofunc> via 1440c6388 DATA
   edge d=4 1405248b0 <- 14052d520 via 14052d5fa UNCONDITIONAL_CALL
   edge d=5 14052d520 <- <nofunc> via 1440c6910 DATA
   edge d=5 14052d520 <- 14052d360 via 14052d39a UNCONDITIONAL_CALL
   edge d=6 14052d360 <- <nofunc> via 1440c68f8 DATA
   edge d=6 14052d360 <- 14052d2c0 via 14052d2d8 UNCONDITIONAL_CALL
```

MachineName / GetUserName trees (out11):
```
######## revtree 0x140f6ab10 5
   edge d=1 140f6ab10 <- <nofunc> via 1441a08f0 DATA
   edge d=1 140f6ab10 <- 140f648f0 via 140f64910 UNCONDITIONAL_CALL
   edge d=2 140f648f0 <- <nofunc> via 14419fdd4 DATA
   edge d=2 140f648f0 <- 140afd960 via 140afdca1 UNCONDITIONAL_CALL
   edge d=2 140f648f0 <- 140f648d0 via 140f648d9 UNCONDITIONAL_CALL
   edge d=3 140afd960 <- <nofunc> via 14414290c DATA
   edge d=3 140afd960 <- <nofunc> via 14332e100 DATA
   edge d=3 140afd960 <- <nofunc> via 143331b88 DATA
   edge d=3 140afd960 <- <nofunc> via 14338fac0 DATA
   edge d=3 140afd960 <- <nofunc> via 1433edf18 DATA
   edge d=3 140f648d0 <- <nofunc> via 14419fdc8 DATA

######## revtree 0x14066bf20 5
   edge d=1 14066bf20 <- <nofunc> via 1440de904 DATA
   edge d=1 14066bf20 <- 14066bf90 via 14066bfc2 UNCONDITIONAL_CALL
   edge d=2 14066bf90 <- <nofunc> via 1440de910 DATA
   edge d=2 14066bf90 <- 1406735e0 via 14067365e UNCONDITIONAL_CALL
   edge d=2 14066bf90 <- 140674740 via 140674744 UNCONDITIONAL_CALL
   edge d=3 1406735e0 <- <nofunc> via 1440df174 DATA
   edge d=3 1406735e0 <- 140673340 via 140673369 UNCONDITIONAL_CALL
   edge d=3 140674740 <- <nofunc> via 1440df2e8 DATA
   edge d=3 140674740 <- 14168ef34 via 14168ef34 DATA
   edge d=4 140673340 <- <nofunc> via 1440df138 DATA
   edge d=4 140673340 <- 140673500 via 14067351c UNCONDITIONAL_CALL
   edge d=4 140673340 <- 140673390 via 1406733bf UNCONDITIONAL_CALL
   edge d=4 140673340 <- 140673590 via 1406735a9 UNCONDITIONAL_CALL
   edge d=4 140673340 <- 140674530 via 14067453c UNCONDITIONAL_CALL
   edge d=4 14168ef34 <- 140673ed0 via 140673f0f UNCONDITIONAL_CALL
   edge d=5 140673500 <- <nofunc> via 1440df15c DATA
   edge d=5 140673500 <- 1406705e0 via 140670691 UNCONDITIONAL_CALL
   edge d=5 140673500 <- 140670db0 via 140670def UNCONDITIONAL_CALL
   edge d=5 140673390 <- <nofunc> via 1440df144 DATA
   edge d=5 140673390 <- 1406737d0 via 140673863 UNCONDITIONAL_CALL
   edge d=5 140673590 <- <nofunc> via 1440df168 DATA
   edge d=5 140673590 <- 140673390 via 140673471 UNCONDITIONAL_CALL
   edge d=5 140674530 <- <nofunc> via 1440df2b8 DATA
   edge d=5 140674530 <- 140674140 via 1406741af UNCONDITIONAL_CALL
   edge d=5 140674530 <- 140674632 via 1406746b4 UNCONDITIONAL_CALL
   edge d=5 140673ed0 <- <nofunc> via 1440df240 DATA
   edge d=5 140673ed0 <- 140673fd0 via 140674052 UNCONDITIONAL_CALL

######## revtree 0x14087b010 5
   edge d=1 14087b010 <- <nofunc> via 14410b9a0 DATA
   edge d=1 14087b010 <- 14087b080 via 14087b0b2 UNCONDITIONAL_CALL
   edge d=2 14087b080 <- <nofunc> via 14410b9ac DATA
   edge d=2 14087b080 <- 14076c6e0 via 14076c6e5 UNCONDITIONAL_CALL
   edge d=3 14076c6e0 <- <nofunc> via 1440f4f6c DATA
   edge d=3 14076c6e0 <- 140124360 via 1401243c1 UNCONDITIONAL_CALL
   edge d=3 14076c6e0 <- 140124500 via 1401245e1 UNCONDITIONAL_CALL
   edge d=3 14076c6e0 <- 140124700 via 140124700 UNCONDITIONAL_JUMP
   edge d=3 14076c6e0 <- 140d2f0b0 via 140d2f0b0 UNCONDITIONAL_JUMP
   edge d=4 140124360 <- <nofunc> via 1440835cc DATA
   edge d=4 140124360 <- 1403aef60 via 1403af0ca UNCONDITIONAL_CALL
   edge d=4 140124360 <- 140123520 via 14012379c UNCONDITIONAL_CALL
   edge d=4 140124500 <- <nofunc> via 1440835d8 DATA
   edge d=4 140124500 <- 1402a0ee0 via 1402a10e4 UNCONDITIONAL_CALL
   edge d=4 140124500 <- 1402a17d0 via 1402a1a13 UNCONDITIONAL_CALL
   edge d=4 140124700 <- <nofunc> via 1440835fc DATA
   edge d=4 140d2f0b0 <- <nofunc> via 1441731a0 DATA
   edge d=5 1403aef60 <- <nofunc> via 1440a0810 DATA
   edge d=5 1403aef60 <- 141434e40 via 141434e9c UNCONDITIONAL_CALL
   edge d=5 1403aef60 <- 1412774c0 via 141277506 UNCONDITIONAL_CALL
   edge d=5 1403aef60 <- 141277420 via 141277426 UNCONDITIONAL_CALL
   edge d=5 1403aef60 <- <nofunc> via 141762068 UNCONDITIONAL_CALL
   edge d=5 140123520 <- <nofunc> via 144083518 DATA
   edge d=5 140123520 <- 140122fb0 via 140123194 UNCONDITIONAL_CALL
   edge d=5 1402a0ee0 <- <nofunc> via 1440901fc DATA
   edge d=5 1402a0ee0 <- 1402d47e0 via 1402d4f8e UNCONDITIONAL_CALL
   edge d=5 1402a17d0 <- <nofunc> via 144090244 DATA
   edge d=5 1402a17d0 <- 1402d47e0 via 1402d4b97 UNCONDITIONAL_CALL

######## revtree 0x140073110 6
   edge d=1 140073110 <- <nofunc> via 14407c528 DATA
   edge d=1 140073110 <- 140073290 via 1400732c9 UNCONDITIONAL_CALL
   edge d=2 140073290 <- <nofunc> via 14407c54c DATA
```

## Appendix B — decompiled functions

### LoginScene.GetDevice @ 0x1402d57c0
```c
// ==== FUN_1402d57c0 @ 1402d57c0 size=252  [fn] ====

longlong FUN_1402d57c0(void)

{
  int iVar1;
  undefined8 uVar2;
  undefined8 uVar3;
  longlong lVar4;
  undefined8 uVar5;
  undefined8 local_28;
  undefined8 local_20;
  
  local_28 = 0;
  local_20 = 0;
  uVar2 = FUN_14076bfc0(0x1a,0);
  uVar2 = FUN_14082c520(uVar2,&PTR_DAT_143e05ed0);
  iVar1 = FUN_140826030(uVar2);
  if (iVar1 == 0) {
    FUN_140825ef0(uVar2);
  }
  uVar2 = FUN_14082c520(uVar2,&PTR_DAT_143de38d0);
  iVar1 = FUN_140826e20(uVar2);
  if (iVar1 != 0) {
    if (PTR_FUN_143f7d270 != (undefined *)0x0) {
      FUN_141682e2a();
    }
    uVar3 = FUN_140827050(uVar2,*(undefined8 *)(DAT_143f8d7e0 + 8));
    lVar4 = FUN_1402d5760(uVar3);
    if (*(int *)(lVar4 + 8) == 0x24) {
      return lVar4;
    }
    FUN_140826df0(uVar2);
  }
  FUN_14079fe10(&local_28);
  lVar4 = FUN_14079fcd0(&local_28,&PTR_DAT_143ed7cf0,0);
  uVar3 = FUN_1402d5700(lVar4);
  uVar5 = FUN_140826cb0();
  FUN_140827100(uVar2,uVar3,uVar5);
  return lVar4;
}
```

### LoginScene.Encrypt @ 0x1402d5700
```c
// ==== FUN_1402d5700 @ 1402d5700 size=88  [fn] ====

void FUN_1402d5700(undefined8 param_1)

{
  undefined8 uVar1;
  
  if (PTR_FUN_143f7d260 != (undefined *)0x0) {
    FUN_141682dfe();
  }
  uVar1 = FUN_1408143b0(*(undefined8 *)(DAT_143f8d7d0 + 0x10),param_1);
  uVar1 = FUN_140a33900(uVar1,*(undefined8 *)(DAT_143f8c180 + 0x18),1);
  FUN_140788f20(uVar1);
  return;
}
```

### LoginScene.Decrypt @ 0x1402d5760
```c
// ==== FUN_1402d5760 @ 1402d5760 size=93  [fn] ====

void FUN_1402d5760(undefined8 param_1)

{
  undefined8 uVar1;
  undefined8 uVar2;
  
  if (PTR_FUN_143f7d260 != (undefined *)0x0) {
    FUN_141682dfe();
  }
  uVar1 = *(undefined8 *)(DAT_143f8d7d0 + 0x10);
  uVar2 = FUN_1407896d0(param_1);
  uVar2 = FUN_140a339d0(uVar2,*(undefined8 *)(DAT_143f8c180 + 0x18),1);
  FUN_140814910(uVar1,uVar2);
  return;
}
```

### LoginScene.CreateHttpClient @ 0x1402d4370
```c
// ==== FUN_1402d4370 @ 1402d4370 size=690  [fn] ====

undefined8 FUN_1402d4370(undefined8 param_1,char param_2)

{
  undefined4 uVar1;
  int iVar2;
  undefined8 uVar3;
  undefined8 uVar4;
  longlong lVar5;
  undefined8 uVar6;
  longlong lVar7;
  undefined8 uVar8;
  undefined8 uVar9;
  undefined **ppuVar10;
  
  uVar3 = FUN_1402d57c0();
  if (PTR_FUN_143f7b1b8 != (undefined *)0x0) {
    FUN_141680b1a();
  }
  uVar4 = *(undefined8 *)(DAT_143f8bec0 + 0x10);
  uVar1 = FUN_1407bce00(uVar4);
  uVar4 = FUN_1407bcce0(uVar4,uVar1);
  lVar5 = FUN_1416a7aa0(&DAT_1432c8b18);
  uVar6 = FUN_1416a7aa0(&DAT_1432c87b8);
  FUN_140708950(uVar6);
  FUN_1416a80d0(lVar5 + 8,uVar6);
  FUN_1406d6820(lVar5);
  if (param_2 == '\0') {
    lVar7 = *(longlong *)(DAT_143f8c6d8 + 0x18);
    if (lVar7 == 0) {
      lVar7 = FUN_1416a7aa0(&DAT_143549538);
      FUN_141689354(lVar7,&PTR_DAT_143f61c08);
      FUN_1416a80d0(DAT_143f8c6d8 + 0x18,lVar7);
    }
  }
  else {
    lVar7 = 0;
  }
  FUN_140709ec0(lVar5);
  FUN_1416a80d0(*(longlong *)(lVar5 + 8) + 0x60,lVar7);
  uVar6 = FUN_1416a7aa0(&DAT_1432c2f70);
  FUN_1406d2ce0(uVar6,lVar5,1);
  lVar5 = FUN_1416a7aa0(&DAT_1432ca348);
  FUN_1406d2bd0(uVar6);
  uVar8 = FUN_14071ff30();
  FUN_140721270(&PTR_DAT_143ecdae0,&PTR_DAT_143ef0710);
  *(undefined ***)(lVar5 + 0x10) = &PTR_DAT_143ecdae0;
  FUN_14136b1a0(uVar8,lVar5);
  uVar8 = FUN_1406d2bd0(uVar6);
  uVar9 = FUN_14071eca0(uVar8,&PTR_DAT_143ec32f0);
  FUN_14071d370(uVar8,uVar9,uVar3);
  uVar8 = FUN_1406d2bd0(uVar6);
  uVar9 = FUN_14071eca0(uVar8,&PTR_DAT_143ec3230);
  FUN_14071d370(uVar8,uVar9,uVar4);
  uVar4 = FUN_1406d2bd0(uVar6);
  if (PTR_FUN_143f7b1b8 != (undefined *)0x0) {
    FUN_14167ee08();
  }
  uVar8 = FUN_1407a68e0(DAT_143f7b1d0);
  uVar9 = FUN_14071eca0(uVar4,&PTR_DAT_143ec3358);
  FUN_14071d370(uVar4,uVar9,uVar8);
  uVar4 = FUN_1406d2bd0(uVar6);
  uVar3 = FUN_1402d54f0(uVar3);
  uVar8 = FUN_14071eca0(uVar4,&PTR_DAT_143ec33f8);
  FUN_14071d370(uVar4,uVar8,uVar3);
  if (PTR_FUN_143f7b8e8 != (undefined *)0x0) {
    FUN_1416813de();
  }
  ppuVar10 = *(undefined ***)(*(longlong *)(DAT_143f8c358 + 8) + 0x40);
  iVar2 = FUN_140778bb0(ppuVar10,&PTR_DAT_143ed50b0);
  if (iVar2 != 0) {
    ppuVar10 = &PTR_DAT_143ee6eb8;
  }
  uVar3 = FUN_1416a7aa0(&DAT_1432f6fa0);
  FUN_1408c9c90(uVar3,ppuVar10);
  FUN_1406d2c10(uVar6,uVar3);
  return uVar6;
}
```

### LoginScene.GetRequestTraceContext @ 0x1402d54f0
```c
// ==== FUN_1402d54f0 @ 1402d54f0 size=444  [fn] ====

undefined8 FUN_1402d54f0(longlong param_1,undefined8 param_2,undefined8 param_3,undefined8 param_4)

{
  uint uVar1;
  undefined8 uVar2;
  longlong lVar3;
  longlong lVar4;
  undefined8 local_40;
  undefined8 local_38;
  undefined8 uStack_30;
  longlong local_28;
  undefined8 uStack_20;
  
  local_40 = 0;
  local_38 = 0;
  uStack_30 = 0;
  local_28 = 0;
  uStack_20 = 0;
  FUN_1408463e0(&local_40,0,3);
  if (PTR_FUN_143f7b1b8 != (undefined *)0x0) {
    FUN_141680b1a();
  }
  lVar4 = DAT_143f8bec0;
  FUN_1414f0800(&local_40,*(undefined4 *)(*(longlong *)(DAT_143f8bec0 + 0x10) + 8));
  FUN_1414f0800(&local_40,*(undefined4 *)(*(longlong *)(lVar4 + 0x10) + 0x14));
  if ((uStack_30._4_1_ == '\0') && (param_1 != 0)) {
    if ((uint)uStack_20 < (uint)uStack_30) {
                    /* WARNING: Subroutine does not return */
      FUN_1407b15d0();
    }
    uVar1 = *(uint *)(param_1 + 8);
    if (uVar1 <= (uint)uStack_20 - (uint)uStack_30) {
      FUN_1407afac0(local_28 + (uStack_30 & 0xffffffff) * 2,param_1 + 0xc,(ulonglong)uVar1 * 2);
      uStack_30 = CONCAT44(uStack_30._4_4_,uVar1 + (uint)uStack_30);
      goto LAB_1402d55ad;
    }
  }
  FUN_140846770(&local_40,param_1);
LAB_1402d55ad:
  uVar2 = FUN_140846580(&local_40);
  lVar3 = FUN_1416a7aa0(&DAT_14331f1e8);
  FUN_140a23450(lVar3,&PTR_DAT_143f62c88);
  lVar4 = lVar3;
  if (PTR_FUN_143f7d270 != (undefined *)0x0) {
    FUN_141682e2a();
  }
  uVar2 = FUN_1408913d0(*(undefined8 *)(DAT_143f8d7e0 + 8),uVar2);
  uVar2 = FUN_140a22c90(lVar3,uVar2);
  uVar2 = FUN_140789c90(uVar2,0,8,param_4,lVar4);
  if (PTR_FUN_143f7d0c0 != (undefined *)0x0) {
    FUN_141682c46();
  }
  uVar2 = FUN_1407e5ff0(*(undefined8 *)(DAT_143f8d6e8 + 8),uVar2);
  lVar4 = *(longlong *)(lVar3 + 0x38);
  if (lVar4 != 0) {
    *(undefined8 *)(lVar3 + 0x38) = 0;
    FUN_140a233f0(lVar4,1);
  }
  if (*(longlong *)(lVar3 + 0x20) != 0) {
    FUN_14077ba20();
  }
  *(undefined8 *)(lVar3 + 0x20) = 0;
  *(undefined1 *)(lVar3 + 0x18) = 1;
  FUN_14076d450(lVar3);
  return uVar2;
}
```

### LoginScene.Load @ 0x1402d3f90
```c
// ==== FUN_1402d3f90 @ 1402d3f90 size=984  [fn] ====

/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

void FUN_1402d3f90(longlong param_1)

{
  longlong lVar1;
  longlong lVar2;
  longlong lVar3;
  undefined8 uVar4;
  undefined8 uVar5;
  float fVar6;
  float fVar7;
  undefined4 local_60;
  int iStack_5c;
  int iStack_58;
  undefined4 uStack_54;
  undefined4 local_50;
  undefined8 local_48;
  undefined8 uStack_40;
  undefined8 local_38;
  undefined8 uStack_30;
  
  local_48 = 0;
  uStack_40 = 0;
  local_38 = 0;
  uStack_30 = 0;
  *(undefined1 *)(param_1 + 0x15) = 1;
  if (PTR_FUN_143f7b8e8 != (undefined *)0x0) {
    FUN_1416813de();
  }
  if (*(char *)(*(longlong *)(DAT_143f8c358 + 8) + 0x7e) == '\0') {
    uVar4 = FUN_1402d4370(param_1,0);
    FUN_1416a80d0(param_1 + 0x20,uVar4);
  }
  else {
    FUN_1402d57c0();
    *(undefined8 *)(param_1 + 0x20) = 0;
  }
  lVar2 = DAT_143f8beb8;
  lVar1 = *(longlong *)(*(longlong *)(DAT_143f8beb8 + 0x10) + 0x28);
  if (PTR_FUN_143f7bba0 != (undefined *)0x0) {
    FUN_14168148e();
  }
  (**(code **)(*(longlong *)(DAT_143f8c838 + 0x68) + 0x20))
            (*(undefined8 *)(*(longlong *)(DAT_143f8c838 + 0x68) + 8),*(undefined8 *)(lVar1 + 0x30),
             0);
  FUN_140120080();
  fVar6 = *(float *)(*(longlong *)(lVar2 + 0x10) + 0x14c);
  local_60 = 0;
  fVar7 = fVar6 * _DAT_14389ffd4;
  iStack_5c = 0x7fffffff;
  if (fVar7 < _DAT_14389ffd8) {
    iStack_5c = (int)(float)(-(uint)!NAN(fVar7) & (uint)fVar7);
  }
  fVar6 = fVar6 * _DAT_14389ffdc;
  iStack_58 = 0x7fffffff;
  if (fVar6 < _DAT_14389ffd8) {
    iStack_58 = (int)(float)(-(uint)!NAN(fVar6) & (uint)fVar6);
  }
  uStack_54 = 0x3f800000;
  local_50 = 0;
  FUN_1401203e0(*(longlong *)(lVar2 + 0x10),&local_60);
  FUN_1401201b0(*(undefined8 *)(lVar2 + 0x10),&local_38);
  FUN_140120050(*(undefined8 *)(lVar2 + 0x10),&uStack_40,&local_48);
  FUN_14011fff0(*(undefined8 *)(lVar2 + 0x10),
                ((int)uStack_30 / 2 + (int)local_38) - (int)uStack_40 / 2,
                (uStack_30._4_4_ / 2 + local_38._4_4_) - (int)local_48 / 2);
  uVar4 = FUN_1416a7aa0(&DAT_1436c03c8);
  if (PTR_FUN_143f7b300 != (undefined *)0x0) {
    FUN_141680ca6();
  }
  lVar1 = DAT_143f8bf90;
  uVar5 = *(undefined8 *)(DAT_143f8bf90 + 8);
  FUN_1416874a0(uVar4,param_1);
  ClassicUO_Network_NetClient_add_Disconnected(uVar5,uVar4);
  uVar5 = FUN_1416a7aa0(&DAT_1432d3648);
  uVar4 = *(undefined8 *)(lVar1 + 8);
  FUN_141687470(uVar5,param_1);
  ClassicUO_Network_NetClient_add_ProtocolSet(uVar4,uVar5);
  if (PTR_FUN_143f7b310 != (undefined *)0x0) {
    FUN_14167ee98();
  }
  uVar4 = FUN_1416a7aa0(&DAT_1432633f0);
  if (PTR_FUN_143f7b310 != (undefined *)0x0) {
    FUN_141680cbc();
  }
  lVar3 = DAT_143f8bf98;
  lVar1 = *(longlong *)(DAT_143f8bf98 + 0x18);
  FUN_141687494(uVar4,param_1);
  lVar1 = *(longlong *)(lVar1 + 8);
  if (0x85 < *(uint *)(lVar1 + 8)) {
    FUN_1416a80d0(lVar1 + 0x438,uVar4);
    uVar4 = FUN_1416a7aa0(&DAT_1432633f0);
    lVar1 = *(longlong *)(lVar3 + 0x18);
    FUN_141687488(uVar4,param_1);
    lVar1 = *(longlong *)(lVar1 + 8);
    if (0x86 < *(uint *)(lVar1 + 8)) {
      FUN_1416a80d0(lVar1 + 0x440,uVar4);
      uVar4 = FUN_1416a7aa0(&DAT_1432633f0);
      lVar1 = *(longlong *)(lVar3 + 0x18);
      FUN_14168747c(uVar4,param_1);
      lVar1 = *(longlong *)(lVar1 + 8);
      if (0xa9 < *(uint *)(lVar1 + 8)) {
        FUN_1416a80d0(lVar1 + 0x558,uVar4);
        uVar4 = FUN_1416a7aa0(&DAT_1432633f0);
        lVar1 = *(longlong *)(lVar3 + 0x18);
        FUN_1416874ac(uVar4,param_1);
        lVar1 = *(longlong *)(lVar1 + 8);
        if (0x53 < *(uint *)(lVar1 + 8)) {
          FUN_1416a80d0(lVar1 + 0x2a8,uVar4);
          lVar1 = *(longlong *)(*(longlong *)(lVar2 + 0x10) + 0x110);
          FUN_1402da4b0(lVar1,*(undefined4 *)(lVar1 + 0x18),0,1);
          *(undefined4 *)(param_1 + 0x90) = 0;
          FUN_1402d52e0(param_1,*(undefined4 *)(param_1 + 0x90),0);
          FUN_1402d6b30(param_1);
          return;
        }
      }
    }
  }
                    /* WARNING: Subroutine does not return */
  FUN_140879890();
}
```

### LoginScene VerifyDeviceCode async MoveNext (account/verifydevice) @ 0x1403b2ed0
```c
// ==== FUN_1403b2ed0 @ 1403b2ed0 size=673  [refs literal account/verifydevice] ====

void FUN_1403b2ed0(longlong *param_1)

{
  longlong lVar1;
  int iVar2;
  longlong lVar3;
  longlong lVar4;
  longlong lVar5;
  undefined8 uVar6;
  undefined8 uVar7;
  longlong local_38;
  
  local_38 = 0;
  lVar3 = param_1[2];
  lVar1 = *param_1;
  if ((int)lVar3 != 0) {
    FUN_1402d52e0(lVar1,2,3);
    iVar2 = FUN_140772d40(*(undefined8 *)(lVar1 + 0x80));
    if (iVar2 != 0) {
      *(undefined ***)(lVar1 + 0x48) = &PTR_DAT_143df7510;
      FUN_1402d52e0(lVar1,3,0x1c);
      goto LAB_1403b3151;
    }
  }
  if ((int)lVar3 == 0) {
    local_38 = param_1[5];
    param_1[5] = 0;
    *(undefined4 *)(param_1 + 2) = 0xffffffff;
  }
  else {
    lVar3 = FUN_1416a7aa0(&DAT_143290a00);
    uVar7 = *(undefined8 *)(lVar1 + 0x20);
    *(undefined ***)(lVar3 + 8) = &PTR_DAT_143d8d2b0;
    *(undefined ***)(lVar3 + 0x10) = &PTR_DAT_143d8d2b0;
    FUN_1416a80d0(lVar3 + 8,*(undefined8 *)(lVar1 + 0x80));
    FUN_1416a80d0(lVar3 + 0x10,param_1[1]);
    lVar4 = FUN_1416a7aa0(&DAT_143324168);
    FUN_140a63590(lVar4,1);
    lVar5 = FUN_1416a7aa0(&DAT_143290a50);
    if (PTR_FUN_143f7c000 != (undefined *)0x0) {
      FUN_1416818f0();
    }
    FUN_1416a80d0(lVar5 + 0x20,*(undefined8 *)(DAT_143f8caa0 + 8));
    FUN_140a63980(lVar4);
    FUN_140a63620(lVar4,lVar5);
    if (*(longlong *)(lVar4 + 0x20) == 0) {
                    /* WARNING: Subroutine does not return */
      FUN_140a4aab0();
    }
    *(undefined1 *)(lVar4 + 0x9e) = 1;
    FUN_1416a80d0(lVar5 + 8,lVar4);
    uVar6 = FUN_1404e6b70(lVar5);
    local_38 = FUN_14156da40(&DAT_143181f58,uVar7,&PTR_DAT_143ecbcf0,lVar3,uVar6,0);
    if ((*(uint *)(local_38 + 0x34) & 0x1600000) == 0) {
      *(undefined4 *)(param_1 + 2) = 0;
      FUN_1416a8140(param_1 + 5,local_38);
      uVar7 = FUN_141436560(param_1,param_1 + 4);
      FUN_141563a00(&PTR_DAT_1431bd660,&local_38,uVar7);
      return;
    }
  }
  if ((*(uint *)(local_38 + 0x34) & 0x11000000) != 0x1000000) {
    FUN_140846ec0(local_38,0);
  }
  lVar3 = *(longlong *)(local_38 + 0x38);
  iVar2 = FUN_1406d7ed0();
  if (iVar2 == 0) {
    iVar2 = *(int *)(lVar3 + 0x38);
    if (iVar2 == 0x191) {
      *(undefined ***)(lVar1 + 0x48) = &PTR_DAT_143e9bcd8;
    }
    else if (iVar2 == 0x1a2) {
      uVar7 = FUN_14012ef70();
      FUN_1416a80d0(lVar1 + 0x48,uVar7);
    }
    else {
      *(undefined ***)(lVar1 + 0x48) = &PTR_DAT_143eb0bc8;
    }
    FUN_1402d52e0(lVar1,3,0x1c);
  }
  else {
    FUN_1402d5b70(lVar1);
  }
LAB_1403b3151:
  *(undefined4 *)(param_1 + 2) = 0xfffffffe;
  FUN_140845c80(param_1 + 3);
  return;
}
```

### LoginScene game-login async MoveNext (login/gamelogin -> 0x91) @ 0x1403b1ce0
```c
// ==== FUN_1403b1ce0 @ 1403b1ce0 size=1706  [fn] ====

void FUN_1403b1ce0(longlong *param_1)

{
  uint uVar1;
  longlong lVar2;
  int iVar3;
  undefined8 uVar4;
  undefined8 uVar5;
  undefined8 uVar6;
  longlong lVar7;
  longlong lVar8;
  longlong lVar9;
  ulonglong local_50;
  longlong lStack_48;
  longlong local_40;
  
  local_50 = 0;
  lStack_48 = 0;
  local_40 = 0;
  uVar1 = *(uint *)(param_1 + 1);
  lVar2 = *param_1;
  if (1 < uVar1) {
    if (*(int *)(lVar2 + 0x90) != 6) goto LAB_1403b236f;
    if (*(longlong *)(lVar2 + 0x20) == 0) {
      FUN_1402d52e0(lVar2,6,8);
      if (PTR_FUN_143f7b300 != (undefined *)0x0) {
        FUN_141680ca6();
      }
      lVar7 = DAT_143f8bf90;
      uVar1 = *(uint *)(lVar2 + 0x9c);
      if (*(uint *)(*(longlong *)(lVar2 + 0x30) + 0x10) <= uVar1) {
                    /* WARNING: Subroutine does not return */
        FUN_1407b16d0(*(undefined8 *)(DAT_143f8bf90 + 8));
      }
      lVar2 = *(longlong *)(*(longlong *)(lVar2 + 0x30) + 8);
      if (*(uint *)(lVar2 + 8) <= uVar1) {
                    /* WARNING: Subroutine does not return */
        FUN_140879890();
      }
      ClassicUO_Network_NetClientExt_Send_GameLogin
                (*(undefined8 *)(DAT_143f8bf90 + 8),
                 *(undefined8 *)(*(longlong *)(lVar2 + 0x10 + (ulonglong)uVar1 * 8) + 8),
                 &PTR_DAT_143d8d2b0);
      *(undefined1 *)(*(longlong *)(lVar7 + 8) + 0x70) = 1;
      goto LAB_1403b236f;
    }
  }
  if (uVar1 == 0) {
    local_40 = param_1[4];
    param_1[4] = 0;
    *(undefined4 *)(param_1 + 1) = 0xffffffff;
LAB_1403b1d4b:
    if ((*(uint *)(local_40 + 0x34) & 0x11000000) != 0x1000000) {
      FUN_140846ec0(local_40,0);
    }
    lVar7 = *(longlong *)(local_40 + 0x38);
    if (*(int *)(lVar2 + 0x90) != 7) goto LAB_1403b236f;
    iVar3 = FUN_1406d7ed0();
    if (iVar3 == 0) {
      iVar3 = *(int *)(lVar7 + 0x38);
      if (iVar3 == 400) {
        *(undefined ***)(lVar2 + 0x48) = &PTR_DAT_143eaff08;
      }
      else if (iVar3 == 0x191) {
        *(undefined ***)(lVar2 + 0x48) = &PTR_DAT_143eb0018;
      }
      else if (iVar3 == 0x1a2) {
        uVar4 = FUN_14012ef70();
        FUN_1416a80d0(lVar2 + 0x48,uVar4);
      }
      else {
        *(undefined ***)(lVar2 + 0x48) = &PTR_DAT_143db6ea0;
      }
      FUN_1402d52e0(lVar2,7,0x1c);
      goto LAB_1403b236f;
    }
    lVar7 = FUN_1406d7cb0(lVar7);
    lVar8 = FUN_1416a7aa0(&DAT_143324168);
    FUN_140a63590(lVar8,1);
    lVar9 = FUN_1416a7aa0(&DAT_14328fcf0);
    if (PTR_FUN_143f7bf90 != (undefined *)0x0) {
      FUN_1416817bc();
    }
    FUN_1416a80d0(lVar9 + 0x20,*(undefined8 *)(DAT_143f8ca30 + 8));
    FUN_140a63980(lVar8);
    FUN_140a63620(lVar8,lVar9);
    if (*(longlong *)(lVar8 + 0x20) == 0) {
LAB_1403b22c7:
                    /* WARNING: Subroutine does not return */
      FUN_140a4aab0();
    }
    *(undefined1 *)(lVar8 + 0x9e) = 1;
    FUN_1416a80d0(lVar9 + 8,lVar8);
    uVar4 = FUN_1404d7540(lVar9);
    if (lVar7 == 0) {
                    /* WARNING: Subroutine does not return */
      FUN_1407853c0(&PTR_DAT_143ed66e8);
    }
    lStack_48 = FUN_14156daa0(&PTR_DAT_143180598,lVar7,uVar4,0);
    if ((*(uint *)(lStack_48 + 0x34) & 0x1600000) == 0) {
      *(undefined4 *)(param_1 + 1) = 1;
      FUN_1416a8140(param_1 + 5,lStack_48);
      uVar4 = FUN_141435d40(param_1,param_1 + 3);
      FUN_141563a00(&PTR_DAT_1431b9058,&lStack_48,uVar4);
      return;
    }
  }
  else {
    if (uVar1 != 1) {
      FUN_1402d52e0(lVar2,6,7);
      uVar4 = FUN_1416a7aa0(&DAT_1432c37c8);
      if (PTR_FUN_143f7cc50 != (undefined *)0x0) {
        FUN_14168257c();
      }
      uVar6 = *(undefined8 *)(DAT_143f8d308 + 0x18);
      uVar5 = FUN_1416a7aa0(&DAT_1432f6fa0);
      local_50 = local_50 & 0xffffffffffffff00;
      FUN_1408d1f00(uVar5,&PTR_DAT_143eeeaa0,0,0,&local_50);
      FUN_1406d78f0(uVar4,uVar6,uVar5);
      lVar8 = FUN_1416a7aa0(&DAT_143290d40);
      *(undefined ***)(lVar8 + 8) = &PTR_DAT_143d8d2b0;
      lVar7 = *(longlong *)(lVar2 + 0x30);
      uVar1 = *(uint *)(lVar2 + 0x9c);
      if (*(uint *)(lVar7 + 0x10) <= uVar1) goto LAB_1403b22e4;
      if (*(uint *)(*(longlong *)(lVar7 + 8) + 8) <= uVar1) goto LAB_1403b22ea;
      FUN_1416a80d0(lVar8 + 8,
                    *(undefined8 *)
                     (*(longlong *)(*(longlong *)(lVar7 + 8) + 0x10 + (ulonglong)uVar1 * 8) + 8));
      lVar7 = FUN_1416a7aa0(&DAT_143324168);
      FUN_140a63590(lVar7,1);
      lVar9 = FUN_1416a7aa0(&DAT_143290d90);
      if (PTR_FUN_143f7c020 != (undefined *)0x0) {
        FUN_141681948();
      }
      FUN_1416a80d0(lVar9 + 0x20,*(undefined8 *)(DAT_143f8cac0 + 8));
      FUN_140a63980(lVar7);
      FUN_140a63620(lVar7,lVar9);
      if (*(longlong *)(lVar7 + 0x20) == 0) goto LAB_1403b22c7;
      *(undefined1 *)(lVar7 + 0x9e) = 1;
      FUN_1416a80d0(lVar9 + 8,lVar7);
      uVar6 = FUN_1404e9ad0(lVar9);
      uVar6 = FUN_141577ed0(&DAT_143182520,lVar8,uVar6);
      uVar5 = FUN_1416a7aa0(&DAT_1432c39e8);
      lVar7 = FUN_140720f40(&PTR_DAT_143ecdae0);
      if (PTR_FUN_143f7cc48 != (undefined *)0x0) {
        FUN_141682566();
      }
      uVar6 = FUN_1406d9590(uVar6,*(undefined8 *)(DAT_143f8d2f8 + 8));
      FUN_1406cf7a0(uVar5,uVar6);
      uVar6 = FUN_1406d4500(uVar5);
      if (PTR_FUN_143f7cd30 != (undefined *)0x0) {
        FUN_14168269a();
      }
      lVar8 = DAT_143f8d3e0;
      if (lVar7 == 0) {
        FUN_14071f620(uVar6);
      }
      else {
        FUN_14071daa0(uVar6,*(undefined8 *)(DAT_143f8d3e0 + 0xe8),lVar7);
      }
      FUN_1406d7810(uVar4,uVar5);
      uVar6 = FUN_1416a7aa0(&DAT_1432c9678);
      uVar4 = FUN_1406d78b0(uVar4);
      FUN_140717b50(uVar6,&PTR_DAT_143dbf460,*(undefined8 *)(lVar2 + 0x28));
      FUN_14071daa0(uVar4,*(undefined8 *)(lVar8 + 0x90),uVar6);
      local_40 = FUN_1402d5960(lVar2);
      if ((*(uint *)(local_40 + 0x34) & 0x1600000) == 0) {
        *(undefined4 *)(param_1 + 1) = 0;
        FUN_1416a8140(param_1 + 4,local_40);
        uVar4 = FUN_141435d40(param_1,param_1 + 3);
        FUN_141563a00(&PTR_DAT_1431bd660,&local_40,uVar4);
        return;
      }
      goto LAB_1403b1d4b;
    }
    lStack_48 = param_1[5];
    param_1[5] = 0;
    *(undefined4 *)(param_1 + 1) = 0xffffffff;
  }
  if ((*(uint *)(lStack_48 + 0x34) & 0x11000000) != 0x1000000) {
    FUN_140846ec0(lStack_48,0);
  }
  lVar8 = *(longlong *)(lStack_48 + 0x38);
  if (PTR_FUN_143f7b300 != (undefined *)0x0) {
    FUN_141680ca6();
  }
  lVar9 = DAT_143f8bf90;
  FUN_1416a80d0(*(longlong *)(DAT_143f8bf90 + 8) + 0x80,*(undefined8 *)(lVar8 + 8));
  if (*(int *)(lVar2 + 0x90) == 7) {
    FUN_1402d52e0(lVar2,7,8);
    lVar7 = *(longlong *)(lVar9 + 8);
    uVar1 = *(uint *)(lVar2 + 0x9c);
    if (*(uint *)(*(longlong *)(lVar2 + 0x30) + 0x10) <= uVar1) {
LAB_1403b22e4:
                    /* WARNING: Subroutine does not return */
      FUN_1407b16d0(lVar7);
    }
    lVar2 = *(longlong *)(*(longlong *)(lVar2 + 0x30) + 8);
    if (*(uint *)(lVar2 + 8) <= uVar1) {
LAB_1403b22ea:
                    /* WARNING: Subroutine does not return */
      FUN_140879890();
    }
    ClassicUO_Network_NetClientExt_Send_GameLogin
              (lVar7,*(undefined8 *)(*(longlong *)(lVar2 + 0x10 + (ulonglong)uVar1 * 8) + 8),
               *(undefined8 *)(lVar8 + 8));
    *(undefined1 *)(*(longlong *)(lVar9 + 8) + 0x70) = 1;
  }
LAB_1403b236f:
  *(undefined4 *)(param_1 + 1) = 0xfffffffe;
  FUN_140845c80(param_1 + 2);
  return;
}
```

### LoginScene getclientinfo async MoveNext (re-creates HttpClient) @ 0x1403b0bd0
```c
// ==== FUN_1403b0bd0 @ 1403b0bd0 size=3215  [fn] ====

/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

void FUN_1403b0bd0(longlong *param_1)

{
  undefined4 *puVar1;
  char cVar2;
  longlong lVar3;
  undefined8 *puVar4;
  longlong lVar5;
  longlong *plVar6;
  undefined4 uVar7;
  undefined4 uVar8;
  undefined8 uVar9;
  undefined8 uVar10;
  undefined1 uVar11;
  int iVar12;
  undefined4 uVar13;
  int iVar14;
  longlong lVar15;
  undefined8 uVar16;
  longlong lVar17;
  longlong lVar18;
  undefined8 uVar19;
  bool bVar20;
  ulonglong local_1a0 [6];
  undefined8 local_170;
  undefined8 uStack_168;
  uint local_160;
  undefined4 uStack_15c;
  undefined4 uStack_158;
  undefined4 uStack_154;
  undefined8 local_150;
  undefined8 local_148;
  undefined8 uStack_140;
  uint local_138;
  undefined4 uStack_134;
  undefined4 uStack_130;
  undefined4 uStack_12c;
  undefined8 local_128;
  undefined8 local_120;
  undefined8 uStack_118;
  uint local_110;
  undefined4 uStack_10c;
  undefined4 uStack_108;
  undefined4 uStack_104;
  undefined8 local_100;
  undefined8 local_f8;
  undefined8 uStack_f0;
  uint local_e8;
  undefined4 uStack_e4;
  undefined4 uStack_e0;
  undefined4 uStack_dc;
  undefined8 local_d8;
  undefined8 local_d0;
  undefined8 uStack_c8;
  uint local_c0;
  undefined4 uStack_bc;
  undefined4 uStack_b8;
  undefined4 uStack_b4;
  undefined8 local_b0;
  longlong local_a8;
  undefined8 local_a0;
  undefined8 uStack_98;
  uint local_90;
  undefined4 uStack_8c;
  undefined4 uStack_88;
  undefined4 uStack_84;
  undefined8 local_80;
  longlong local_78 [6];
  longlong local_48;
  
  local_1a0[0] = 0;
  lVar15 = -0x150;
  do {
    *(undefined8 *)((longlong)&local_48 + lVar15) = 0;
    *(undefined8 *)(&stack0xffffffffffffffc0 + lVar15) = 0;
    *(undefined8 *)(&stack0xffffffffffffffc8 + lVar15) = 0;
    *(undefined8 *)(&stack0xffffffffffffffd0 + lVar15) = 0;
    *(undefined8 *)(&stack0xffffffffffffffd8 + lVar15) = 0;
    *(undefined8 *)(&stack0xffffffffffffffe0 + lVar15) = 0;
    lVar15 = lVar15 + 0x30;
  } while (lVar15 != 0);
  iVar14 = (int)param_1[3];
  lVar3 = *param_1;
  local_48 = lVar15;
  switch(iVar14) {
  case 0:
    goto switchD_1403b0c4a_caseD_0;
  case 1:
    local_78[0] = param_1[7];
    param_1[7] = 0;
    *(undefined4 *)(param_1 + 3) = 0xffffffff;
    goto LAB_1403b0c61;
  case 2:
    goto switchD_1403b0c4a_caseD_2;
  }
  uVar16 = FUN_1416a7aa0(&DAT_14326af58);
  FUN_1416a8140(param_1 + 1,uVar16);
  if (*(longlong *)(lVar3 + 0x20) == 0) goto LAB_1403b1836;
switchD_1403b0c4a_caseD_0:
  if (iVar14 == 0) {
    local_48 = param_1[6];
    param_1[6] = 0;
    iVar14 = -1;
    *(undefined4 *)(param_1 + 3) = 0xffffffff;
  }
  else {
    uVar16 = *(undefined8 *)(lVar3 + 0x20);
    uVar19 = FUN_1416a7aa0(&DAT_1432f6fa0);
    local_1a0[0] = local_1a0[0] & 0xffffffffffffff00;
    FUN_1408d1f00(uVar19,&PTR_DAT_143ed4d50,0,0,local_1a0);
    if (PTR_FUN_143f7cc50 != (undefined *)0x0) {
      FUN_14168257c();
    }
    uVar19 = FUN_1406d38b0(uVar16,*(undefined8 *)(DAT_143f8d308 + 8),uVar19);
    local_48 = FUN_1406d3000(uVar16,uVar19,0,0);
    if ((*(uint *)(local_48 + 0x34) & 0x1600000) == 0) {
      *(undefined4 *)(param_1 + 3) = 0;
      FUN_1416a8140(param_1 + 6,local_48);
      uVar16 = FUN_141435940(param_1,param_1 + 5);
      FUN_141563a00(&PTR_DAT_1431bd660,&local_48,uVar16);
      return;
    }
  }
  if ((*(uint *)(local_48 + 0x34) & 0x11000000) != 0x1000000) {
    FUN_140846ec0(local_48,0);
  }
  FUN_1416a8140(param_1 + 2,*(undefined8 *)(local_48 + 0x38));
  iVar12 = FUN_1406d7ed0();
  if (iVar12 == 0) {
    uVar16 = FUN_1406d7cb0();
    local_78[0] = FUN_1406d45b0(uVar16,0);
    if ((*(uint *)(local_78[0] + 0x34) & 0x1600000) == 0) {
      *(undefined4 *)(param_1 + 3) = 1;
      FUN_1416a8140(param_1 + 7,local_78[0]);
      uVar16 = FUN_141435940(param_1,param_1 + 5);
      FUN_141563a00(&PTR_DAT_1431bf998,local_78,uVar16);
      return;
    }
LAB_1403b0c61:
    if ((*(uint *)(local_78[0] + 0x34) & 0x11000000) != 0x1000000) {
      FUN_140846ec0(local_78[0],0);
    }
    lVar15 = *(longlong *)(local_78[0] + 0x38);
    FUN_1408463e0(&local_170,0x15,2);
    uVar8 = _UNK_1438a1e2c;
    uVar7 = _UNK_1438a1e28;
    uVar13 = _UNK_1438a1e24;
    local_a0 = local_170;
    uStack_98 = uStack_168;
    local_90 = local_160;
    uStack_8c = uStack_15c;
    uStack_88 = uStack_158;
    uStack_84 = uStack_154;
    local_80 = local_150;
    uVar10 = local_80;
    local_80._0_4_ = (uint)local_150;
    uVar16 = local_d8;
    uVar19 = local_b0;
    if ((uint)local_80 < local_160) goto LAB_1403b1830;
    puVar1 = (undefined4 *)(CONCAT44(uStack_154,uStack_158) + (ulonglong)local_160 * 2);
    local_80 = uVar10;
    if ((uint)local_80 - local_160 < 0xd) {
      FUN_140846910(&local_a0,&PTR_DAT_143e6f468);
    }
    else {
      *puVar1 = _DAT_1438a1e20;
      puVar1[1] = uVar13;
      puVar1[2] = uVar7;
      puVar1[3] = uVar8;
      *(undefined8 *)(puVar1 + 4) = 0x3a00650064006f;
      *(undefined2 *)(puVar1 + 6) = 0x20;
      local_90 = local_160 + 0xd;
    }
    FUN_1414f0800(&local_a0,*(undefined4 *)(param_1[2] + 0x38));
    uVar8 = _UNK_1438a1e3c;
    uVar7 = _UNK_1438a1e38;
    uVar13 = _UNK_1438a1e34;
    uVar16 = local_d8;
    uVar19 = local_b0;
    uVar10 = local_80;
    if ((uint)local_80 < local_90) goto LAB_1403b1830;
    puVar1 = (undefined4 *)(CONCAT44(uStack_84,uStack_88) + (ulonglong)local_90 * 2);
    if ((uint)local_80 - local_90 < 8) {
      FUN_140846910(&local_a0,&PTR_DAT_143d8e608);
    }
    else {
      *puVar1 = _DAT_1438a1e30;
      puVar1[1] = uVar13;
      puVar1[2] = uVar7;
      puVar1[3] = uVar8;
      local_90 = local_90 + 8;
    }
    if (((char)uStack_8c == '\0') && (lVar15 != 0)) {
      uVar16 = local_d8;
      uVar19 = local_b0;
      uVar10 = local_80;
      if ((uint)local_80 < local_90) goto LAB_1403b1830;
      if ((uint)local_80 - local_90 < *(uint *)(lVar15 + 8)) goto LAB_1403b0dc1;
      FUN_1407afac0(CONCAT44(uStack_84,uStack_88) + (ulonglong)local_90 * 2,lVar15 + 0xc,
                    (ulonglong)*(uint *)(lVar15 + 8) * 2);
      local_90 = local_90 + *(int *)(lVar15 + 8);
    }
    else {
LAB_1403b0dc1:
      FUN_140846770(&local_a0,lVar15);
    }
    uVar16 = FUN_140846580(&local_a0);
    FUN_1402d6b80(lVar3,uVar16);
    *(undefined ***)(lVar3 + 0x48) = &PTR_DAT_143db7a68;
    *(undefined1 *)(lVar3 + 0xb7) = 1;
  }
  else {
switchD_1403b0c4a_caseD_2:
    if (iVar14 == 2) {
      local_a8 = param_1[8];
      param_1[8] = 0;
      *(undefined4 *)(param_1 + 3) = 0xffffffff;
    }
    else {
      lVar15 = FUN_1416a7aa0(&DAT_143324168);
      lVar17 = FUN_1406d7cb0();
      FUN_140a63590(lVar15,1);
      lVar18 = FUN_1416a7aa0(&DAT_1432904a0);
      if (PTR_FUN_143f7bfc8 != (undefined *)0x0) {
        FUN_141681856();
      }
      FUN_1416a80d0(lVar18 + 0x48,*(undefined8 *)(DAT_143f8ca68 + 8));
      FUN_140a63980(lVar15);
      FUN_140a63620(lVar15,lVar18);
      if (*(longlong *)(lVar15 + 0x20) == 0) {
                    /* WARNING: Subroutine does not return */
        FUN_140a4aab0();
      }
      *(undefined1 *)(lVar15 + 0x9e) = 1;
      FUN_1416a80d0(lVar18 + 8,lVar15);
      uVar16 = FUN_1404de8c0(lVar18);
      if (lVar17 == 0) {
                    /* WARNING: Subroutine does not return */
        FUN_1407853c0(&PTR_DAT_143ed66e8);
      }
      local_a8 = FUN_14156daa0(&PTR_DAT_143181630,lVar17,uVar16,0);
      if ((*(uint *)(local_a8 + 0x34) & 0x1600000) == 0) {
        *(undefined4 *)(param_1 + 3) = 2;
        FUN_1416a8140(param_1 + 8,local_a8);
        uVar16 = FUN_141435940(param_1,param_1 + 5);
        FUN_141563a00(&PTR_DAT_1431b9ad0,&local_a8,uVar16);
        return;
      }
    }
    if ((*(uint *)(local_a8 + 0x34) & 0x11000000) != 0x1000000) {
      FUN_140846ec0(local_a8,0);
    }
    lVar15 = *(longlong *)(local_a8 + 0x38);
    lVar17 = param_1[1];
    if (PTR_FUN_143f7b1b8 != (undefined *)0x0) {
      FUN_141680b1a();
    }
    lVar18 = DAT_143f8bec0;
    iVar14 = *(int *)(*(longlong *)(DAT_143f8bec0 + 0x10) + 0x10);
    *(bool *)(lVar17 + 8) = iVar14 == 3;
    *(bool *)(lVar3 + 0x17) = iVar14 == 4;
    FUN_1416a80d0(lVar3 + 0x50,*(undefined8 *)(lVar15 + 0x10));
    FUN_1416a80d0(lVar3 + 0x58,*(undefined8 *)(lVar15 + 8));
    *(undefined4 *)(lVar3 + 0xa4) = *(undefined4 *)(lVar15 + 0x20);
    FUN_1416a80d0(lVar3 + 0x60,*(undefined8 *)(lVar15 + 0x18));
    *(undefined1 *)(lVar3 + 0xb6) = *(undefined1 *)(lVar15 + 0x34);
    if (*(char *)(lVar3 + 0x17) == '\0') {
      uVar19 = FUN_1416a7aa0(&DAT_1434ce1c0);
      uVar16 = *(undefined8 *)(lVar3 + 0x50);
      FUN_141689378(uVar19,param_1[1]);
      uVar11 = FUN_14156b040(&PTR_DAT_143181250,uVar16,uVar19);
    }
    else {
      uVar11 = 0;
    }
    *(undefined1 *)(lVar3 + 0x16) = uVar11;
    if (*(int *)(*(longlong *)(lVar18 + 0x10) + 0x10) == 3) {
      uVar13 = *(undefined4 *)(lVar15 + 0x28);
    }
    else {
      uVar13 = *(undefined4 *)(lVar15 + 0x24);
    }
    *(undefined4 *)(lVar3 + 0xa8) = uVar13;
    cVar2 = *(char *)(lVar15 + 0x35);
    *(char *)(lVar3 + 0xb5) = cVar2;
    iVar14 = *(int *)(lVar15 + 0x2c);
    *(int *)(lVar3 + 0xac) = iVar14;
    *(undefined4 *)(lVar3 + 0xb0) = *(undefined4 *)(lVar15 + 0x30);
    if (((*(char *)(lVar3 + 0x17) == '\0') && (cVar2 != '\0')) && (0 < iVar14)) {
      if (PTR_FUN_143f7b1b8 != (undefined *)0x0) {
        FUN_14167ee08();
      }
      bVar20 = DAT_143f7b1d0 < iVar14;
    }
    else {
      bVar20 = false;
    }
    *(bool *)(lVar3 + 0xb4) = bVar20;
    if (*(char *)(lVar3 + 0xb6) != '\0') {
      lVar15 = *(longlong *)(lVar3 + 0x20);
      if (lVar15 != 0) {
        if (*(char *)(lVar15 + 0x13) == '\0') {
          *(undefined1 *)(lVar15 + 0x13) = 1;
          lVar17 = *(longlong *)(lVar15 + 0x18);
          if (*(char *)(lVar17 + 0x24) != '\0') {
                    /* WARNING: Subroutine does not return */
            FUN_1407b2010(0x46);
          }
          iVar14 = FUN_1407f92a0(lVar17);
          if (iVar14 != 0) {
            FUN_1407f9300(lVar17,0);
          }
          puVar4 = *(undefined8 **)(lVar15 + 0x18);
          if ((undefined *)*puVar4 == &DAT_1432d9c80) {
LAB_1403b13e1:
            FUN_1407f8fb0(puVar4,1);
          }
          else if ((undefined *)*puVar4 == &DAT_1432ecf88) {
            if (*(char *)((longlong)puVar4 + 0x24) == '\0') {
              lVar17 = puVar4[5];
              if ((lVar17 != 0) &&
                 (iVar14 = FUN_14088d580(*(undefined8 *)(lVar17 + 8),puVar4[6],lVar17), iVar14 == 0)
                 ) {
                uVar16 = puVar4[6];
                lVar5 = *(longlong *)(*(longlong *)(lVar17 + 8) + 8);
                if ((*(int *)(lVar5 + 0x20) != 0) &&
                   ((*(int *)(lVar5 + 0x20) != 2 &&
                    (iVar14 = *(int *)(*(longlong *)(lVar17 + 8) + 0x30), iVar12 = FUN_1407f4360(),
                    iVar14 != iVar12)))) {
                  FUN_14088d680(*(undefined8 *)(lVar17 + 8),uVar16);
                }
              }
              goto LAB_1403b13e1;
            }
          }
          else if (*(char *)((longlong)puVar4 + 0x24) == '\0') {
            lVar17 = puVar4[5];
            if ((lVar17 != 0) &&
               (iVar14 = FUN_14088d580(*(undefined8 *)(lVar17 + 8),puVar4[6],lVar17), iVar14 == 0))
            {
              uVar16 = puVar4[6];
              lVar5 = *(longlong *)(*(longlong *)(lVar17 + 8) + 8);
              if ((*(int *)(lVar5 + 0x20) != 0) &&
                 ((*(int *)(lVar5 + 0x20) != 2 &&
                  (iVar14 = *(int *)(*(longlong *)(lVar17 + 8) + 0x30), iVar12 = FUN_1407f4360(),
                  iVar14 != iVar12)))) {
                FUN_14088d680(*(undefined8 *)(lVar17 + 8),uVar16);
              }
            }
            lVar17 = puVar4[7];
            if ((lVar17 != 0) &&
               (iVar14 = FUN_14088d580(*(undefined8 *)(lVar17 + 8),puVar4[8],lVar17), iVar14 == 0))
            {
              uVar16 = puVar4[8];
              lVar5 = *(longlong *)(*(longlong *)(lVar17 + 8) + 8);
              if ((*(int *)(lVar5 + 0x20) != 0) &&
                 ((*(int *)(lVar5 + 0x20) != 2 &&
                  (iVar14 = *(int *)(*(longlong *)(lVar17 + 8) + 0x30), iVar12 = FUN_1407f4360(),
                  iVar14 != iVar12)))) {
                FUN_14088d680(*(undefined8 *)(lVar17 + 8),uVar16);
              }
            }
            goto LAB_1403b13e1;
          }
          FUN_14076d450(puVar4);
        }
        if ((*(char *)(lVar15 + 0x10) == '\0') &&
           (*(undefined1 *)(lVar15 + 0x10) = 1, *(char *)(lVar15 + 0x11) != '\0')) {
          plVar6 = *(longlong **)(lVar15 + 8);
          (**(code **)(*plVar6 + 0x40))(plVar6,1);
          FUN_14076d450(plVar6);
        }
        FUN_14076d450(lVar15);
      }
      uVar16 = FUN_1402d4370(lVar3,1);
      FUN_1416a80d0(lVar3 + 0x20,uVar16);
    }
    if (PTR_FUN_143f7b1b8 != (undefined *)0x0) {
      FUN_14167ee08();
    }
    if (DAT_143f7b1cc != '\0') {
      FUN_1402adf40(*(undefined8 *)(lVar3 + 0x18),0xd,0);
      goto LAB_1403b1836;
    }
    iVar14 = FUN_1402d3da0(lVar3);
    if (iVar14 == 0) {
      FUN_1408463e0(&local_148,0x88,2);
      local_d0 = local_148;
      uStack_c8 = uStack_140;
      local_c0 = local_138;
      uStack_bc = uStack_134;
      uStack_b8 = uStack_130;
      uStack_b4 = uStack_12c;
      local_b0 = local_128;
      uVar19 = local_b0;
      local_b0._0_4_ = (uint)local_128;
      uVar16 = local_d8;
      uVar10 = local_80;
      if ((uint)local_b0 < local_138) {
LAB_1403b1830:
        local_80 = uVar10;
        local_b0 = uVar19;
        local_d8 = uVar16;
                    /* WARNING: Subroutine does not return */
        FUN_1407b15d0();
      }
      local_b0 = uVar19;
      if ((uint)local_b0 - local_138 < 0x29) {
        FUN_140846910(&local_d0,&PTR_DAT_143db0010);
      }
      else {
        FUN_1407afac0(CONCAT44(uStack_12c,uStack_130) + (ulonglong)local_138 * 2,
                      u_A_more_recent_version_of_the_cli_143db001c,0x52);
        local_c0 = local_c0 + 0x29;
      }
      FUN_1414f0800(&local_d0,*(undefined4 *)(*(longlong *)(lVar18 + 0x10) + 0x10));
      uVar16 = local_d8;
      uVar19 = local_b0;
      uVar10 = local_80;
      if ((uint)local_b0 < local_c0) goto LAB_1403b1830;
      if ((uint)local_b0 == local_c0) {
        FUN_140846910(&local_d0,&PTR_DAT_143da0338);
      }
      else {
        *(undefined2 *)(CONCAT44(uStack_b4,uStack_b8) + (ulonglong)local_c0 * 2) = 0x2e;
        local_c0 = local_c0 + 1;
      }
      FUN_1414f0800(&local_d0,*(undefined4 *)(lVar3 + 0xa8));
      uVar16 = local_d8;
      uVar19 = local_b0;
      uVar10 = local_80;
      if ((uint)local_b0 < local_c0) goto LAB_1403b1830;
      if ((uint)local_b0 - local_c0 < 0x5e) {
        FUN_140846910(&local_d0,&PTR_DAT_143d9cd78);
      }
      else {
        FUN_1407afac0(CONCAT44(uStack_b4,uStack_b8) + (ulonglong)local_c0 * 2,
                      u___is_available_to_download__Plea_143d9cd84,0xbc);
        local_c0 = local_c0 + 0x5e;
      }
      uVar16 = FUN_140846580(&local_d0);
      FUN_1416a80d0(lVar3 + 0x48,uVar16);
    }
    else {
      iVar14 = FUN_1402d3e00(lVar3);
      if (iVar14 != 0) goto LAB_1403b1836;
      FUN_1408463e0(&local_120,0x79,1);
      uVar9 = _UNK_1438a1e48;
      local_f8 = local_120;
      uStack_f0 = uStack_118;
      local_e8 = local_110;
      uStack_e4 = uStack_10c;
      uStack_e0 = uStack_108;
      uStack_dc = uStack_104;
      local_d8 = local_100;
      uVar16 = local_d8;
      local_d8._0_4_ = (uint)local_100;
      uVar19 = local_b0;
      uVar10 = local_80;
      if ((uint)local_d8 < local_110) goto LAB_1403b1830;
      puVar4 = (undefined8 *)(CONCAT44(uStack_104,uStack_108) + (ulonglong)local_110 * 2);
      local_d8 = uVar16;
      if ((uint)local_d8 - local_110 < 0x1c) {
        FUN_140846910(&local_f8,&PTR_DAT_143eb45d8);
      }
      else {
        *puVar4 = _DAT_1438a1e40;
        puVar4[1] = uVar9;
        uVar16 = _UNK_1438a1e58;
        puVar4[2] = _DAT_1438a1e50;
        puVar4[3] = uVar16;
        uVar8 = _UNK_1438a1e6c;
        uVar7 = _UNK_1438a1e68;
        uVar13 = _UNK_1438a1e64;
        *(undefined4 *)(puVar4 + 4) = _DAT_1438a1e60;
        *(undefined4 *)((longlong)puVar4 + 0x24) = uVar13;
        *(undefined4 *)(puVar4 + 5) = uVar7;
        *(undefined4 *)((longlong)puVar4 + 0x2c) = uVar8;
        puVar4[6] = 0x20006e006f0069;
        local_e8 = local_110 + 0x1c;
      }
      FUN_1414f0800(&local_f8,*(undefined4 *)(lVar3 + 0xb0));
      uVar16 = local_d8;
      uVar19 = local_b0;
      uVar10 = local_80;
      if ((uint)local_d8 < local_e8) goto LAB_1403b1830;
      if ((uint)local_d8 - local_e8 < 0x5d) {
        FUN_140846910(&local_f8,&PTR_DAT_143d9cb70);
      }
      else {
        FUN_1407afac0(CONCAT44(uStack_dc,uStack_e0) + (ulonglong)local_e8 * 2,
                      u___are_available__Please_consider_143d9cb7c,0xba);
        local_e8 = local_e8 + 0x5d;
      }
      uVar16 = FUN_140846580(&local_f8);
      FUN_1416a80d0(lVar3 + 0x48,uVar16);
    }
  }
  FUN_1402d52e0(lVar3,*(undefined4 *)(lVar3 + 0x90),0x1c);
LAB_1403b1836:
  *(undefined4 *)(param_1 + 3) = 0xfffffffe;
  param_1[1] = 0;
  param_1[2] = 0;
  FUN_140845c80(param_1 + 4);
  return;
}
```

### NetClientExt.Send_GameLogin (0x91) @ 0x1401480e0
```c
// ==== ClassicUO.Network.NetClientExt.Send_GameLogin @ 1401480e0 size=2449  [fn] ====

/* WARNING: Removing unreachable block (ram,0x00014014813c) */

void ClassicUO_Network_NetClientExt_Send_GameLogin
               (undefined8 param_1,undefined8 param_2,undefined8 param_3)

{
  longlong lVar1;
  ushort uVar2;
  int iVar3;
  int iVar4;
  uint uVar5;
  int iVar6;
  longlong lVar7;
  undefined8 uVar8;
  undefined4 uVar9;
  longlong lVar10;
  uint uVar11;
  undefined4 local_60 [2];
  longlong local_58;
  longlong lStack_50;
  undefined8 local_48;
  longlong lStack_40;
  ulonglong local_38;
  
  local_58 = 0;
  lStack_50 = 0;
  local_48 = 0;
  lStack_40 = 0;
  local_38 = 0;
  iVar3 = ClassicUO_Network_PacketsTable_GetPacketLength();
  iVar6 = 0x40;
  if (-1 < iVar3) {
    iVar6 = iVar3;
  }
  lStack_50 = 0;
  lStack_40 = 0;
  local_38 = 0;
  local_48 = 0;
  if (0 < iVar6) {
    if (iVar6 < 0) {
      iVar6 = 0;
    }
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar1 = DAT_143f93980;
    lVar7 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar6);
    if (lStack_50 != 0) {
      if ((uint)local_38 < local_48._4_4_) goto LAB_140148a5f;
      if (lVar7 == 0) {
        lVar10 = 0;
        uVar11 = 0;
      }
      else {
        lVar10 = lVar7 + 0x10;
        uVar11 = *(uint *)(lVar7 + 8);
      }
      if (uVar11 < local_48._4_4_) goto LAB_140148a59;
      FUN_1407afac0(lVar10,lStack_40,local_48._4_4_);
      if (lStack_50 != 0) {
        FUN_141182540(*(undefined8 *)(lVar1 + 8),lStack_50,0);
      }
    }
    if (lVar7 == 0) {
      lStack_40 = 0;
      uVar9 = 0;
    }
    else {
      lStack_40 = lVar7 + 0x10;
      uVar9 = *(undefined4 *)(lVar7 + 8);
    }
    local_38 = CONCAT44(local_38._4_4_,uVar9);
    lStack_50 = lVar7;
  }
  if ((int)(uint)local_38 < (int)((uint)local_48 + 1)) {
    iVar6 = local_48._4_4_ + 1;
    if ((int)(local_48._4_4_ + 1) < (int)((uint)local_38 * 2)) {
      iVar6 = (uint)local_38 * 2;
    }
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar1 = DAT_143f93980;
    lVar7 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar6);
    if (lStack_50 != 0) {
      if ((uint)local_38 < local_48._4_4_) goto LAB_140148a5f;
      if (lVar7 == 0) {
        lVar10 = 0;
        uVar11 = 0;
      }
      else {
        lVar10 = lVar7 + 0x10;
        uVar11 = *(uint *)(lVar7 + 8);
      }
      if (uVar11 < local_48._4_4_) goto LAB_140148a59;
      FUN_1407afac0(lVar10,lStack_40,local_48._4_4_);
      if (lStack_50 != 0) {
        FUN_141182540(*(undefined8 *)(lVar1 + 8),lStack_50,0);
      }
    }
    if (lVar7 == 0) {
      lStack_40 = 0;
      uVar9 = 0;
    }
    else {
      lStack_40 = lVar7 + 0x10;
      uVar9 = *(undefined4 *)(lVar7 + 8);
    }
    local_38 = CONCAT44(local_38._4_4_,uVar9);
    lStack_50 = lVar7;
  }
  uVar11 = (uint)local_48;
  if ((uint)local_38 <= (uint)local_48) {
LAB_140148a6f:
                    /* WARNING: Subroutine does not return */
    FUN_140879890();
  }
  *(undefined1 *)(lStack_40 + (local_48 & 0xffffffff)) = 0x91;
  iVar4 = (uint)local_48 + 1;
  iVar6 = iVar4;
  if (iVar4 < (int)local_48._4_4_) {
    iVar6 = local_48._4_4_;
  }
  local_48 = CONCAT44(iVar6,iVar4);
  if (iVar3 < 0) {
    if ((int)(uint)local_38 < (int)(uVar11 + 3)) {
      iVar4 = iVar6 + 2;
      if (iVar6 + 2 < (int)((uint)local_38 * 2)) {
        iVar4 = (uint)local_38 * 2;
      }
      if (PTR_FUN_143f834a8 != (undefined *)0x0) {
        FUN_141684910();
      }
      lVar1 = DAT_143f93980;
      lVar7 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar4);
      if (lStack_50 != 0) {
        if ((uint)local_38 < local_48._4_4_) goto LAB_140148a5f;
        if (lVar7 == 0) {
          lVar10 = 0;
          uVar11 = 0;
        }
        else {
          lVar10 = lVar7 + 0x10;
          uVar11 = *(uint *)(lVar7 + 8);
        }
        if (uVar11 < local_48._4_4_) goto LAB_140148a59;
        FUN_1407afac0(lVar10,lStack_40,local_48._4_4_);
        if (lStack_50 != 0) {
          FUN_141182540(*(undefined8 *)(lVar1 + 8),lStack_50,0);
        }
      }
      if (lVar7 == 0) {
        lStack_40 = 0;
        uVar9 = 0;
      }
      else {
        lStack_40 = lVar7 + 0x10;
        uVar9 = *(undefined4 *)(lVar7 + 8);
      }
      local_38 = CONCAT44(local_38._4_4_,uVar9);
      lStack_50 = lVar7;
    }
    if ((local_38 & 0xffffffff) < (local_48 & 0xffffffff) + 2) goto LAB_140148a5f;
    *(undefined2 *)((local_48 & 0xffffffff) + lStack_40) = 0;
    iVar4 = (uint)local_48 + 2;
    iVar6 = iVar4;
    if (iVar4 < (int)local_48._4_4_) {
      iVar6 = local_48._4_4_;
    }
    local_48 = CONCAT44(iVar6,iVar4);
  }
  uVar8 = FUN_140127220();
  FUN_1414ab7c0(&lStack_50,uVar8,param_2,0xffffffff);
  if ((int)(uint)local_38 < (int)((uint)local_48 + 1)) {
    iVar6 = local_48._4_4_ + 1;
    if ((int)(local_48._4_4_ + 1) < (int)((uint)local_38 * 2)) {
      iVar6 = (uint)local_38 * 2;
    }
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar1 = DAT_143f93980;
    lVar7 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar6);
    if (lStack_50 != 0) {
      if ((uint)local_38 < local_48._4_4_) goto LAB_140148a5f;
      if (lVar7 == 0) {
        lVar10 = 0;
        uVar11 = 0;
      }
      else {
        lVar10 = lVar7 + 0x10;
        uVar11 = *(uint *)(lVar7 + 8);
      }
      if (uVar11 < local_48._4_4_) goto LAB_140148a59;
      FUN_1407afac0(lVar10,lStack_40,local_48._4_4_);
      if (lStack_50 != 0) {
        FUN_141182540(*(undefined8 *)(lVar1 + 8),lStack_50,0);
      }
    }
    if (lVar7 == 0) {
      lStack_40 = 0;
      uVar9 = 0;
    }
    else {
      lStack_40 = lVar7 + 0x10;
      uVar9 = *(undefined4 *)(lVar7 + 8);
    }
    local_38 = CONCAT44(local_38._4_4_,uVar9);
    lStack_50 = lVar7;
  }
  if ((uint)local_38 <= (uint)local_48) goto LAB_140148a6f;
  *(undefined1 *)(lStack_40 + (local_48 & 0xffffffff)) = 0;
  iVar4 = (uint)local_48 + 1;
  iVar6 = iVar4;
  if (iVar4 < (int)local_48._4_4_) {
    iVar6 = local_48._4_4_;
  }
  local_48 = CONCAT44(iVar6,iVar4);
  uVar8 = FUN_140127220();
  FUN_1414ab7c0(&lStack_50,uVar8,param_3,0xffffffff);
  if ((int)(uint)local_38 < (int)((uint)local_48 + 1)) {
    iVar6 = local_48._4_4_ + 1;
    if ((int)(local_48._4_4_ + 1) < (int)((uint)local_38 * 2)) {
      iVar6 = (uint)local_38 * 2;
    }
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar1 = DAT_143f93980;
    lVar7 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar6);
    if (lStack_50 != 0) {
      if ((uint)local_38 < local_48._4_4_) goto LAB_140148a5f;
      if (lVar7 == 0) {
        lVar10 = 0;
        uVar11 = 0;
      }
      else {
        lVar10 = lVar7 + 0x10;
        uVar11 = *(uint *)(lVar7 + 8);
      }
      if (uVar11 < local_48._4_4_) goto LAB_140148a59;
      FUN_1407afac0(lVar10,lStack_40,local_48._4_4_);
      if (lStack_50 != 0) {
        FUN_141182540(*(undefined8 *)(lVar1 + 8),lStack_50,0);
      }
    }
    if (lVar7 == 0) {
      lStack_40 = 0;
      uVar9 = 0;
    }
    else {
      lStack_40 = lVar7 + 0x10;
      uVar9 = *(undefined4 *)(lVar7 + 8);
    }
    local_38 = CONCAT44(local_38._4_4_,uVar9);
    lStack_50 = lVar7;
  }
  if ((uint)local_38 <= (uint)local_48) goto LAB_140148a6f;
  *(undefined1 *)(lStack_40 + (local_48 & 0xffffffff)) = 0;
  iVar4 = (uint)local_48 + 1;
  iVar6 = iVar4;
  if (iVar4 < (int)local_48._4_4_) {
    iVar6 = local_48._4_4_;
  }
  local_48 = CONCAT44(iVar6,iVar4);
  if (iVar3 < 0) {
    iVar3 = 1;
    if (1 < iVar6) {
      iVar3 = iVar6;
    }
    local_48 = CONCAT44(iVar3,1);
    if ((int)(uint)local_38 < (int)(-(uint)local_38 + 3)) {
      iVar3 = -(uint)local_38 + 2 + iVar3;
      if (iVar3 < (int)((uint)local_38 * 2)) {
        iVar3 = (uint)local_38 * 2;
      }
      if (PTR_FUN_143f834a8 != (undefined *)0x0) {
        FUN_141684910();
      }
      lVar1 = DAT_143f93980;
      lVar7 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar3);
      if (lStack_50 != 0) {
        if ((uint)local_38 < local_48._4_4_) goto LAB_140148a5f;
        if (lVar7 == 0) {
          lVar10 = 0;
          uVar11 = 0;
        }
        else {
          lVar10 = lVar7 + 0x10;
          uVar11 = *(uint *)(lVar7 + 8);
        }
        if (uVar11 < local_48._4_4_) goto LAB_140148a59;
        FUN_1407afac0(lVar10,lStack_40,local_48._4_4_);
        if (lStack_50 != 0) {
          FUN_141182540(*(undefined8 *)(lVar1 + 8),lStack_50,0);
        }
      }
      if (lVar7 == 0) {
        lStack_40 = 0;
        uVar9 = 0;
      }
      else {
        lStack_40 = lVar7 + 0x10;
        uVar9 = *(undefined4 *)(lVar7 + 8);
      }
      local_38 = CONCAT44(local_38._4_4_,uVar9);
      lStack_50 = lVar7;
    }
    uVar2 = local_48._4_2_;
    if ((int)(uint)local_38 < (int)((uint)local_48 + 2)) {
      if (PTR_FUN_143f834a8 != (undefined *)0x0) {
        FUN_141684910();
      }
      lVar1 = DAT_143f93980;
      lVar7 = FUN_1411822c0();
      if (lStack_50 != 0) {
        if ((uint)local_38 < local_48._4_4_) goto LAB_140148a5f;
        if (lVar7 == 0) {
          lVar10 = 0;
          uVar11 = 0;
        }
        else {
          lVar10 = lVar7 + 0x10;
          uVar11 = *(uint *)(lVar7 + 8);
        }
        if (uVar11 < local_48._4_4_) {
LAB_140148a59:
                    /* WARNING: Subroutine does not return */
          FUN_1407b1600();
        }
        FUN_1407afac0(lVar10,lStack_40,local_48._4_4_);
        if (lStack_50 != 0) {
          FUN_141182540(*(undefined8 *)(lVar1 + 8),lStack_50,0);
        }
      }
      if (lVar7 == 0) {
        lStack_40 = 0;
        uVar9 = 0;
      }
      else {
        lStack_40 = lVar7 + 0x10;
        uVar9 = *(undefined4 *)(lVar7 + 8);
      }
      local_38 = CONCAT44(local_38._4_4_,uVar9);
      lStack_50 = lVar7;
    }
    if ((uint)local_38 < (uint)local_48) goto LAB_140148a5f;
    if ((uint)local_38 - (uint)local_48 < 2) {
                    /* WARNING: Subroutine does not return */
      FUN_1407b1c10(0x28);
    }
    *(ushort *)((local_48 & 0xffffffff) + lStack_40) = uVar2 >> 8 | uVar2 << 8;
    iVar3 = (uint)local_48 + 2;
    iVar6 = iVar3;
    if (iVar3 < (int)local_48._4_4_) {
      iVar6 = local_48._4_4_;
    }
    local_48 = CONCAT44(iVar6,iVar3);
  }
  else {
    uVar11 = iVar3 - iVar6;
    if (0 < (int)uVar11) {
      if ((int)(uint)local_38 < (int)(uVar11 + iVar4)) {
        if (PTR_FUN_143f834a8 != (undefined *)0x0) {
          FUN_141684910();
        }
        lVar1 = DAT_143f93980;
        lVar7 = FUN_1411822c0();
        if (lStack_50 != 0) {
          if ((uint)local_38 < local_48._4_4_) goto LAB_140148a5f;
          if (lVar7 == 0) {
            lVar10 = 0;
            uVar5 = 0;
          }
          else {
            lVar10 = lVar7 + 0x10;
            uVar5 = *(uint *)(lVar7 + 8);
          }
          if (uVar5 < local_48._4_4_) goto LAB_140148a59;
          FUN_1407afac0(lVar10,lStack_40,local_48._4_4_);
          if (lStack_50 != 0) {
            FUN_141182540(*(undefined8 *)(lVar1 + 8),lStack_50,0);
          }
        }
        if (lVar7 == 0) {
          lStack_40 = 0;
          uVar9 = 0;
        }
        else {
          lStack_40 = lVar7 + 0x10;
          uVar9 = *(undefined4 *)(lVar7 + 8);
        }
        local_38 = CONCAT44(local_38._4_4_,uVar9);
        lStack_50 = lVar7;
      }
      if ((local_48 & 0xffffffff) + (ulonglong)uVar11 <= (local_38 & 0xffffffff)) {
        FUN_1414a7780((local_48 & 0xffffffff) + lStack_40,uVar11,0);
        iVar3 = uVar11 + (uint)local_48;
        iVar6 = iVar3;
        if (iVar3 < (int)local_48._4_4_) {
          iVar6 = local_48._4_4_;
        }
        local_48 = CONCAT44(iVar6,iVar3);
        goto LAB_14014897d;
      }
LAB_140148a5f:
                    /* WARNING: Subroutine does not return */
      FUN_1407b15d0();
    }
  }
LAB_14014897d:
  local_58 = lStack_50;
  local_60[0] = local_48._4_4_;
  iVar6 = FUN_1401a2ba0(&local_58,local_60);
  if (iVar6 != 0) {
    ClassicUO_Network_NetClient_Send(param_1,local_58,local_60[0]);
  }
  if (lStack_50 != 0) {
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    FUN_141182540(*(undefined8 *)(DAT_143f93980 + 8),lStack_50,0);
  }
  return;
}
```

### NetClientExt.Send_Info (0xFF) @ 0x140147ae0
```c
// ==== ClassicUO.Network.NetClientExt.Send_Info @ 140147ae0 size=1525  [fn] ====

void ClassicUO_Network_NetClientExt_Send_Info(undefined8 param_1)

{
  int iVar1;
  longlong lVar2;
  longlong lVar3;
  longlong lVar4;
  undefined1 *puVar5;
  uint uVar6;
  int iVar7;
  uint uVar8;
  uint uVar9;
  uint uVar10;
  undefined1 *puVar11;
  uint uVar12;
  
  iVar1 = ClassicUO_Network_PacketsTable_GetPacketLength(0xff);
  iVar7 = 0x40;
  if (-1 < iVar1) {
    iVar7 = iVar1;
  }
  lVar2 = 0;
  puVar11 = (undefined1 *)0x0;
  uVar12 = 0;
  if (0 < iVar7) {
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar2 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
    if (lVar2 == 0) {
      puVar11 = (undefined1 *)0x0;
      uVar12 = 0;
    }
    else {
      puVar11 = (undefined1 *)(lVar2 + 0x10);
      uVar12 = *(uint *)(lVar2 + 8);
    }
  }
  lVar3 = lVar2;
  if ((int)uVar12 < 1) {
    iVar7 = 1;
    if (1 < (int)(uVar12 * 2)) {
      iVar7 = uVar12 * 2;
    }
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar4 = DAT_143f93980;
    lVar3 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
    if (lVar2 != 0) {
      FUN_141182540(*(undefined8 *)(lVar4 + 8),lVar2);
    }
    if (lVar3 == 0) {
      puVar11 = (undefined1 *)0x0;
      uVar12 = 0;
    }
    else {
      puVar11 = (undefined1 *)(lVar3 + 0x10);
      uVar12 = *(uint *)(lVar3 + 8);
    }
  }
  if (uVar12 == 0) {
                    /* WARNING: Subroutine does not return */
    FUN_140879890();
  }
  *puVar11 = 0xff;
  uVar8 = 1;
  uVar9 = 1;
  lVar2 = lVar3;
  if (iVar1 < 0) {
    if ((int)uVar12 < 3) {
      if (PTR_FUN_143f834a8 != (undefined *)0x0) {
        FUN_141684910();
      }
      lVar4 = DAT_143f93980;
      lVar2 = FUN_1411822c0();
      if (lVar3 != 0) {
        if (lVar2 == 0) {
          puVar5 = (undefined1 *)0x0;
          iVar7 = 0;
        }
        else {
          puVar5 = (undefined1 *)(lVar2 + 0x10);
          iVar7 = *(int *)(lVar2 + 8);
        }
        if (iVar7 == 0) goto LAB_1401480bd;
        *puVar5 = *puVar11;
        FUN_141182540(*(undefined8 *)(lVar4 + 8),lVar3);
      }
      if (lVar2 == 0) {
        puVar11 = (undefined1 *)0x0;
        uVar12 = 0;
      }
      else {
        puVar11 = (undefined1 *)(lVar2 + 0x10);
        uVar12 = *(uint *)(lVar2 + 8);
      }
    }
    if (uVar12 < 3) goto LAB_1401480c3;
    *(undefined2 *)(puVar11 + 1) = 0;
    uVar8 = 3;
    uVar9 = 3;
  }
  lVar3 = lVar2;
  if ((int)uVar12 < (int)(uVar8 + 4)) {
    iVar7 = uVar9 + 4;
    if ((int)(uVar9 + 4) < (int)(uVar12 * 2)) {
      iVar7 = uVar12 * 2;
    }
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar4 = DAT_143f93980;
    lVar3 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
    if (lVar2 != 0) {
      if (uVar12 < uVar9) goto LAB_1401480c3;
      if (lVar3 == 0) {
        puVar5 = (undefined1 *)0x0;
        uVar12 = 0;
      }
      else {
        puVar5 = (undefined1 *)(lVar3 + 0x10);
        uVar12 = *(uint *)(lVar3 + 8);
      }
      if (uVar12 < uVar9) goto LAB_1401480bd;
      FUN_1407afac0(puVar5,puVar11);
      FUN_141182540(*(undefined8 *)(lVar4 + 8),lVar2);
    }
    if (lVar3 == 0) {
      puVar11 = (undefined1 *)0x0;
      uVar12 = 0;
    }
    else {
      puVar11 = (undefined1 *)(lVar3 + 0x10);
      uVar12 = *(uint *)(lVar3 + 8);
    }
  }
  if (uVar8 <= uVar12) {
    if (uVar12 - uVar8 < 4) {
LAB_1401480a8:
                    /* WARNING: Subroutine does not return */
      FUN_1407b1c10(0x28);
    }
    *(undefined4 *)(puVar11 + uVar8) = 0x1000000;
    uVar8 = uVar8 + 4;
    if (uVar9 <= uVar8) {
      uVar9 = uVar8;
    }
    if (iVar1 < 0) {
      uVar8 = 1;
      if (1 < uVar9) {
        uVar8 = uVar9;
      }
      lVar2 = lVar3;
      if ((int)uVar12 < (int)(-uVar12 + 3)) {
        iVar7 = -uVar12 + 2 + uVar8;
        if (iVar7 < (int)(uVar12 * 2)) {
          iVar7 = uVar12 * 2;
        }
        if (PTR_FUN_143f834a8 != (undefined *)0x0) {
          FUN_141684910();
        }
        lVar4 = DAT_143f93980;
        lVar2 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
        if (lVar3 != 0) {
          if (uVar12 < uVar8) goto LAB_1401480c3;
          if (lVar2 == 0) {
            puVar5 = (undefined1 *)0x0;
            uVar12 = 0;
          }
          else {
            puVar5 = (undefined1 *)(lVar2 + 0x10);
            uVar12 = *(uint *)(lVar2 + 8);
          }
          if (uVar12 < uVar8) goto LAB_1401480bd;
          FUN_1407afac0(puVar5,puVar11,uVar8);
          FUN_141182540(*(undefined8 *)(lVar4 + 8),lVar3,0);
        }
        if (lVar2 == 0) {
          puVar11 = (undefined1 *)0x0;
          uVar12 = 0;
        }
        else {
          puVar11 = (undefined1 *)(lVar2 + 0x10);
          uVar12 = *(uint *)(lVar2 + 8);
        }
      }
      lVar4 = lVar2;
      if ((int)uVar12 < 3) {
        iVar7 = uVar8 + 2;
        if ((int)(uVar8 + 2) < (int)(uVar12 * 2)) {
          iVar7 = uVar12 * 2;
        }
        if (PTR_FUN_143f834a8 != (undefined *)0x0) {
          FUN_141684910();
        }
        lVar3 = DAT_143f93980;
        lVar4 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
        if (lVar2 != 0) {
          if (uVar12 < uVar8) goto LAB_1401480c3;
          if (lVar4 == 0) {
            puVar5 = (undefined1 *)0x0;
            uVar12 = 0;
          }
          else {
            puVar5 = (undefined1 *)(lVar4 + 0x10);
            uVar12 = *(uint *)(lVar4 + 8);
          }
          if (uVar12 < uVar8) {
LAB_1401480bd:
                    /* WARNING: Subroutine does not return */
            FUN_1407b1600(puVar5);
          }
          FUN_1407afac0(puVar5,puVar11,uVar8);
          FUN_141182540(*(undefined8 *)(lVar3 + 8),lVar2,0);
        }
        if (lVar4 == 0) {
          puVar11 = (undefined1 *)0x0;
          uVar12 = 0;
        }
        else {
          puVar11 = (undefined1 *)(lVar4 + 0x10);
          uVar12 = *(uint *)(lVar4 + 8);
        }
      }
      if (uVar12 == 0) goto LAB_1401480c3;
      if (uVar12 - 1 < 2) goto LAB_1401480a8;
      *(short *)(puVar11 + 1) = (short)uVar8 << 8;
      uVar10 = 3;
      if (3 < uVar8) {
        uVar10 = uVar8;
      }
    }
    else {
      uVar6 = iVar1 - uVar9;
      lVar4 = lVar3;
      uVar10 = uVar9;
      if (0 < (int)uVar6) {
        if ((int)uVar12 < (int)(uVar8 + uVar6)) {
          iVar7 = uVar6 + uVar9;
          if ((int)(uVar6 + uVar9) < (int)(uVar12 * 2)) {
            iVar7 = uVar12 * 2;
          }
          if (PTR_FUN_143f834a8 != (undefined *)0x0) {
            FUN_141684910();
          }
          lVar2 = DAT_143f93980;
          lVar4 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
          if (lVar3 != 0) {
            if (uVar12 < uVar9) goto LAB_1401480c3;
            if (lVar4 == 0) {
              puVar5 = (undefined1 *)0x0;
              uVar12 = 0;
            }
            else {
              puVar5 = (undefined1 *)(lVar4 + 0x10);
              uVar12 = *(uint *)(lVar4 + 8);
            }
            if (uVar12 < uVar9) goto LAB_1401480bd;
            FUN_1407afac0(puVar5,puVar11,uVar9);
            FUN_141182540(*(undefined8 *)(lVar2 + 8),lVar3,0);
          }
          if (lVar4 == 0) {
            puVar11 = (undefined1 *)0x0;
            uVar12 = 0;
          }
          else {
            puVar11 = (undefined1 *)(lVar4 + 0x10);
            uVar12 = *(uint *)(lVar4 + 8);
          }
        }
        if ((ulonglong)uVar12 < (ulonglong)uVar8 + (ulonglong)uVar6) goto LAB_1401480c3;
        FUN_1414a7780(puVar11 + uVar8,uVar6,0);
        uVar10 = uVar6 + uVar8;
        if ((int)(uVar6 + uVar8) < (int)uVar9) {
          uVar10 = uVar9;
        }
      }
    }
    ClassicUO_Network_NetClient_Send(param_1,lVar4,uVar10);
    if (lVar4 != 0) {
      if (PTR_FUN_143f834a8 != (undefined *)0x0) {
        FUN_141684910();
      }
      FUN_141182540(*(undefined8 *)(DAT_143f93980 + 8),lVar4,0);
    }
    return;
  }
LAB_1401480c3:
                    /* WARNING: Subroutine does not return */
  FUN_1407b15d0();
}
```

### NetClientExt.Send_Seed (0xEF) @ 0x1401474c0
```c
// ==== ClassicUO.Network.NetClientExt.Send_Seed @ 1401474c0 size=1553  [fn] ====

void ClassicUO_Network_NetClientExt_Send_Seed(undefined8 param_1)

{
  int iVar1;
  longlong lVar2;
  longlong lVar3;
  longlong lVar4;
  undefined1 *puVar5;
  uint uVar6;
  int iVar7;
  uint uVar8;
  uint uVar9;
  uint uVar10;
  undefined1 *puVar11;
  uint uVar12;
  
  iVar1 = ClassicUO_Network_PacketsTable_GetPacketLength(0xef);
  iVar7 = 0x40;
  if (-1 < iVar1) {
    iVar7 = iVar1;
  }
  lVar2 = 0;
  puVar11 = (undefined1 *)0x0;
  uVar12 = 0;
  if (0 < iVar7) {
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar2 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
    if (lVar2 == 0) {
      puVar11 = (undefined1 *)0x0;
      uVar12 = 0;
    }
    else {
      puVar11 = (undefined1 *)(lVar2 + 0x10);
      uVar12 = *(uint *)(lVar2 + 8);
    }
  }
  lVar3 = lVar2;
  if ((int)uVar12 < 1) {
    iVar7 = 1;
    if (1 < (int)(uVar12 * 2)) {
      iVar7 = uVar12 * 2;
    }
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar4 = DAT_143f93980;
    lVar3 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
    if (lVar2 != 0) {
      FUN_141182540(*(undefined8 *)(lVar4 + 8),lVar2);
    }
    if (lVar3 == 0) {
      puVar11 = (undefined1 *)0x0;
      uVar12 = 0;
    }
    else {
      puVar11 = (undefined1 *)(lVar3 + 0x10);
      uVar12 = *(uint *)(lVar3 + 8);
    }
  }
  if (uVar12 == 0) {
                    /* WARNING: Subroutine does not return */
    FUN_140879890();
  }
  *puVar11 = 0xef;
  uVar8 = 1;
  uVar9 = 1;
  lVar2 = lVar3;
  if (iVar1 < 0) {
    if ((int)uVar12 < 3) {
      if (PTR_FUN_143f834a8 != (undefined *)0x0) {
        FUN_141684910();
      }
      lVar4 = DAT_143f93980;
      lVar2 = FUN_1411822c0();
      if (lVar3 != 0) {
        if (lVar2 == 0) {
          puVar5 = (undefined1 *)0x0;
          iVar7 = 0;
        }
        else {
          puVar5 = (undefined1 *)(lVar2 + 0x10);
          iVar7 = *(int *)(lVar2 + 8);
        }
        if (iVar7 == 0) goto LAB_140147ab9;
        *puVar5 = *puVar11;
        FUN_141182540(*(undefined8 *)(lVar4 + 8),lVar3);
      }
      if (lVar2 == 0) {
        puVar11 = (undefined1 *)0x0;
        uVar12 = 0;
      }
      else {
        puVar11 = (undefined1 *)(lVar2 + 0x10);
        uVar12 = *(uint *)(lVar2 + 8);
      }
    }
    if (uVar12 < 3) goto LAB_140147abf;
    *(undefined2 *)(puVar11 + 1) = 0;
    uVar8 = 3;
    uVar9 = 3;
  }
  uVar10 = DAT_143f7b308;
  lVar3 = lVar2;
  if ((int)uVar12 < (int)(uVar8 + 4)) {
    iVar7 = uVar9 + 4;
    if ((int)(uVar9 + 4) < (int)(uVar12 * 2)) {
      iVar7 = uVar12 * 2;
    }
    if (PTR_FUN_143f834a8 != (undefined *)0x0) {
      FUN_141684910();
    }
    lVar4 = DAT_143f93980;
    lVar3 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
    if (lVar2 != 0) {
      if (uVar12 < uVar9) goto LAB_140147abf;
      if (lVar3 == 0) {
        puVar5 = (undefined1 *)0x0;
        uVar12 = 0;
      }
      else {
        puVar5 = (undefined1 *)(lVar3 + 0x10);
        uVar12 = *(uint *)(lVar3 + 8);
      }
      if (uVar12 < uVar9) goto LAB_140147ab9;
      FUN_1407afac0(puVar5,puVar11);
      FUN_141182540(*(undefined8 *)(lVar4 + 8),lVar2);
    }
    if (lVar3 == 0) {
      puVar11 = (undefined1 *)0x0;
      uVar12 = 0;
    }
    else {
      puVar11 = (undefined1 *)(lVar3 + 0x10);
      uVar12 = *(uint *)(lVar3 + 8);
    }
  }
  if (uVar8 <= uVar12) {
    if (uVar12 - uVar8 < 4) {
LAB_140147aa4:
                    /* WARNING: Subroutine does not return */
      FUN_1407b1c10(0x28);
    }
    *(uint *)(puVar11 + uVar8) =
         uVar10 >> 0x18 | (uVar10 & 0xff0000) >> 8 | (uVar10 & 0xff00) << 8 | uVar10 << 0x18;
    uVar8 = uVar8 + 4;
    if (uVar9 <= uVar8) {
      uVar9 = uVar8;
    }
    if (iVar1 < 0) {
      uVar8 = 1;
      if (1 < uVar9) {
        uVar8 = uVar9;
      }
      lVar2 = lVar3;
      if ((int)uVar12 < (int)(-uVar12 + 3)) {
        iVar7 = -uVar12 + 2 + uVar8;
        if (iVar7 < (int)(uVar12 * 2)) {
          iVar7 = uVar12 * 2;
        }
        if (PTR_FUN_143f834a8 != (undefined *)0x0) {
          FUN_141684910();
        }
        lVar4 = DAT_143f93980;
        lVar2 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
        if (lVar3 != 0) {
          if (uVar12 < uVar8) goto LAB_140147abf;
          if (lVar2 == 0) {
            puVar5 = (undefined1 *)0x0;
            uVar12 = 0;
          }
          else {
            puVar5 = (undefined1 *)(lVar2 + 0x10);
            uVar12 = *(uint *)(lVar2 + 8);
          }
          if (uVar12 < uVar8) goto LAB_140147ab9;
          FUN_1407afac0(puVar5,puVar11,uVar8);
          FUN_141182540(*(undefined8 *)(lVar4 + 8),lVar3,0);
        }
        if (lVar2 == 0) {
          puVar11 = (undefined1 *)0x0;
          uVar12 = 0;
        }
        else {
          puVar11 = (undefined1 *)(lVar2 + 0x10);
          uVar12 = *(uint *)(lVar2 + 8);
        }
      }
      lVar4 = lVar2;
      if ((int)uVar12 < 3) {
        iVar7 = uVar8 + 2;
        if ((int)(uVar8 + 2) < (int)(uVar12 * 2)) {
          iVar7 = uVar12 * 2;
        }
        if (PTR_FUN_143f834a8 != (undefined *)0x0) {
          FUN_141684910();
        }
        lVar3 = DAT_143f93980;
        lVar4 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
        if (lVar2 != 0) {
          if (uVar12 < uVar8) goto LAB_140147abf;
          if (lVar4 == 0) {
            puVar5 = (undefined1 *)0x0;
            uVar12 = 0;
          }
          else {
            puVar5 = (undefined1 *)(lVar4 + 0x10);
            uVar12 = *(uint *)(lVar4 + 8);
          }
          if (uVar12 < uVar8) {
LAB_140147ab9:
                    /* WARNING: Subroutine does not return */
            FUN_1407b1600(puVar5);
          }
          FUN_1407afac0(puVar5,puVar11,uVar8);
          FUN_141182540(*(undefined8 *)(lVar3 + 8),lVar2,0);
        }
        if (lVar4 == 0) {
          puVar11 = (undefined1 *)0x0;
          uVar12 = 0;
        }
        else {
          puVar11 = (undefined1 *)(lVar4 + 0x10);
          uVar12 = *(uint *)(lVar4 + 8);
        }
      }
      if (uVar12 == 0) goto LAB_140147abf;
      if (uVar12 - 1 < 2) goto LAB_140147aa4;
      *(short *)(puVar11 + 1) = (short)uVar8 << 8;
      uVar10 = 3;
      if (3 < uVar8) {
        uVar10 = uVar8;
      }
    }
    else {
      uVar6 = iVar1 - uVar9;
      lVar4 = lVar3;
      uVar10 = uVar9;
      if (0 < (int)uVar6) {
        if ((int)uVar12 < (int)(uVar8 + uVar6)) {
          iVar7 = uVar6 + uVar9;
          if ((int)(uVar6 + uVar9) < (int)(uVar12 * 2)) {
            iVar7 = uVar12 * 2;
          }
          if (PTR_FUN_143f834a8 != (undefined *)0x0) {
            FUN_141684910();
          }
          lVar2 = DAT_143f93980;
          lVar4 = FUN_1411822c0(*(undefined8 *)(DAT_143f93980 + 8),iVar7);
          if (lVar3 != 0) {
            if (uVar12 < uVar9) goto LAB_140147abf;
            if (lVar4 == 0) {
              puVar5 = (undefined1 *)0x0;
              uVar12 = 0;
            }
            else {
              puVar5 = (undefined1 *)(lVar4 + 0x10);
              uVar12 = *(uint *)(lVar4 + 8);
            }
            if (uVar12 < uVar9) goto LAB_140147ab9;
            FUN_1407afac0(puVar5,puVar11,uVar9);
            FUN_141182540(*(undefined8 *)(lVar2 + 8),lVar3,0);
          }
          if (lVar4 == 0) {
            puVar11 = (undefined1 *)0x0;
            uVar12 = 0;
          }
          else {
            puVar11 = (undefined1 *)(lVar4 + 0x10);
            uVar12 = *(uint *)(lVar4 + 8);
          }
        }
        if ((ulonglong)uVar12 < (ulonglong)uVar8 + (ulonglong)uVar6) goto LAB_140147abf;
        FUN_1414a7780(puVar11 + uVar8,uVar6,0);
        uVar10 = uVar6 + uVar8;
        if ((int)(uVar6 + uVar8) < (int)uVar9) {
          uVar10 = uVar9;
        }
      }
    }
    ClassicUO_Network_NetClient_Send(param_1,lVar4,uVar10);
    if (lVar4 != 0) {
      if (PTR_FUN_143f834a8 != (undefined *)0x0) {
        FUN_141684910();
      }
      FUN_141182540(*(undefined8 *)(DAT_143f93980 + 8),lVar4,0);
    }
    return;
  }
LAB_140147abf:
                    /* WARNING: Subroutine does not return */
  FUN_1407b15d0();
}
```

### Guid.NewGuid (CoCreateGuid) @ 0x14079fe10
```c
// ==== FUN_14079fe10 @ 14079fe10 size=103  [fn] ====

undefined8 * FUN_14079fe10(undefined8 *param_1)

{
  code *pcVar1;
  HRESULT HVar2;
  undefined8 *puVar3;
  undefined1 local_78 [40];
  GUID local_50;
  
  FUN_1416a96b0(local_78);
  HVar2 = CoCreateGuid(&local_50);
  FUN_1416a9700(local_78);
  if (HVar2 == 0) {
    *param_1 = local_50._0_8_;
    *(uchar (*) [8])(param_1 + 1) = local_50.Data4;
    return param_1;
  }
  FUN_14079fe80();
  pcVar1 = (code *)swi(3);
  puVar3 = (undefined8 *)(*pcVar1)();
  return puVar3;
}
```

### Guid.ToString(format) @ 0x14079fcd0
```c
// ==== FUN_14079fcd0 @ 14079fcd0 size=224  [fn] ====

longlong FUN_14079fcd0(undefined8 param_1,longlong param_2)

{
  ushort uVar1;
  longlong lVar2;
  undefined4 uVar3;
  longlong local_48;
  undefined8 uStack_40;
  longlong local_38;
  undefined8 uStack_30;
  undefined1 local_28 [8];
  
  local_48 = 0;
  uStack_40 = 0;
  local_38 = 0;
  uStack_30 = 0;
  if ((param_2 == 0) || (*(int *)(param_2 + 8) == 0)) {
LAB_14079fd3e:
    uVar3 = 0x24;
  }
  else {
    if (*(int *)(param_2 + 8) != 1) {
                    /* WARNING: Subroutine does not return */
      FUN_14079fde0();
    }
    uVar1 = *(ushort *)(param_2 + 0xc) | 0x20;
    if (uVar1 < 0x65) {
      if (uVar1 != 0x62) {
        if (uVar1 != 100) {
LAB_14079fdac:
                    /* WARNING: Subroutine does not return */
          FUN_14079fde0();
        }
        goto LAB_14079fd3e;
      }
    }
    else {
      if (uVar1 == 0x6e) {
        uVar3 = 0x20;
        goto LAB_14079fd43;
      }
      if (uVar1 != 0x70) {
        if (uVar1 != 0x78) goto LAB_14079fdac;
        uVar3 = 0x44;
        goto LAB_14079fd43;
      }
    }
    uVar3 = 0x26;
  }
LAB_14079fd43:
  lVar2 = FUN_1416a7b60(&DAT_1432d14b8,uVar3);
  local_38 = lVar2 + 0xc;
  if (param_2 == 0) {
    local_48 = 0;
    uVar3 = 0;
  }
  else {
    local_48 = param_2 + 0xc;
    uVar3 = *(undefined4 *)(param_2 + 8);
  }
  uStack_30 = CONCAT44(uStack_30._4_4_,*(undefined4 *)(lVar2 + 8));
  uStack_40 = CONCAT44(uStack_40._4_4_,uVar3);
  FUN_1414cea60(param_1,&local_38,local_28,&local_48);
  return lVar2;
}
```

### Environment.GetFolderPath @ 0x14076bfc0
```c
// ==== FUN_14076bfc0 @ 14076bfc0 size=868  [fn] ====

undefined ** FUN_14076bfc0(undefined4 param_1,undefined4 param_2)

{
  int iVar1;
  longlong lVar2;
  longlong lVar3;
  undefined8 uVar4;
  undefined8 uVar5;
  undefined **ppuVar6;
  undefined *local_40;
  undefined8 uStack_38;
  undefined **local_30;
  undefined1 local_28 [16];
  undefined **ppuVar7;
  
  local_40 = (undefined *)0x0;
  uStack_38 = 0;
  local_30 = (undefined **)0x0;
  ppuVar7 = (undefined **)0x0;
  ppuVar6 = (undefined **)0x0;
  switch(param_1) {
  case 0:
  case 0x10:
    local_40 = &DAT_143211e38;
    break;
  default:
    lVar2 = FUN_1416a7aa0(&DAT_1432eb7c0);
    *(undefined4 *)(lVar2 + 8) = param_1;
    lVar3 = FUN_1416a7aa0(&DAT_1432eb7c0);
    *(undefined4 *)(lVar3 + 8) = param_1;
    uVar4 = FUN_1416a7aa0(&DAT_1432d1fe8);
    uVar5 = FUN_1407b0850(&PTR_DAT_143e1b300,lVar3);
    FUN_1407854d0(uVar4,&PTR_DAT_143ee1b50,lVar2,uVar5);
                    /* WARNING: Subroutine does not return */
    FUN_1416a8430(uVar4);
  case 2:
    local_40 = &DAT_143207030;
    break;
  ...
  case 0x1a:
    local_40 = &DAT_143213ef0;
    ppuVar6 = &PTR_DAT_143db1558;
  ...
```

### Directory.Exists @ 0x140826030
```c
// ==== FUN_140826030 @ 140826030 size=93  [fn] ====

bool FUN_140826030(longlong param_1)

{
  int iVar1;
  undefined8 uVar2;
  bool bVar3;
  ulonglong local_30 [2];
  undefined4 local_20;
  undefined4 uStack_1c;
  undefined4 uStack_18;
  undefined4 uStack_14;
  undefined4 uStack_10;
  
  if ((param_1 == 0) || (*(int *)(param_1 + 8) == 0)) {
    bVar3 = false;
  }
  else {
    uVar2 = FUN_14082d270();
    local_30[0] = 0;
    local_30[1] = 0;
    local_20 = 0;
    uStack_1c = 0;
    uStack_18 = 0;
    uStack_14 = 0;
    uStack_10 = 0;
    iVar1 = FUN_140828df0(uVar2,local_30,1);
    if ((iVar1 == 0) && ((int)local_30[0] != -1)) {
      bVar3 = (local_30[0] & 0x10) != 0;
    }
    else {
      bVar3 = false;
    }
  }
  return bVar3;
}
```

### Directory.CreateDirectory @ 0x140825ef0
```c
// ==== FUN_140825ef0 @ 140825ef0 size=115  [fn] ====

longlong FUN_140825ef0(longlong param_1)

{
  undefined8 uVar1;
  longlong lVar2;
  
  if ((param_1 != 0) && (*(int *)(param_1 + 8) != 0)) {
    uVar1 = FUN_14082d270(param_1);
    FUN_1408290c0(uVar1,0);
    lVar2 = FUN_1416a7aa0(&DAT_1432dd728);
    *(undefined4 *)(lVar2 + 0x28) = 0xffffffff;
    FUN_1408265a0(lVar2,param_1,uVar1,0,1);
    return lVar2;
  }
                    /* WARNING: Subroutine does not return */
  FUN_140785280(param_1,&PTR_DAT_143ef76f8);
}
```

### File.Exists @ 0x140826e20
```c
// ==== FUN_140826e20 @ 140826e20 size=68  [fn] ====

undefined8 FUN_140826e20(longlong param_1)

{
  short sVar1;
  longlong lVar2;
  undefined8 uVar3;
  
  if ((param_1 != 0) && (*(int *)(param_1 + 8) != 0)) {
    lVar2 = FUN_14082d270();
    if ((*(int *)(lVar2 + 8) < 1) ||
       ((sVar1 = *(short *)(lVar2 + 0xc + (ulonglong)(*(int *)(lVar2 + 8) - 1) * 2), sVar1 != 0x5c
        && (sVar1 != 0x2f)))) {
      uVar3 = FUN_140828da0();
      return uVar3;
    }
  }
  return 0;
}
```

### File.ReadAllText(path,enc) @ 0x140827050
```c
// ==== FUN_140827050 @ 140827050 size=140  [fn] ====

undefined8 FUN_140827050(undefined8 param_1,undefined8 param_2)

{
  undefined8 uVar1;
  undefined8 uVar2;
  
  FUN_140827670(param_1,param_2);
  uVar1 = FUN_1416a7aa0(&DAT_1432de2d8);
  uVar2 = FUN_14082ef50(param_1,0x400);
  FUN_14082eda0(uVar1,uVar2,param_2,1,0x400,0);
  uVar2 = FUN_14082f3c0(uVar1);
  FUN_14082efe0(uVar1,1);
  FUN_14076d450(uVar1);
  return uVar2;
}
```

### File.Delete @ 0x140826df0
```c
// ==== FUN_140826df0 @ 140826df0 size=38  [fn] ====

void FUN_140826df0(longlong param_1)

{
  undefined8 uVar1;
  
  if (param_1 != 0) {
    uVar1 = FUN_14082d270();
    FUN_140829700(uVar1);
    return;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407853c0(&PTR_DAT_143ef76f8);
}
```

### File.WriteAllText(path,text,enc) @ 0x140827100
```c
// ==== FUN_140827100 @ 140827100 size=95  [fn] ====

void FUN_140827100(undefined8 param_1,longlong param_2,undefined8 param_3)

{
  longlong local_28;
  undefined4 local_20;
  
  local_28 = 0;
  FUN_140827670(param_1,param_3);
  if (param_2 == 0) {
    local_28 = 0;
    local_20 = 0;
  }
  else {
    local_28 = param_2 + 0xc;
    local_20 = *(undefined4 *)(param_2 + 8);
  }
  FUN_1408278e0(param_1,2,&local_28,param_3);
  return;
}
```

### UTF8Encoding(no BOM) singleton @ 0x140826cb0
```c
// ==== FUN_140826cb0 @ 140826cb0 size=91  [fn] ====

longlong FUN_140826cb0(void)

{
  longlong lVar1;
  
  lVar1 = *(longlong *)(DAT_143f8d7f8 + 8);
  if (lVar1 == 0) {
    lVar1 = FUN_1416a7aa0(&DAT_1432dd1c8);
    FUN_140813930(lVar1,0xfde9);
    *(undefined2 *)(lVar1 + 0x25) = 0x100;
    FUN_14081e5b0(lVar1);
    FUN_1416a80d0(DAT_143f8d7f8 + 8,lVar1);
  }
  return lVar1;
}
```

### Encoding.GetBytes @ 0x1408143b0
```c
// ==== FUN_1408143b0 @ 1408143b0 size=105  [fn] ====

undefined8 FUN_1408143b0(longlong *param_1,longlong param_2)

{
  longlong lVar1;
  int iVar2;
  undefined8 uVar3;
  
  if (param_2 != 0) {
    lVar1 = *param_1;
    iVar2 = (**(code **)(lVar1 + 0x78))(param_1,param_2);
    uVar3 = FUN_1416a7bc0(&DAT_143849058,(longlong)iVar2);
    (**(code **)(lVar1 + 0xb8))(param_1,param_2,0,*(undefined4 *)(param_2 + 8),uVar3,0);
    return uVar3;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407853c0(&PTR_DAT_143efbc70);
}
```

### ProtectedData.Protect @ 0x140a33900
```c
// ==== FUN_140a33900 @ 140a33900 size=204  [fn] ====

undefined8 FUN_140a33900(longlong param_1,longlong param_2,undefined4 param_3)

{
  undefined4 uVar1;
  undefined8 local_60;
  ulonglong local_58;
  longlong lStack_50;
  undefined8 local_48;
  longlong lStack_40;
  undefined8 local_38;
  undefined8 uStack_30;
  undefined8 local_28;
  
  local_60 = 0;
  local_58 = 0;
  lStack_50 = 0;
  local_48 = 0;
  lStack_40 = 0;
  local_38 = 0;
  uStack_30 = 0;
  local_28 = 0;
  FUN_140a33ef0();
  if (param_1 != 0) {
    lStack_40 = param_1 + 0x10;
    if (param_2 == 0) {
      lStack_50 = 0;
      uVar1 = 0;
    }
    else {
      lStack_50 = param_2 + 0x10;
      uVar1 = *(undefined4 *)(param_2 + 8);
    }
    local_38 = CONCAT44(local_38._4_4_,*(undefined4 *)(param_1 + 8));
    local_48 = CONCAT44(local_48._4_4_,uVar1);
    local_60 = 0;
    local_58 = local_58 & 0xffffffff00000000;
    FUN_140a33aa0(&lStack_40,&lStack_50,param_3,1,&uStack_30,&local_28,1,&local_60);
    return local_28;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407853c0(&PTR_DAT_143f08de0);
}
```

### ProtectedData.Unprotect @ 0x140a339d0
```c
// ==== FUN_140a339d0 @ 140a339d0 size=197  [fn] ====

undefined8 FUN_140a339d0(longlong param_1,longlong param_2,undefined4 param_3)

{
  undefined4 uVar1;
  undefined8 local_60;
  ulonglong local_58;
  longlong lStack_50;
  undefined8 local_48;
  longlong lStack_40;
  undefined8 local_38;
  undefined8 uStack_30;
  undefined8 local_28;
  
  local_60 = 0;
  local_58 = 0;
  lStack_50 = 0;
  local_48 = 0;
  lStack_40 = 0;
  local_38 = 0;
  uStack_30 = 0;
  local_28 = 0;
  FUN_140a33ef0();
  if (param_1 != 0) {
    lStack_40 = param_1 + 0x10;
    if (param_2 == 0) {
      lStack_50 = 0;
      uVar1 = 0;
    }
    else {
      lStack_50 = param_2 + 0x10;
      uVar1 = *(undefined4 *)(param_2 + 8);
    }
    local_38 = CONCAT44(local_38._4_4_,*(undefined4 *)(param_1 + 8));
    local_48 = CONCAT44(local_48._4_4_,uVar1);
    local_60 = 0;
    local_58 = local_58 & 0xffffffff00000000;
    FUN_140a33aa0(&lStack_40,&lStack_50,param_3,0,&uStack_30,&local_28,1,&local_60);
    return local_28;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407853c0(&PTR_DAT_143ede5e0);
}
```

### ProtectedData core (CryptProtectData/CryptUnprotectData) @ 0x140a33aa0
```c
// ==== FUN_140a33aa0 @ 140a33aa0 size=1039  [fn] ====

/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

undefined4
FUN_140a33aa0(undefined8 *param_1,undefined8 *param_2,int param_3,char param_4,uint *param_5,
             undefined8 *param_6,char param_7,undefined8 *param_8)

{
  uint uVar1;
  int iVar2;
  DWORD DVar3;
  undefined **ppuVar4;
  longlong lVar5;
  undefined8 uVar6;
  longlong *plVar7;
  BYTE *pBVar8;
  uint uVar9;
  undefined4 uVar10;
  CRYPTPROTECT_PROMPTSTRUCT *pCVar11;
  DATA_BLOB *pDVar12;
  undefined1 local_a8 [40];
  DATA_BLOB local_80;
  DATA_BLOB DStack_70;
  DATA_BLOB DStack_60;
  BYTE *pBStack_50;
  BYTE *local_48;
  
  uVar1 = *(uint *)(param_2 + 1);
  pBVar8 = (BYTE *)*param_1;
  DStack_60.cbData = *(DWORD *)(param_1 + 1);
  uVar9 = DStack_60.cbData;
  if (DStack_60.cbData == 0) {
    pBVar8 = &DAT_143f59cf0;
    uVar9 = 1;
  }
  DStack_60.pbData = (BYTE *)0x0;
  if (uVar9 != 0) {
    DStack_60.pbData = pBVar8;
  }
  pBStack_50 = (BYTE *)0x0;
  if (uVar1 != 0) {
    pBStack_50 = (BYTE *)*param_2;
  }
  DStack_60._4_4_ = 0;
  DStack_70._0_8_ = 0;
  DStack_70.pbData = (BYTE *)0x0;
  if (uVar1 != 0) {
    DStack_70._4_4_ = 0;
    DStack_70.cbData = uVar1;
    DStack_70.pbData = pBStack_50;
  }
  DVar3 = 1;
  if (param_3 == 1) {
    DVar3 = 5;
  }
  local_80._0_8_ = 0;
  local_80.pbData = (BYTE *)0x0;
  local_48 = DStack_60.pbData;
  if (param_4 == '\0') {
    SetLastError(0);
    pCVar11 = (CRYPTPROTECT_PROMPTSTRUCT *)0x0;
    pDVar12 = &local_80;
    FUN_1416a96b0(local_a8);
    iVar2 = CryptUnprotectData(&DStack_60,(LPWSTR *)0x0,&DStack_70,(PVOID)0x0,pCVar11,DVar3,pDVar12)
    ;
    FUN_1416a9700(local_a8);
    DVar3 = GetLastError();
    plVar7 = (longlong *)
             (*(longlong *)((longlong)ThreadLocalStoragePointer + (ulonglong)__tls_index * 8) + 0x10
             );
    lVar5 = *plVar7;
    if (lVar5 == 0) {
      lVar5 = FUN_140875b90(plVar7);
    }
    *(DWORD *)(lVar5 + 0x110) = DVar3;
  }
  else {
    SetLastError(0);
    pCVar11 = (CRYPTPROTECT_PROMPTSTRUCT *)0x0;
    pDVar12 = &local_80;
    FUN_1416a96b0(local_a8);
    iVar2 = CryptProtectData(&DStack_60,(LPCWSTR)0x0,&DStack_70,(PVOID)0x0,pCVar11,DVar3,pDVar12);
    FUN_1416a9700(local_a8);
    DVar3 = GetLastError();
    plVar7 = (longlong *)
             (*(longlong *)((longlong)ThreadLocalStoragePointer + (ulonglong)__tls_index * 8) + 0x10
             );
    lVar5 = *plVar7;
    if (lVar5 == 0) {
      lVar5 = FUN_140875b90(plVar7);
    }
    *(DWORD *)(lVar5 + 0x110) = DVar3;
  }
  pBVar8 = local_80.pbData;
  uVar6 = local_80._0_8_;
  if (iVar2 == 0) {
    plVar7 = (longlong *)
             (*(longlong *)((longlong)ThreadLocalStoragePointer + (ulonglong)__tls_index * 8) + 0x10
             );
    lVar5 = *plVar7;
    if (lVar5 == 0) {
      lVar5 = FUN_140875b90(plVar7);
    }
    if ((param_4 != '\0') &&
       ((*(int *)(lVar5 + 0x110) == -0x7ff8fffe || (*(int *)(lVar5 + 0x110) == 2)))) {
      uVar6 = FUN_1416a7aa0(&DAT_1432d7370);
      FUN_1407be060(uVar6,&PTR_DAT_143e888b0);
                    /* WARNING: Subroutine does not return */
      FUN_1416a8430(uVar6);
    }
    uVar6 = FUN_140a33890();
                    /* WARNING: Subroutine does not return */
    FUN_1416a8430(uVar6);
  }
  if (local_80.pbData == (BYTE *)0x0) {
    uVar6 = FUN_1416a7aa0(&DAT_1432d5228);
    FUN_1407aacb0(uVar6);
                    /* WARNING: Subroutine does not return */
    FUN_1416a8430(uVar6);
  }
  DVar3 = local_80.cbData;
  if ((int)local_80.cbData < 0) {
                    /* WARNING: Subroutine does not return */
    FUN_1407b15d0();
  }
  if (param_7 == '\0') {
    if (*(uint *)(param_8 + 1) < local_80.cbData) {
      *param_5 = 0;
      *param_6 = 0;
      uVar10 = 0;
      goto LAB_140a33e78;
    }
    FUN_1407afac0(*param_8,local_80.pbData,local_80._0_8_ & 0xffffffff);
    *param_5 = DVar3;
    *param_6 = 0;
  }
  else {
    if (local_80.cbData == 0) {
      ppuVar4 = &PTR_DAT_143f456d8;
    }
    else {
      ppuVar4 = (undefined **)FUN_1416a7bc0(&DAT_143849058,local_80._0_8_ & 0xffffffff);
      FUN_1407afac0(ppuVar4 + 2,pBVar8,uVar6 & 0xffffffff);
    }
    FUN_1416a8140(param_6,ppuVar4);
    *param_5 = DVar3;
  }
  uVar10 = 1;
LAB_140a33e78:
  if ((local_80.pbData != (BYTE *)0x0) &&
     (FUN_1407afc20(pBVar8,uVar6 & 0xffffffff),
     ((ulonglong)local_80.pbData & 0xffffffffffff0000) != 0)) {
    FUN_14087b700();
  }
  return uVar10;
}
```

### Convert.ToBase64String @ 0x140788f20
```c
// ==== FUN_140788f20 @ 140788f20 size=62  [fn] ====

void FUN_140788f20(longlong param_1)

{
  longlong local_10;
  undefined4 local_8;
  
  local_10 = 0;
  if (param_1 != 0) {
    local_10 = param_1 + 0x10;
    local_8 = *(undefined4 *)(param_1 + 8);
    FUN_140788f60(&local_10,0);
    return;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407853c0(&PTR_DAT_143ee9960);
}
```

### Convert.FromBase64String @ 0x1407896d0
```c
// ==== FUN_1407896d0 @ 1407896d0 size=57  [fn] ====

void FUN_1407896d0(longlong param_1)

{
  if (param_1 != 0) {
    FUN_140789ae0(param_1 + 0xc,*(undefined4 *)(param_1 + 8));
    return;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407b1b90(0x11);
}
```

### HMACSHA256..ctor(key) @ 0x140a23450
```c
// ==== FUN_140a23450 @ 140a23450 size=225  [fn] ====

void FUN_140a23450(longlong param_1,longlong param_2)

{
  longlong lVar1;
  undefined8 uVar2;
  longlong local_28;
  undefined4 local_20;
  
  local_28 = 0;
  *(undefined4 *)(param_1 + 0x30) = 0x40;
  if (param_2 != 0) {
    FUN_140a22fa0(param_1,&PTR_DAT_143e61200);
    lVar1 = FUN_1416a7aa0(&DAT_14331f178);
    local_28 = param_2 + 0x10;
    local_20 = *(undefined4 *)(param_2 + 8);
    *(undefined ***)(lVar1 + 0x10) = &PTR_DAT_143e61200;
    *(undefined4 *)(lVar1 + 0x20) = 0x40;
    uVar2 = FUN_140a23010(lVar1,&local_28);
    FUN_1416a80d0(lVar1 + 8,uVar2);
    if (*(longlong *)(lVar1 + 8) == 0) {
      FUN_1416a80d0(lVar1 + 8,param_2);
    }
    FUN_1416a80d0(param_1 + 0x38,lVar1);
    uVar2 = FUN_140a20970(*(undefined8 *)(*(longlong *)(param_1 + 0x38) + 8));
    FUN_1416a80d0(param_1 + 0x20,uVar2);
    *(undefined4 *)(param_1 + 0x30) = 0x40;
    *(int *)(param_1 + 0x10) =
         *(int *)(*(longlong *)(*(longlong *)(param_1 + 0x38) + 0x18) + 0x20) << 3;
    return;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407853c0(&PTR_DAT_143eebf68);
}
```

### HashAlgorithm.ComputeHash @ 0x140a22c90
```c
// ==== FUN_140a22c90 @ 140a22c90 size=68  [fn] ====

void FUN_140a22c90(longlong *param_1,longlong param_2)

{
  if ((char)param_1[3] != '\0') {
                    /* WARNING: Subroutine does not return */
    FUN_1407b1ef0(param_1);
  }
  if (param_2 != 0) {
    (**(code **)(*param_1 + 0x38))(param_1,param_2,0,*(undefined4 *)(param_2 + 8));
    FUN_140a22dd0(param_1);
    return;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407853c0(&PTR_DAT_143ed2098);
}
```

### Convert.ToHexString(bytes,offset,length) @ 0x140789c90
```c
// ==== FUN_140789c90 @ 140789c90 size=155  [fn] ====

void FUN_140789c90(longlong param_1,uint param_2,uint param_3)

{
  int iVar1;
  longlong local_10;
  uint local_8;
  
  local_10 = 0;
  if (param_1 == 0) {
                    /* WARNING: Subroutine does not return */
    FUN_1407853c0(&PTR_DAT_143ee9960);
  }
  if ((int)param_3 < 0) {
                    /* WARNING: Subroutine does not return */
    FUN_1414ef9d0(param_3,&PTR_DAT_143eed388);
  }
  if ((int)param_2 < 0) {
                    /* WARNING: Subroutine does not return */
    FUN_1414ef9d0(param_2,&PTR_DAT_143ef4890);
  }
  iVar1 = *(uint *)(param_1 + 8) - param_3;
  if ((iVar1 <= (int)param_2) && (iVar1 < (int)param_2)) {
                    /* WARNING: Subroutine does not return */
    FUN_1414efab0(param_2,iVar1,&PTR_DAT_143ef4890);
  }
  if ((ulonglong)param_3 + (ulonglong)param_2 <= (ulonglong)*(uint *)(param_1 + 8)) {
    local_10 = param_1 + 0x10 + (ulonglong)param_2;
    local_8 = param_3;
    FUN_140789d30(&local_10);
    return;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407b15d0();
}
```

### Version field count @ 0x1407bce00
```c
// ==== FUN_1407bce00 @ 1407bce00 size=30  [fn] ====

undefined8 FUN_1407bce00(longlong param_1)

{
  undefined8 uVar1;
  
  if (*(int *)(param_1 + 0x10) == -1) {
    return 2;
  }
  uVar1 = 3;
  if (*(int *)(param_1 + 0x14) != -1) {
    uVar1 = 4;
  }
  return uVar1;
}
```

### Version.ToString(fieldCount) @ 0x1407bcce0
```c
// ==== FUN_1407bcce0 @ 1407bcce0 size=130  [fn] ====

void FUN_1407bcce0(undefined8 param_1,undefined4 param_2)

{
  undefined1 local_a8 [104];
  longlong local_40;
  undefined1 *local_38;
  undefined8 uStack_30;
  undefined1 *local_28;
  undefined8 uStack_20;
  uint local_18 [2];
  
  local_28 = (undefined1 *)0x0;
  uStack_20 = 0;
  local_40 = DAT_143ffb000;
  uStack_30 = 0x2f;
  local_38 = local_a8;
  FUN_1414d6260(param_1,&local_38,param_2,local_18);
  if (local_18[0] < 0x30) {
    uStack_20 = CONCAT44(uStack_20._4_4_,local_18[0]);
    local_28 = local_a8;
    FUN_1411938a0(&local_28);
    if (local_40 != DAT_143ffb000) {
      FUN_1416a7800();
    }
    return;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407b15d0();
}
```

### HttpHeaders.GetHeaderDescriptor @ 0x14071eca0
```c
// ==== FUN_14071eca0 @ 14071eca0 size=325  [fn] ====

longlong * FUN_14071eca0(longlong param_1,longlong param_2)

{
  byte bVar1;
  int iVar2;
  undefined8 uVar3;
  undefined8 uVar4;
  longlong *local_18;
  
  local_18 = (longlong *)0x0;
  if ((param_2 == 0) || (*(int *)(param_2 + 8) == 0)) {
                    /* WARNING: Subroutine does not return */
    FUN_140785280(param_2,&PTR_DAT_143ef1e38);
  }
  iVar2 = FUN_14071b6d0(param_2,&local_18);
  if (iVar2 == 0) {
    uVar3 = FUN_1416a7aa0(&DAT_1432d37e8);
    uVar4 = FUN_1406cd110(&PTR_DAT_143e8c300,param_2);
    FUN_140794f90(uVar3,uVar4);
                    /* WARNING: Subroutine does not return */
    FUN_1416a8430(uVar3);
  }
  if ((local_18 == (longlong *)0x0) || ((undefined *)*local_18 != &DAT_1432c9628)) {
    bVar1 = 0x10;
  }
  else {
    bVar1 = *(byte *)(local_18 + 7);
  }
  if ((*(byte *)(param_1 + 0x14) & bVar1) != 0) {
    return local_18;
  }
  if ((local_18 == (longlong *)0x0) || ((undefined *)*local_18 != &DAT_1432c9628)) {
    bVar1 = 0x10;
  }
  else {
    bVar1 = *(byte *)(local_18 + 7);
  }
  if ((*(byte *)(param_1 + 0x15) & bVar1) == 0) {
    uVar3 = FUN_1416a7aa0(&DAT_1432d4838);
    uVar4 = FUN_1406cd110(&PTR_DAT_143e3bc58,param_2);
    FUN_1407a3710(uVar3,uVar4);
                    /* WARNING: Subroutine does not return */
    FUN_1416a8430(uVar3);
  }
  if ((local_18 == (longlong *)0x0) || ((undefined *)*local_18 != &DAT_1432c9628)) {
    if ((local_18 == (longlong *)0x0) || ((undefined *)*local_18 != &DAT_1432d14b8)) {
      local_18 = (longlong *)0x0;
    }
  }
  else {
    local_18 = (longlong *)local_18[1];
  }
  return local_18;
}
```

### HttpHeaders.Add @ 0x14071d370
```c
// ==== FUN_14071d370 @ 14071d370 size=234  [fn] ====

void FUN_14071d370(undefined8 param_1,undefined8 *param_2,undefined8 param_3)

{
  int iVar1;
  undefined8 *puVar2;
  longlong local_38;
  undefined8 *puStack_30;
  longlong local_28;
  
  local_38 = 0;
  puStack_30 = (undefined8 *)0x0;
  local_28 = 0;
  if ((param_2 == (undefined8 *)0x0) || (puVar2 = param_2, (undefined *)*param_2 != &DAT_1432c9628))
  {
    puVar2 = (undefined8 *)0x0;
  }
  if ((puVar2 == (undefined8 *)0x0) || (puVar2[2] == 0)) {
    FUN_14071e9b0(param_1,param_2);
    FUN_14071ee80(param_3);
    FUN_14071d460(param_1,param_2,param_3);
  }
  else {
    FUN_14071e9b0(param_1,param_2);
    iVar1 = FUN_14071e330(param_1,param_2,&local_28);
    if (iVar1 == 0) {
      local_28 = FUN_1416a7aa0(&DAT_1432c9f20);
    }
    FUN_14071ea40(param_2,local_28,param_3);
    if ((iVar1 == 0) && (*(longlong *)(local_28 + 0x10) != 0)) {
      local_38 = local_28;
      puStack_30 = param_2;
      FUN_14071f580(param_1,&local_38);
    }
  }
  return;
}
```

### JsonSerializerOptions..ctor(JsonSerializerDefaults) @ 0x140a63590
```c
// ==== FUN_140a63590 @ 140a63590 size=130  [fn] ====

void FUN_140a63590(longlong param_1,int param_2)

{
  undefined8 uVar1;
  
  FUN_140a632c0(param_1);
  if (param_2 == 1) {
    *(undefined1 *)(param_1 + 0x9b) = 1;
    *(undefined ***)(param_1 + 0x30) = &PTR_DAT_143f59b90;
    *(undefined4 *)(param_1 + 0x6c) = 1;
  }
  else if (param_2 == 2) {
    *(undefined4 *)(param_1 + 0x78) = 1;
    *(undefined1 *)(param_1 + 0x9d) = 0;
    *(undefined2 *)(param_1 + 0x95) = 0x101;
  }
  else if (param_2 != 0) {
    uVar1 = FUN_1416a7aa0(&DAT_1432d1fe8);
    FUN_140785410(uVar1,&PTR_DAT_143ed96c0);
                    /* WARNING: Subroutine does not return */
    FUN_1416a8430(uVar1);
  }
  return;
}
```

### VerifyDeviceModelPropInit @ 0x1404e6d50
```c
// ==== FUN_1404e6d50 @ 1404e6d50 size=747  [fn] ====

longlong FUN_1404e6d50(undefined8 param_1)

{
  longlong lVar1;
  longlong lVar2;
  longlong lVar3;
  longlong lVar4;
  undefined8 uVar5;
  
  lVar2 = FUN_1416a7c30(&DAT_143853c88,2);
  lVar3 = FUN_1416a7aa0(&DAT_143570450);
  *(undefined2 *)(lVar3 + 0x48) = 0x101;
  *(undefined1 *)(lVar3 + 0x4a) = 0;
  *(undefined ***)(lVar3 + 8) = &PTR_DAT_143f1aba0;
  *(undefined8 *)(lVar3 + 0x18) = 0;
  lVar1 = DAT_143f8cba0;
  lVar4 = *(longlong *)(DAT_143f8cba0 + 0x20);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1435636b0);
    FUN_14168e664(lVar4,&PTR_DAT_143f60ff8);
    FUN_1416a80d0(lVar1 + 0x20,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x20,lVar4);
  lVar4 = *(longlong *)(lVar1 + 0x28);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_143563638);
    FUN_14168e670(lVar4,&PTR_DAT_143f60ff8);
    FUN_1416a80d0(lVar1 + 0x28,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x28,lVar4);
  *(undefined8 *)(lVar3 + 0x50) = 0;
  *(undefined2 *)(lVar3 + 0x4b) = 0;
  *(undefined8 *)(lVar3 + 0x58) = 0;
  *(undefined ***)(lVar3 + 0x30) = &PTR_DAT_143e19eb0;
  *(undefined8 *)(lVar3 + 0x38) = 0;
  lVar4 = *(longlong *)(lVar1 + 0x30);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1436204a0);
    FUN_14168e67c(lVar4,&PTR_DAT_143f60ff8);
    FUN_1416a80d0(lVar1 + 0x30,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x40,lVar4);
  uVar5 = FUN_1415786f0(&PTR_PTR_143195348,param_1,lVar3);
  FUN_140841a80(lVar2,0,uVar5);
  lVar4 = *(longlong *)(lVar2 + 0x10);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa3) = 0;
  lVar4 = *(longlong *)(lVar2 + 0x10);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa4) = 0;
  lVar3 = FUN_1416a7aa0(&DAT_143570450);
  *(undefined2 *)(lVar3 + 0x48) = 0x101;
  *(undefined1 *)(lVar3 + 0x4a) = 0;
  *(undefined ***)(lVar3 + 8) = &PTR_DAT_143f1aba0;
  *(undefined8 *)(lVar3 + 0x18) = 0;
  lVar4 = *(longlong *)(lVar1 + 0x38);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1435636b0);
    FUN_14168e688(lVar4,&PTR_DAT_143f60ff8);
    FUN_1416a80d0(lVar1 + 0x38,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x20,lVar4);
  lVar4 = *(longlong *)(lVar1 + 0x40);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_143563638);
    FUN_14168e694(lVar4,&PTR_DAT_143f60ff8);
    FUN_1416a80d0(lVar1 + 0x40,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x28,lVar4);
  *(undefined8 *)(lVar3 + 0x50) = 0;
  *(undefined2 *)(lVar3 + 0x4b) = 0;
  *(undefined8 *)(lVar3 + 0x58) = 0;
  *(undefined ***)(lVar3 + 0x30) = &PTR_DAT_143dd1e00;
  *(undefined8 *)(lVar3 + 0x38) = 0;
  lVar4 = *(longlong *)(lVar1 + 0x48);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1436204a0);
    FUN_14168e6a0(lVar4,&PTR_DAT_143f60ff8);
    FUN_1416a80d0(lVar1 + 0x48,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x40,lVar4);
  uVar5 = FUN_1415786f0(&PTR_PTR_143195348,param_1,lVar3);
  FUN_140841a80(lVar2,1,uVar5);
  lVar4 = *(longlong *)(lVar2 + 0x18);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa3) = 0;
  lVar4 = *(longlong *)(lVar2 + 0x18);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa4) = 0;
  return lVar2;
}
```

### GameLoginRequestModelPropInit @ 0x1404e9cb0
```c
// ==== FUN_1404e9cb0 @ 1404e9cb0 size=399  [fn] ====

longlong FUN_1404e9cb0(undefined8 param_1)

{
  longlong lVar1;
  longlong lVar2;
  longlong lVar3;
  longlong lVar4;
  undefined8 uVar5;
  
  lVar2 = FUN_1416a7c30(&DAT_143853c88,1);
  lVar3 = FUN_1416a7aa0(&DAT_143570450);
  *(undefined2 *)(lVar3 + 0x48) = 0x101;
  *(undefined1 *)(lVar3 + 0x4a) = 0;
  *(undefined ***)(lVar3 + 8) = &PTR_DAT_143f1ac20;
  *(undefined8 *)(lVar3 + 0x18) = 0;
  lVar1 = DAT_143f8cbc0;
  lVar4 = *(longlong *)(DAT_143f8cbc0 + 0x20);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1435636b0);
    FUN_14168e814(lVar4,&PTR_DAT_143f60f98);
    FUN_1416a80d0(lVar1 + 0x20,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x20,lVar4);
  lVar4 = *(longlong *)(lVar1 + 0x28);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_143563638);
    FUN_14168e820(lVar4,&PTR_DAT_143f60f98);
    FUN_1416a80d0(lVar1 + 0x28,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x28,lVar4);
  *(undefined8 *)(lVar3 + 0x50) = 0;
  *(undefined2 *)(lVar3 + 0x4b) = 0;
  *(undefined8 *)(lVar3 + 0x58) = 0;
  *(undefined ***)(lVar3 + 0x30) = &PTR_DAT_143db2fd8;
  *(undefined8 *)(lVar3 + 0x38) = 0;
  lVar4 = *(longlong *)(lVar1 + 0x30);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1436204a0);
    FUN_14168e82c(lVar4,&PTR_DAT_143f60f98);
    FUN_1416a80d0(lVar1 + 0x30,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x40,lVar4);
  uVar5 = FUN_1415786f0(&PTR_PTR_143195348,param_1,lVar3);
  FUN_140841a80(lVar2,0,uVar5);
  lVar4 = *(longlong *)(lVar2 + 0x10);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa3) = 0;
  lVar4 = *(longlong *)(lVar2 + 0x10);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa4) = 0;
  return lVar2;
}
```

### ClientLoginRequestModelPropInit @ 0x1404e8510
```c
// ==== FUN_1404e8510 @ 1404e8510 size=747  [fn] ====

longlong FUN_1404e8510(undefined8 param_1)

{
  longlong lVar1;
  longlong lVar2;
  longlong lVar3;
  longlong lVar4;
  undefined8 uVar5;
  
  lVar2 = FUN_1416a7c30(&DAT_143853c88,2);
  lVar3 = FUN_1416a7aa0(&DAT_143570450);
  *(undefined2 *)(lVar3 + 0x48) = 0x101;
  *(undefined1 *)(lVar3 + 0x4a) = 0;
  *(undefined ***)(lVar3 + 8) = &PTR_DAT_143f1abe0;
  *(undefined8 *)(lVar3 + 0x18) = 0;
  lVar1 = DAT_143f8cbb0;
  lVar4 = *(longlong *)(DAT_143f8cbb0 + 0x20);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1435636b0);
    FUN_14168e73c(lVar4,&PTR_DAT_143f60fc8);
    FUN_1416a80d0(lVar1 + 0x20,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x20,lVar4);
  lVar4 = *(longlong *)(lVar1 + 0x28);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_143563638);
    FUN_14168e748(lVar4,&PTR_DAT_143f60fc8);
    FUN_1416a80d0(lVar1 + 0x28,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x28,lVar4);
  *(undefined8 *)(lVar3 + 0x50) = 0;
  *(undefined2 *)(lVar3 + 0x4b) = 0;
  *(undefined8 *)(lVar3 + 0x58) = 0;
  *(undefined ***)(lVar3 + 0x30) = &PTR_DAT_143dfe4d8;
  *(undefined8 *)(lVar3 + 0x38) = 0;
  lVar4 = *(longlong *)(lVar1 + 0x30);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1436204a0);
    FUN_14168e754(lVar4,&PTR_DAT_143f60fc8);
    FUN_1416a80d0(lVar1 + 0x30,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x40,lVar4);
  uVar5 = FUN_1415786f0(&PTR_PTR_143195348,param_1,lVar3);
  FUN_140841a80(lVar2,0,uVar5);
  lVar4 = *(longlong *)(lVar2 + 0x10);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa3) = 0;
  lVar4 = *(longlong *)(lVar2 + 0x10);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa4) = 0;
  lVar3 = FUN_1416a7aa0(&DAT_143570450);
  *(undefined2 *)(lVar3 + 0x48) = 0x101;
  *(undefined1 *)(lVar3 + 0x4a) = 0;
  *(undefined ***)(lVar3 + 8) = &PTR_DAT_143f1abe0;
  *(undefined8 *)(lVar3 + 0x18) = 0;
  lVar4 = *(longlong *)(lVar1 + 0x38);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1435636b0);
    FUN_14168e760(lVar4,&PTR_DAT_143f60fc8);
    FUN_1416a80d0(lVar1 + 0x38,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x20,lVar4);
  lVar4 = *(longlong *)(lVar1 + 0x40);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_143563638);
    FUN_14168e76c(lVar4,&PTR_DAT_143f60fc8);
    FUN_1416a80d0(lVar1 + 0x40,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x28,lVar4);
  *(undefined8 *)(lVar3 + 0x50) = 0;
  *(undefined2 *)(lVar3 + 0x4b) = 0;
  *(undefined8 *)(lVar3 + 0x58) = 0;
  *(undefined ***)(lVar3 + 0x30) = &PTR_DAT_143e4e900;
  *(undefined8 *)(lVar3 + 0x38) = 0;
  lVar4 = *(longlong *)(lVar1 + 0x48);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1436204a0);
    FUN_14168e778(lVar4,&PTR_DAT_143f60fc8);
    FUN_1416a80d0(lVar1 + 0x48,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x40,lVar4);
  uVar5 = FUN_1415786f0(&PTR_PTR_143195348,param_1,lVar3);
  FUN_140841a80(lVar2,1,uVar5);
  lVar4 = *(longlong *)(lVar2 + 0x18);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa3) = 0;
  lVar4 = *(longlong *)(lVar2 + 0x18);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa4) = 0;
  return lVar2;
}
```

### GameLoginResponsePropInit @ 0x1404d7770
```c
// ==== FUN_1404d7770 @ 1404d7770 size=399  [fn] ====

longlong FUN_1404d7770(undefined8 param_1)

{
  longlong lVar1;
  longlong lVar2;
  longlong lVar3;
  longlong lVar4;
  undefined8 uVar5;
  
  lVar2 = FUN_1416a7c30(&DAT_143853c88,1);
  lVar3 = FUN_1416a7aa0(&DAT_143570450);
  *(undefined2 *)(lVar3 + 0x48) = 0x101;
  *(undefined1 *)(lVar3 + 0x4a) = 0;
  *(undefined ***)(lVar3 + 8) = &PTR_DAT_143f1a9a0;
  *(undefined8 *)(lVar3 + 0x18) = 0;
  lVar1 = DAT_143f8caf8;
  lVar4 = *(longlong *)(DAT_143f8caf8 + 0x20);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1435636b0);
    FUN_14168de30(lVar4,&PTR_DAT_143f61148);
    FUN_1416a80d0(lVar1 + 0x20,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x20,lVar4);
  lVar4 = *(longlong *)(lVar1 + 0x28);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_143563638);
    FUN_14168de3c(lVar4,&PTR_DAT_143f61148);
    FUN_1416a80d0(lVar1 + 0x28,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x28,lVar4);
  *(undefined8 *)(lVar3 + 0x50) = 0;
  *(undefined2 *)(lVar3 + 0x4b) = 0;
  *(undefined8 *)(lVar3 + 0x58) = 0;
  *(undefined ***)(lVar3 + 0x30) = &PTR_DAT_143de5348;
  *(undefined8 *)(lVar3 + 0x38) = 0;
  lVar4 = *(longlong *)(lVar1 + 0x30);
  if (lVar4 == 0) {
    lVar4 = FUN_1416a7aa0(&DAT_1436204a0);
    FUN_14168de48(lVar4,&PTR_DAT_143f61148);
    FUN_1416a80d0(lVar1 + 0x30,lVar4);
  }
  FUN_1416a80d0(lVar3 + 0x40,lVar4);
  uVar5 = FUN_1415786f0(&PTR_PTR_143195348,param_1,lVar3);
  FUN_140841a80(lVar2,0,uVar5);
  lVar4 = *(longlong *)(lVar2 + 0x10);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa3) = 0;
  lVar4 = *(longlong *)(lVar2 + 0x10);
  if (*(longlong *)(lVar4 + 8) != 0) {
    FUN_140a69af0();
  }
  *(undefined1 *)(lVar4 + 0xa4) = 0;
  return lVar2;
}
```

### LoginLogInfoDTO.get_DeviceId @ 0x1404d4310
```c
// ==== FUN_1404d4310 @ 1404d4310 size=5  [fn] ====

undefined8 FUN_1404d4310(longlong param_1)

{
  return *(undefined8 *)(param_1 + 0x18);
}
```

### LoginLogInfoDTO.set_DeviceId @ 0x1404d4320
```c
// ==== FUN_1404d4320 @ 1404d4320 size=11  [fn] ====

void FUN_1404d4320(longlong param_1)

{
  FUN_1416a80d0(param_1 + 0x18);
  return;
}
```

### CngProvider.MicrosoftPlatformCryptoProvider getter @ 0x140a22010
```c
// ==== FUN_140a22010 @ 140a22010 size=79  [refs literal Microsoft Platform Crypto Provider] ====

longlong FUN_140a22010(void)

{
  longlong lVar1;
  
  lVar1 = *(longlong *)(DAT_143f8de40 + 8);
  if (lVar1 == 0) {
    lVar1 = FUN_1416a7aa0(&DAT_14331ec90);
    FUN_140a21f40(lVar1,&PTR_DAT_143e3a978);
    FUN_1416a80d0(DAT_143f8de40 + 8,lVar1);
  }
  return lVar1;
}
```

### CngProvider.MicrosoftSoftwareKeyStorageProvider getter @ 0x140a22060
```c
// ==== FUN_140a22060 @ 140a22060 size=79  [refs literal Microsoft Software Key Storage Provider] ====

longlong FUN_140a22060(void)

{
  longlong lVar1;
  
  lVar1 = *(longlong *)(DAT_143f8de40 + 0x18);
  if (lVar1 == 0) {
    lVar1 = FUN_1416a7aa0(&DAT_14331ec90);
    FUN_140a21f40(lVar1,&PTR_DAT_143e3aa18);
    FUN_1416a80d0(DAT_143f8de40 + 0x18,lVar1);
  }
  return lVar1;
}
```

### CngKey.KeySize getter (only user of Platform provider) @ 0x140a29e70
```c
// ==== FUN_140a29e70 @ 140a29e70 size=715  [caller of 140a22010] ====

/* WARNING: Globals starting with '_' overlap smaller symbols at the same address */

int FUN_140a29e70(longlong param_1)

{
  int iVar1;
  int iVar2;
  longlong lVar3;
  longlong lVar4;
  undefined **ppuVar5;
  undefined8 uVar6;
  undefined1 auVar7 [16];
  undefined1 auVar8 [16];
  undefined1 auVar9 [16];
  undefined1 auVar10 [16];
  undefined1 auVar11 [16];
  int local_18 [2];
  
  local_18[0] = 0;
  iVar1 = FUN_140a1fd30(*(undefined8 *)(param_1 + 8),&PTR_DAT_143e57f38,local_18);
  iVar2 = 0;
  if (iVar1 != 0) {
    iVar2 = FUN_140a1fd30(*(undefined8 *)(param_1 + 8),&PTR_DAT_143e2dd30,local_18);
  }
  if (iVar2 != 0) {
    uVar6 = FUN_140a20d80(iVar2);
                    /* WARNING: Subroutine does not return */
    FUN_1416a8430(uVar6);
  }
  if (local_18[0] != 0) {
    return local_18[0];
  }
  lVar3 = FUN_140a29dc0(param_1);
  lVar4 = FUN_140a22010();
  if (lVar3 == 0) {
    if (lVar4 != 0) {
      return local_18[0];
    }
  }
  else {
    if (lVar4 == 0) {
      iVar2 = 0;
    }
    else {
      iVar2 = FUN_140771570(*(undefined8 *)(lVar3 + 8),*(undefined8 *)(lVar4 + 8));
    }
    if (iVar2 == 0) {
      return local_18[0];
    }
  }
  lVar3 = FUN_140a29c90(param_1);
  lVar4 = FUN_140a21bf0();
  if (lVar3 == 0) {
    if (lVar4 == 0) goto LAB_140a29f74;
  }
  else {
    if (lVar4 == 0) {
      iVar2 = 0;
    }
    else {
      iVar2 = FUN_140771570(*(undefined8 *)(lVar3 + 8),*(undefined8 *)(lVar4 + 8));
    }
    if (iVar2 != 0) goto LAB_140a29f74;
  }
  lVar4 = FUN_140a21c40();
  if (lVar3 == 0) {
    if (lVar4 != 0) {
      return local_18[0];
    }
  }
  else {
    if (lVar4 == 0) {
      iVar2 = 0;
    }
    else {
      iVar2 = FUN_140771570(*(undefined8 *)(lVar3 + 8),*(undefined8 *)(lVar4 + 8));
    }
    if (iVar2 == 0) {
      return local_18[0];
    }
  }
LAB_140a29f74:
  lVar3 = FUN_140a263a0(*(undefined8 *)(param_1 + 8),&PTR_DAT_143dfb738,0);
  if (lVar3 == 0) {
    ppuVar5 = (undefined **)0x0;
  }
  else if (*(int *)(lVar3 + 8) == 0) {
    ppuVar5 = &PTR_DAT_143d8d2b0;
  }
  else {
    ppuVar5 = (undefined **)FUN_14084b630();
  }
  if (ppuVar5 == &PTR_DAT_143ef3410) {
    return 0xc0;
  }
  if ((ppuVar5 != (undefined **)0x0) && (*(int *)(ppuVar5 + 1) == 8)) {
    auVar7._0_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0xc) == _DAT_1438b00f0);
    auVar7._4_4_ = -(uint)(*(int *)(ppuVar5 + 2) == _UNK_1438b00f4);
    auVar7._8_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0x14) == _UNK_1438b00f8);
    auVar7._12_4_ = -(uint)(*(int *)(ppuVar5 + 3) == _UNK_1438b00fc);
    if ((ushort)((ushort)(SUB161(auVar7 >> 7,0) & 1) | (ushort)(SUB161(auVar7 >> 0xf,0) & 1) << 1 |
                 (ushort)(SUB161(auVar7 >> 0x17,0) & 1) << 2 |
                 (ushort)(SUB161(auVar7 >> 0x1f,0) & 1) << 3 |
                 (ushort)(SUB161(auVar7 >> 0x27,0) & 1) << 4 |
                 (ushort)(SUB161(auVar7 >> 0x2f,0) & 1) << 5 |
                 (ushort)(SUB161(auVar7 >> 0x37,0) & 1) << 6 |
                 (ushort)(SUB161(auVar7 >> 0x3f,0) & 1) << 7 |
                 (ushort)(SUB161(auVar7 >> 0x47,0) & 1) << 8 |
                 (ushort)(SUB161(auVar7 >> 0x4f,0) & 1) << 9 |
                 (ushort)(SUB161(auVar7 >> 0x57,0) & 1) << 10 |
                 (ushort)(SUB161(auVar7 >> 0x5f,0) & 1) << 0xb |
                 (ushort)((byte)(auVar7._12_4_ >> 7) & 1) << 0xc |
                 (ushort)((byte)(auVar7._12_4_ >> 0xf) & 1) << 0xd |
                 (ushort)((byte)(auVar7._12_4_ >> 0x17) & 1) << 0xe |
                (ushort)(byte)(auVar7._12_4_ >> 0x1f) << 0xf) == 0xffff) {
      return 0xc0;
    }
  }
  if (ppuVar5 == &PTR_DAT_143ef3438) {
    return 0xe0;
  }
  if ((ppuVar5 != (undefined **)0x0) && (*(int *)(ppuVar5 + 1) == 8)) {
    auVar8._0_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0xc) == _DAT_1438b0100);
    auVar8._4_4_ = -(uint)(*(int *)(ppuVar5 + 2) == _UNK_1438b0104);
    auVar8._8_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0x14) == _UNK_1438b0108);
    auVar8._12_4_ = -(uint)(*(int *)(ppuVar5 + 3) == _UNK_1438b010c);
    if ((ushort)((ushort)(SUB161(auVar8 >> 7,0) & 1) | (ushort)(SUB161(auVar8 >> 0xf,0) & 1) << 1 |
                 (ushort)(SUB161(auVar8 >> 0x17,0) & 1) << 2 |
                 (ushort)(SUB161(auVar8 >> 0x1f,0) & 1) << 3 |
                 (ushort)(SUB161(auVar8 >> 0x27,0) & 1) << 4 |
                 (ushort)(SUB161(auVar8 >> 0x2f,0) & 1) << 5 |
                 (ushort)(SUB161(auVar8 >> 0x37,0) & 1) << 6 |
                 (ushort)(SUB161(auVar8 >> 0x3f,0) & 1) << 7 |
                 (ushort)(SUB161(auVar8 >> 0x47,0) & 1) << 8 |
                 (ushort)(SUB161(auVar8 >> 0x4f,0) & 1) << 9 |
                 (ushort)(SUB161(auVar8 >> 0x57,0) & 1) << 10 |
                 (ushort)(SUB161(auVar8 >> 0x5f,0) & 1) << 0xb |
                 (ushort)((byte)(auVar8._12_4_ >> 7) & 1) << 0xc |
                 (ushort)((byte)(auVar8._12_4_ >> 0xf) & 1) << 0xd |
                 (ushort)((byte)(auVar8._12_4_ >> 0x17) & 1) << 0xe |
                (ushort)(byte)(auVar8._12_4_ >> 0x1f) << 0xf) == 0xffff) {
      return 0xe0;
    }
  }
  if (ppuVar5 == &PTR_DAT_143ef3460) {
    return 0x100;
  }
  if ((ppuVar5 != (undefined **)0x0) && (*(int *)(ppuVar5 + 1) == 8)) {
    auVar9._0_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0xc) == _DAT_1438b0110);
    auVar9._4_4_ = -(uint)(*(int *)(ppuVar5 + 2) == _UNK_1438b0114);
    auVar9._8_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0x14) == _UNK_1438b0118);
    auVar9._12_4_ = -(uint)(*(int *)(ppuVar5 + 3) == _UNK_1438b011c);
    if ((ushort)((ushort)(SUB161(auVar9 >> 7,0) & 1) | (ushort)(SUB161(auVar9 >> 0xf,0) & 1) << 1 |
                 (ushort)(SUB161(auVar9 >> 0x17,0) & 1) << 2 |
                 (ushort)(SUB161(auVar9 >> 0x1f,0) & 1) << 3 |
                 (ushort)(SUB161(auVar9 >> 0x27,0) & 1) << 4 |
                 (ushort)(SUB161(auVar9 >> 0x2f,0) & 1) << 5 |
                 (ushort)(SUB161(auVar9 >> 0x37,0) & 1) << 6 |
                 (ushort)(SUB161(auVar9 >> 0x3f,0) & 1) << 7 |
                 (ushort)(SUB161(auVar9 >> 0x47,0) & 1) << 8 |
                 (ushort)(SUB161(auVar9 >> 0x4f,0) & 1) << 9 |
                 (ushort)(SUB161(auVar9 >> 0x57,0) & 1) << 10 |
                 (ushort)(SUB161(auVar9 >> 0x5f,0) & 1) << 0xb |
                 (ushort)((byte)(auVar9._12_4_ >> 7) & 1) << 0xc |
                 (ushort)((byte)(auVar9._12_4_ >> 0xf) & 1) << 0xd |
                 (ushort)((byte)(auVar9._12_4_ >> 0x17) & 1) << 0xe |
                (ushort)(byte)(auVar9._12_4_ >> 0x1f) << 0xf) == 0xffff) {
      return 0x100;
    }
  }
  if (ppuVar5 == &PTR_DAT_143ef3488) {
    return 0x180;
  }
  if ((ppuVar5 != (undefined **)0x0) && (*(int *)(ppuVar5 + 1) == 8)) {
    auVar10._0_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0xc) == _DAT_1438b0120);
    auVar10._4_4_ = -(uint)(*(int *)(ppuVar5 + 2) == _UNK_1438b0124);
    auVar10._8_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0x14) == _UNK_1438b0128);
    auVar10._12_4_ = -(uint)(*(int *)(ppuVar5 + 3) == _UNK_1438b012c);
    if ((ushort)((ushort)(SUB161(auVar10 >> 7,0) & 1) | (ushort)(SUB161(auVar10 >> 0xf,0) & 1) << 1
                 | (ushort)(SUB161(auVar10 >> 0x17,0) & 1) << 2 |
                 (ushort)(SUB161(auVar10 >> 0x1f,0) & 1) << 3 |
                 (ushort)(SUB161(auVar10 >> 0x27,0) & 1) << 4 |
                 (ushort)(SUB161(auVar10 >> 0x2f,0) & 1) << 5 |
                 (ushort)(SUB161(auVar10 >> 0x37,0) & 1) << 6 |
                 (ushort)(SUB161(auVar10 >> 0x3f,0) & 1) << 7 |
                 (ushort)(SUB161(auVar10 >> 0x47,0) & 1) << 8 |
                 (ushort)(SUB161(auVar10 >> 0x4f,0) & 1) << 9 |
                 (ushort)(SUB161(auVar10 >> 0x57,0) & 1) << 10 |
                 (ushort)(SUB161(auVar10 >> 0x5f,0) & 1) << 0xb |
                 (ushort)((byte)(auVar10._12_4_ >> 7) & 1) << 0xc |
                 (ushort)((byte)(auVar10._12_4_ >> 0xf) & 1) << 0xd |
                 (ushort)((byte)(auVar10._12_4_ >> 0x17) & 1) << 0xe |
                (ushort)(byte)(auVar10._12_4_ >> 0x1f) << 0xf) == 0xffff) {
      return 0x180;
    }
  }
  if (ppuVar5 != &PTR_DAT_143ef34b0) {
    if (ppuVar5 == (undefined **)0x0) {
      return local_18[0];
    }
    if (*(int *)(ppuVar5 + 1) != 8) {
      return local_18[0];
    }
    auVar11._0_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0xc) == _DAT_1438b0130);
    auVar11._4_4_ = -(uint)(*(int *)(ppuVar5 + 2) == _UNK_1438b0134);
    auVar11._8_4_ = -(uint)(*(int *)((longlong)ppuVar5 + 0x14) == _UNK_1438b0138);
    auVar11._12_4_ = -(uint)(*(int *)(ppuVar5 + 3) == _UNK_1438b013c);
    if ((ushort)((ushort)(SUB161(auVar11 >> 7,0) & 1) | (ushort)(SUB161(auVar11 >> 0xf,0) & 1) << 1
                 | (ushort)(SUB161(auVar11 >> 0x17,0) & 1) << 2 |
                 (ushort)(SUB161(auVar11 >> 0x1f,0) & 1) << 3 |
                 (ushort)(SUB161(auVar11 >> 0x27,0) & 1) << 4 |
                 (ushort)(SUB161(auVar11 >> 0x2f,0) & 1) << 5 |
                 (ushort)(SUB161(auVar11 >> 0x37,0) & 1) << 6 |
                 (ushort)(SUB161(auVar11 >> 0x3f,0) & 1) << 7 |
                 (ushort)(SUB161(auVar11 >> 0x47,0) & 1) << 8 |
                 (ushort)(SUB161(auVar11 >> 0x4f,0) & 1) << 9 |
                 (ushort)(SUB161(auVar11 >> 0x57,0) & 1) << 10 |
                 (ushort)(SUB161(auVar11 >> 0x5f,0) & 1) << 0xb |
                 (ushort)((byte)(auVar11._12_4_ >> 7) & 1) << 0xc |
                 (ushort)((byte)(auVar11._12_4_ >> 0xf) & 1) << 0xd |
                 (ushort)((byte)(auVar11._12_4_ >> 0x17) & 1) << 0xe |
                (ushort)(byte)(auVar11._12_4_ >> 0x1f) << 0xf) != 0xffff) {
      return local_18[0];
    }
  }
  return 0x209;
}
```

### NCryptOpenStorageProvider wrapper @ 0x140a1f980
```c
// ==== FUN_140a1f980 @ 140a1f980 size=240  [uses NCryptOpenStorageProvider] ====

SECURITY_STATUS FUN_140a1f980(undefined8 *param_1,longlong param_2,DWORD param_3)

{
  SECURITY_STATUS SVar1;
  longlong lVar2;
  LPCWSTR pszProviderName;
  undefined1 local_80 [40];
  LPCWSTR local_58;
  NCRYPT_PROV_HANDLE local_50;
  undefined4 local_44;
  
  local_58 = (LPCWSTR)0x0;
  local_44 = 0;
  *param_1 = 0;
  local_50 = 0;
  lVar2 = FUN_14156e1b0(&PTR_DAT_1431a1ba0);
  if (param_2 == 0) {
    pszProviderName = (LPCWSTR)0x0;
  }
  else {
    pszProviderName = (LPCWSTR)(param_2 + 0xc);
  }
  local_58 = pszProviderName;
  FUN_1416a96b0(local_80);
  SVar1 = NCryptOpenStorageProvider(&local_50,pszProviderName,param_3);
  FUN_1416a9700();
  local_58 = (LPCWSTR)0x0;
  local_44 = 1;
  *(NCRYPT_PROV_HANDLE *)(lVar2 + 8) = local_50;
  FUN_1416a8140(param_1,lVar2);
  return SVar1;
}


== symbol NCRYPT.DLL::NCryptGetProperty @ EXTERNAL:0000011f ext=true type=Function
   xref sym EXTERNAL:0000011f <- 141787d98 DATA in <nofunc>
      xref2 141787d98 <- 1417496ed INDIRECTION in NCRYPT.DLL::NCryptGetProperty @ 1417496ed
   xref sym EXTERNAL:0000011f <- 1417496ed COMPUTED_JUMP in NCRYPT.DLL::NCryptGetProperty @ 1417496ed
   thunk 1417496ed
   xref thunk 1417496ed <- 140a1fb85 UNCONDITIONAL_CALL in FUN_140a1fac0 @ 140a1fac0
```

### NCryptOpenKey wrapper @ 0x140a1f530
```c
// ==== FUN_140a1f530 @ 140a1f530 size=337  [uses NCryptOpenKey] ====

SECURITY_STATUS
FUN_140a1f530(longlong param_1,undefined8 *param_2,longlong param_3,DWORD param_4,DWORD param_5)

{
  NCRYPT_PROV_HANDLE hProvider;
  SECURITY_STATUS SVar1;
  longlong lVar2;
  LPCWSTR pszKeyName;
  undefined1 local_90 [40];
  LPCWSTR local_68;
  longlong lStack_60;
  undefined8 local_58;
  NCRYPT_KEY_HANDLE local_50;
  undefined4 local_44;
  
  local_68 = (LPCWSTR)0x0;
  lStack_60 = 0;
  local_58 = 0;
  local_44 = 0;
  *param_2 = 0;
  local_50 = 0;
  lVar2 = FUN_14156e1b0(&PTR_DAT_1431a1b80);
  local_58 = 0;
  lStack_60 = param_1;
  FUN_14084eb20(param_1,&local_58);
  if (param_3 == 0) {
    pszKeyName = (LPCWSTR)0x0;
  }
  else {
    pszKeyName = (LPCWSTR)(param_3 + 0xc);
  }
  hProvider = *(NCRYPT_PROV_HANDLE *)(lStack_60 + 8);
  local_68 = pszKeyName;
  FUN_1416a96b0(local_90);
  SVar1 = NCryptOpenKey(hProvider,&local_50,pszKeyName,param_4,param_5);
  FUN_1416a9700();
  local_68 = (LPCWSTR)0x0;
  local_44 = 1;
  *(NCRYPT_KEY_HANDLE *)(lVar2 + 8) = local_50;
  FUN_1416a8140(param_2,lVar2);
  if ((char)local_58 != '\0') {
    FUN_14084eb80(lStack_60,0);
  }
  return SVar1;
}


== symbol NCRYPT.DLL::NCryptOpenStorageProvider @ EXTERNAL:0000011e ext=true type=Function
   xref sym EXTERNAL:0000011e <- 141787d90 DATA in <nofunc>
      xref2 141787d90 <- 1417496ff INDIRECTION in NCRYPT.DLL::NCryptOpenStorageProvider @ 1417496ff
   xref sym EXTERNAL:0000011e <- 1417496ff COMPUTED_JUMP in NCRYPT.DLL::NCryptOpenStorageProvider @ 1417496ff
   thunk 1417496ff
   xref thunk 1417496ff <- 140a1fa1c UNCONDITIONAL_CALL in FUN_140a1f980 @ 140a1f980
```

### NCryptGetProperty wrapper @ 0x140a1fac0
```c
// ==== FUN_140a1fac0 @ 140a1fac0 size=267  [uses NCryptGetProperty] ====

SECURITY_STATUS
FUN_140a1fac0(longlong param_1,longlong param_2,PBYTE param_3,DWORD param_4,DWORD *param_5,
             DWORD param_6)

{
  NCRYPT_HANDLE hObject;
  SECURITY_STATUS SVar1;
  LPCWSTR pszProperty;
  undefined1 local_88 [40];
  LPCWSTR local_60;
  DWORD *local_58;
  longlong lStack_50;
  undefined8 local_48;
  
  local_60 = (LPCWSTR)0x0;
  local_58 = (DWORD *)0x0;
  *param_5 = 0;
  local_48 = 0;
  lStack_50 = param_1;
  FUN_14084eb20();
  local_58 = param_5;
  if (param_2 == 0) {
    pszProperty = (LPCWSTR)0x0;
  }
  else {
    pszProperty = (LPCWSTR)(param_2 + 0xc);
  }
  hObject = *(NCRYPT_HANDLE *)(lStack_50 + 8);
  local_60 = pszProperty;
  FUN_1416a96b0(local_88);
  SVar1 = NCryptGetProperty(hObject,pszProperty,param_3,param_4,param_5,param_6);
  FUN_1416a9700();
  local_60 = (LPCWSTR)0x0;
  local_58 = (DWORD *)0x0;
  if ((char)local_48 != '\0') {
    FUN_14084eb80(lStack_50,0);
  }
  return SVar1;
}


== symbol NCRYPT.DLL::NCryptFreeObject @ EXTERNAL:00000120 ext=true type=Function
   xref sym EXTERNAL:00000120 <- 141787da0 DATA in <nofunc>
      xref2 141787da0 <- 1417496e7 INDIRECTION in NCRYPT.DLL::NCryptFreeObject @ 1417496e7
   xref sym EXTERNAL:00000120 <- 1417496e7 COMPUTED_JUMP in NCRYPT.DLL::NCryptFreeObject @ 1417496e7
   thunk 1417496e7
   xref thunk 1417496e7 <- 140a208dc UNCONDITIONAL_CALL in FUN_140a208a0 @ 140a208a0
```

### NCryptImportKey wrapper @ 0x140a1f6e0
```c
// ==== FUN_140a1f6e0 @ 140a1f6e0 size=379  [uses NCryptImportKey] ====

SECURITY_STATUS
FUN_140a1f6e0(longlong param_1,NCRYPT_KEY_HANDLE param_2,longlong param_3,NCryptBufferDesc *param_4,
             undefined8 *param_5,PBYTE param_6,DWORD param_7,DWORD param_8)

{
  NCRYPT_PROV_HANDLE hProvider;
  SECURITY_STATUS SVar1;
  longlong lVar2;
  LPCWSTR pszBlobType;
  NCRYPT_KEY_HANDLE *phKey;
  undefined1 local_98 [40];
  LPCWSTR local_70;
  PBYTE local_68;
  longlong lStack_60;
  undefined8 local_58;
  NCRYPT_KEY_HANDLE local_50;
  undefined4 local_44;
  
  local_70 = (LPCWSTR)0x0;
  local_68 = (PBYTE)0x0;
  lStack_60 = 0;
  local_58 = 0;
  local_44 = 0;
  *param_5 = 0;
  local_50 = 0;
  lVar2 = FUN_14156e1b0(&PTR_DAT_1431a1b80);
  local_58 = 0;
  lStack_60 = param_1;
  FUN_14084eb20(param_1,&local_58);
  local_68 = param_6;
  if (param_3 == 0) {
    pszBlobType = (LPCWSTR)0x0;
  }
  else {
    pszBlobType = (LPCWSTR)(param_3 + 0xc);
  }
  phKey = &local_50;
  hProvider = *(NCRYPT_PROV_HANDLE *)(lStack_60 + 8);
  local_70 = pszBlobType;
  FUN_1416a96b0(local_98);
  SVar1 = NCryptImportKey(hProvider,param_2,pszBlobType,param_4,phKey,param_6,param_7,param_8);
  FUN_1416a9700();
  local_70 = (LPCWSTR)0x0;
  local_68 = (PBYTE)0x0;
  local_44 = 1;
  *(NCRYPT_KEY_HANDLE *)(lVar2 + 8) = local_50;
  FUN_1416a8140(param_5,lVar2);
  if ((char)local_58 != '\0') {
    FUN_14084eb80(lStack_60,0);
  }
  return SVar1;
}


== symbol NCRYPT.DLL::NCryptOpenKey @ EXTERNAL:0000011d ext=true type=Function
   xref sym EXTERNAL:0000011d <- 141787d88 DATA in <nofunc>
      xref2 141787d88 <- 1417496f9 INDIRECTION in NCRYPT.DLL::NCryptOpenKey @ 1417496f9
   xref sym EXTERNAL:0000011d <- 1417496f9 COMPUTED_JUMP in NCRYPT.DLL::NCryptOpenKey @ 1417496f9
   thunk 1417496f9
   xref thunk 1417496f9 <- 140a1f614 UNCONDITIONAL_CALL in FUN_140a1f530 @ 140a1f530
```

### CngProvider open (caller of NCryptOpenStorageProvider) @ 0x140a26310
```c
// ==== FUN_140a26310 @ 140a26310 size=73  [caller of 140a1f980] ====

undefined8 FUN_140a26310(longlong param_1)

{
  int iVar1;
  undefined8 uVar2;
  undefined8 local_10;
  
  local_10 = 0;
  iVar1 = FUN_140a1f980(&local_10,*(undefined8 *)(param_1 + 8),0);
  if (iVar1 == 0) {
    return local_10;
  }
  FUN_14084eac0();
  uVar2 = FUN_140a20d80(iVar1);
                    /* WARNING: Subroutine does not return */
  FUN_1416a8430(uVar2);
}
```

### NetworkInterface enumeration (GetAdaptersAddresses) @ 0x14072ca60
```c
// ==== FUN_14072ca60 @ 14072ca60 size=525  [uses GetAdaptersAddresses] ====

undefined ** FUN_14072ca60(void)

{
  uint uVar1;
  int iVar2;
  longlong lVar3;
  undefined **ppuVar4;
  ulonglong uVar5;
  undefined8 uVar6;
  int *piVar7;
  undefined1 local_78 [40];
  ulonglong local_50;
  int local_48 [2];
  
  local_48[0] = 0;
  lVar3 = FUN_1416a7aa0(&DAT_143550940);
  *(undefined ***)(lVar3 + 8) = &PTR_DAT_143f46b08;
  piVar7 = local_48;
  FUN_1416a96b0(local_78);
  iVar2 = GetAdaptersAddresses(0,0x1c0,0,0,piVar7);
  FUN_1416a9700(local_78);
  while (iVar2 == 0x6f) {
    uVar5 = FUN_14084d050((longlong)local_48[0]);
    piVar7 = local_48;
    local_50 = uVar5;
    FUN_1416a96b0(local_78);
    iVar2 = GetAdaptersAddresses(0,0x1c0,0,uVar5,piVar7);
    FUN_1416a9700(local_78);
    uVar5 = local_50;
    if (iVar2 == 0) {
      for (; uVar5 != 0; uVar5 = *(ulonglong *)(uVar5 + 8)) {
        uVar6 = FUN_1416a7aa0(&DAT_1432cb8c0);
        FUN_14072cc90(uVar6,uVar5);
        *(int *)(lVar3 + 0x14) = *(int *)(lVar3 + 0x14) + 1;
        uVar1 = *(uint *)(lVar3 + 0x10);
        if (uVar1 < *(uint *)(*(longlong *)(lVar3 + 8) + 8)) {
          *(uint *)(lVar3 + 0x10) = uVar1 + 1;
          FUN_1416a80d0(*(longlong *)(lVar3 + 8) + 0x10 + (ulonglong)uVar1 * 8,uVar6);
        }
        else {
          FUN_141375ff0(lVar3,uVar6);
        }
      }
    }
    if ((local_50 & 0xffffffffffff0000) != 0) {
      FUN_14087b700(local_50);
    }
  }
  if ((iVar2 == 0xe8) || (iVar2 == 0x57)) {
    ppuVar4 = &PTR_DAT_143f46af0;
  }
  else {
    if (iVar2 != 0) {
      lVar3 = FUN_1416a7aa0(&DAT_1432cb668);
      uVar6 = FUN_14087d320(iVar2,0);
      FUN_14084e520(lVar3,uVar6);
      *(int *)(lVar3 + 0x50) = iVar2;
                    /* WARNING: Subroutine does not return */
      FUN_1416a8430(lVar3);
    }
    ppuVar4 = (undefined **)FUN_141376ff0(lVar3);
  }
  return ppuVar4;
}


== symbol IPHLPAPI.DLL::GetAdaptersAddresses @ 14174930f ext=false type=Function
   xref sym 14174930f <- 14072cae5 UNCONDITIONAL_CALL in FUN_14072ca60 @ 14072ca60
   xref sym 14174930f <- 14072cb8a UNCONDITIONAL_CALL in FUN_14072ca60 @ 14072ca60
// (already decompiled 14072ca60 for uses GetAdaptersAddresses)
```

### HttpWindowsProxy ctor path (caller chain of GetAdaptersAddresses) @ 0x14070e760
```c
// ==== FUN_14070e760 @ 14070e760 size=485  [fn] ====

void FUN_14070e760(longlong param_1,longlong param_2)

{
  longlong lVar1;
  int iVar2;
  undefined8 uVar3;
  undefined8 uVar4;
  longlong lVar5;
  longlong lVar6;
  
  if (PTR_FUN_143f7c040 != (undefined *)0x0) {
    FUN_1416819a0();
  }
  if (*(longlong *)(DAT_143f8cbe0 + 8) == 0) {
    uVar3 = 0;
  }
  else {
    uVar3 = FUN_1404f29a0(*(longlong *)(DAT_143f8cbe0 + 8),&PTR_DAT_143e6b238,0);
  }
  FUN_1416a80d0(param_1 + 8,uVar3);
  uVar3 = FUN_1416a7aa0(&DAT_1432c6fd8);
  FUN_1406f8fe0(uVar3);
  FUN_1416a80d0(param_1 + 0x10,uVar3);
  uVar3 = FUN_1416a7aa0(&DAT_1432d9d08);
  FUN_1407f9810(uVar3,0,0);
  FUN_1416a80d0(param_1 + 0x40,uVar3);
  if ((*(longlong *)(param_1 + 8) != 0) && (param_2 == 0)) {
    uVar3 = FUN_1404f2ca0();
    uVar4 = FUN_140801230();
    iVar2 = FUN_1406ccfb0(uVar3,1,0x10000007,uVar4,1);
    if (iVar2 == 0) {
      lVar1 = *(longlong *)(param_1 + 0x40);
      lVar5 = *(longlong *)(DAT_143f8d3b8 + 8);
      if (lVar5 == 0) {
        lVar5 = FUN_1416a7aa0(&DAT_1432da6f8);
        FUN_1416982a9(lVar5,0);
        FUN_1416a80d0(DAT_143f8d3b8 + 8,lVar5);
      }
      if (lVar1 == 0) {
                    /* WARNING: Subroutine does not return */
        FUN_1407853c0(&PTR_DAT_143f0a820);
      }
      lVar6 = FUN_1416a7aa0(&DAT_1432da870);
      FUN_1416a80d0(lVar6 + 8,lVar5);
      FUN_1416a80d0(lVar6 + 0x18,param_1);
      uVar3 = FUN_1407f9980();
      FUN_1416a80d0(lVar6 + 0x10,uVar3);
      uVar3 = FUN_140801230(lVar1);
      uVar4 = FUN_1416a7ae0(&DAT_1432dae50);
      FUN_140802bb0(uVar4,uVar3,lVar6,0xffffffff,1);
      FUN_140802ea0(uVar4);
      FUN_1416a80d0(param_1 + 0x48,uVar4);
    }
  }
  FUN_14070ea40(param_1,param_2);
  return;
}
```

### SystemProxyInfo.ConstructSystemProxy @ 0x14070ae40
```c
// ==== FUN_14070ae40 @ 14070ae40 size=67  [fn] ====

undefined8 FUN_14070ae40(void)

{
  int iVar1;
  undefined8 uVar2;
  undefined8 local_10;
  
  local_10 = 0;
  iVar1 = FUN_14070e650(&local_10);
  if (iVar1 == 0) {
    uVar2 = FUN_1416a7aa0(&DAT_1432c90a0);
    FUN_14070e760(uVar2,0);
    local_10 = uVar2;
  }
  return local_10;
}
```

### Environment.MachineName core (GetComputerNameW) @ 0x14087b080
```c
// ==== FUN_14087b080 @ 14087b080 size=148  [fn] ====

undefined8 FUN_14087b080(void)

{
  int iVar1;
  undefined8 uVar2;
  undefined1 *local_58;
  uint local_50;
  undefined1 *local_48;
  uint local_40 [2];
  undefined1 local_38 [32];
  longlong local_18;
  
  local_58 = (undefined1 *)0x0;
  local_18 = DAT_143ffb000;
  local_40[0] = 0x10;
  local_48 = local_38;
  iVar1 = FUN_14087b010(local_38,local_40);
  if (iVar1 == 0) {
    uVar2 = 0;
    if (local_18 != DAT_143ffb000) {
      uVar2 = FUN_1416a7800();
    }
    return uVar2;
  }
  if (local_40[0] < 0x11) {
    local_50 = local_40[0];
    local_58 = local_38;
    uVar2 = FUN_1411938a0(&local_58);
    if (local_18 != DAT_143ffb000) {
      uVar2 = FUN_1416a7800();
    }
    return uVar2;
  }
                    /* WARNING: Subroutine does not return */
  FUN_1407b15d0();
}
```

### Environment.MachineName getter @ 0x14076c6e0
```c
// ==== FUN_14076c6e0 @ 14076c6e0 size=59  [fn] ====

void FUN_14076c6e0(void)

{
  longlong lVar1;
  undefined8 uVar2;
  
  lVar1 = FUN_14087b080();
  if (lVar1 != 0) {
    return;
  }
  uVar2 = FUN_1416a7aa0(&DAT_1432d4838);
  FUN_1407a3710(uVar2,&PTR_DAT_143dd5400);
                    /* WARNING: Subroutine does not return */
  FUN_1416a8430(uVar2);
}
```

### Assistant.Platform.GetWindowsUserName @ 0x140073290
```c
// ==== FUN_140073290 @ 140073290 size=96  [fn] ====

undefined ** FUN_140073290(void)

{
  int iVar1;
  undefined8 uVar2;
  undefined **ppuVar3;
  undefined4 local_18 [2];
  
  local_18[0] = 0x400;
  uVar2 = FUN_1416a7aa0(&DAT_1432dbb10);
  FUN_14080bdc0(uVar2,local_18[0],0x7fffffff);
  iVar1 = FUN_140073110(uVar2,local_18);
  if (iVar1 == 0) {
    return &PTR_DAT_143d8d2b0;
  }
  ppuVar3 = (undefined **)FUN_14080bfe0(uVar2);
  return ppuVar3;
}
```

### ImageSharp ICC/EXIF ctor (deviceManufacturerInfo/deviceModelInfo literals) @ 0x140534330
```c
// ==== FUN_140534330 @ 140534330 size=154  [refs literal deviceManufacturerInfo] ====

void FUN_140534330(longlong param_1,undefined4 param_2,undefined4 param_3,undefined8 param_4,
                  undefined4 param_5,longlong param_6,longlong param_7)

{
  undefined8 uVar1;
  
  *(undefined4 *)(param_1 + 0x18) = param_2;
  *(undefined4 *)(param_1 + 0x1c) = param_3;
  *(undefined8 *)(param_1 + 0x10) = param_4;
  *(undefined4 *)(param_1 + 0x20) = param_5;
  if (param_6 == 0) {
    uVar1 = FUN_1416a7aa0(&DAT_1432d1f68);
    FUN_140785320(uVar1,&PTR_DAT_143edb9e8);
                    /* WARNING: Subroutine does not return */
    FUN_1416a8430(uVar1);
  }
  FUN_1416a8140(param_1,param_6);
  if (param_7 != 0) {
    FUN_1416a8140(param_1 + 8,param_7);
    return;
  }
  uVar1 = FUN_1416a7aa0(&DAT_1432d1f68);
  FUN_140785320(uVar1,&PTR_DAT_143edba30);
                    /* WARNING: Subroutine does not return */
  FUN_1416a8430(uVar1);
}
```

### OidLookup friendly-name table cctor (TPMManufacturer/TPMModel/TPMVersion) @ 0x140a24120
```c
// ==== FUN_140a24120 @ 140a24120 size=4115  [refs literal TPMManufacturer] ====

void FUN_140a24120(void)

{
  undefined **ppuVar1;
  undefined ***local_98;
  undefined4 local_90;
  undefined1 local_88 [16];
  undefined1 local_78 [16];
  undefined1 local_68 [16];
  undefined **local_58;
  undefined **local_50;
  undefined **local_48;
  undefined **local_40;
  undefined **local_38;
  undefined **local_30;
  undefined **local_28;
  undefined **local_20;
  undefined **local_18;
  undefined **local_10;
  undefined **local_8;
  
  ppuVar1 = (undefined **)0xffffffffffffff70;
  do {
    *(undefined8 *)((longlong)&local_8 + (longlong)ppuVar1) = 0;
    *(undefined8 *)(&stack0x00000000 + (longlong)ppuVar1) = 0;
    *(undefined8 *)(&stack0x00000008 + (longlong)ppuVar1) = 0;
    *(undefined8 *)(&stack0x00000010 + (longlong)ppuVar1) = 0;
    *(undefined8 *)(&stack0x00000018 + (longlong)ppuVar1) = 0;
    *(undefined8 *)(&stack0x00000020 + (longlong)ppuVar1) = 0;
    ppuVar1 = ppuVar1 + 6;
  } while (ppuVar1 != (undefined **)0x0);
  local_8 = ppuVar1;
  if (PTR_FUN_143f7d8e0 != (undefined *)0x0) {
    FUN_14167f9d8();
  }
  local_98 = (undefined ***)0x0;
  local_90 = 0;
  FUN_140a25140(&PTR_DAT_143da4418,&PTR_DAT_143da6cc8);
  local_98 = (undefined ***)0x0;
  local_90 = 0;
  FUN_140a25140(&PTR_DAT_143da5b50,&PTR_DAT_143ecc708);
  local_98 = (undefined ***)0x0;
  local_90 = 0;
  FUN_140a25140(&PTR_DAT_143da5cb8,&PTR_DAT_143ecc730);
  local_98 = (undefined ***)0x0;
  local_90 = 0;
  FUN_140a25140(&PTR_DAT_143da5b98,&PTR_DAT_143ecc760);
  local_98 = (undefined ***)0x0;
  local_90 = 0;
  FUN_140a25140(&PTR_DAT_143da5be0,&PTR_DAT_143ecc788);
  local_98 = (undefined ***)0x0;
  local_90 = 0;
  FUN_140a25140(&PTR_DAT_143da5c28,&PTR_DAT_143ecc7b8);
  local_98 = (undefined ***)0x0;
  local_90 = 0;
  FUN_140a25140(&PTR_DAT_143da5c70,&PTR_DAT_143ecc7e0);
  local_98 = (undefined ***)0x0;
  local_90 = 0;
  ... (OID/friendly-name pairs elided; TPM entries:)
  FUN_140a25140(&PTR_DAT_143da5dd8,&PTR_DAT_143e78540);
  FUN_140a25140(&PTR_DAT_143da5e08,&PTR_DAT_143e78578);
  FUN_140a25140(&PTR_DAT_143da5e38,&PTR_DAT_143e785a0);
  ...
```
