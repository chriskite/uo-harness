import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.data.StringDataType;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;
import ghidra.program.model.listing.FunctionManager;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class ACXrefs extends GhidraScript {

    static final String[] TARGETS = {
        "Speedhack", "AutoClicking", "AutoKeyboard", "TPMModel",
        "Send_UOLive_HashResponse", "Send_TimeSyncPingReq",
        "CAPTCHA_GUMP_ID", "IsRazorBlockedSysMessage",
        "K32EnumProcesses", "K32EnumProcessModulesEx", "IsDebuggerPresent",
        "GetMd5", "CompareMd5", "TakeScreenshot", "CaptureScreenToFile",
        "get_DeviceId", "ServerClientRestriction", "VerifyDeviceModel",
        "EnablePvpScriptRestrictions", "RazorNegotiateResponse",
        "GetAsyncKeyState", "SetWindowsHookEx", "EnumWindows",
        "CheckGlobalKeys", "ResetKeyboardUsingSendInput",
        "IsMostRecentGameFilesVersion", "VersionRestrictions"
    };

    @Override
    public void run() throws Exception {
        Map<String, List<Address>> found = new LinkedHashMap<>();
        Memory mem = currentProgram.getMemory();

        // Pass 1: raw byte search (handles strings inside undefined metadata blobs)
        for (String t : TARGETS) {
            byte[] bytes = t.getBytes("ASCII");
            Address a = mem.getMinAddress();
            int guard = 0;
            while (a != null && guard++ < 64) {
                a = mem.findBytes(a, bytes, null, true, monitor);
                if (a == null) break;
                found.computeIfAbsent(t, k -> new ArrayList<>()).add(a);
                a = a.add(1);
            }
        }

        ReferenceManager rm = currentProgram.getReferenceManager();
        FunctionManager fm = currentProgram.getFunctionManager();

        for (Map.Entry<String, List<Address>> e : found.entrySet()) {
            println("=== " + e.getKey() + " (" + e.getValue().size() + " hits)");
            for (Address a : e.getValue()) {
                Function f0 = fm.getFunctionContaining(a);
                int nrefs = 0;
                StringBuilder sb = new StringBuilder();
                for (ghidra.program.model.symbol.ReferenceIterator it = rm.getReferencesTo(a); it.hasNext();) {
                    Reference r = it.next();
                    nrefs++;
                    Function f = fm.getFunctionContaining(r.getFromAddress());
                    sb.append("\n    xref from " + r.getFromAddress() + " in " +
                            (f != null ? f.getName() + " @ " + f.getEntryPoint() : "no-func") +
                            " type=" + r.getReferenceType());
                }
                println("  @ " + a + " refs=" + nrefs + (f0 != null ? " (inside func " + f0.getName() + ")" : "") + sb);
            }
        }
        println("DONE");
    }
}
