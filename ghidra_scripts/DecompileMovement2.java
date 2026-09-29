// Decompile Walk/TryStep/resync paths and xref callers.
//@category UOHarness
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import java.util.HashSet;

public class DecompileMovement2 extends GhidraScript {
    private static final long[] VAS = {
        0x140328440L, // PlayerMobile.Walk
        0x1403283C0L, // PlayerMobile.TryStep
        0x14020DA80L, // GameActions.TriggerClientResync
        0x1401A2BA0L, // Plugin.ClientResync (candidate)
        0x140033030L, // Assistant.Client.ClientResync (candidate)
        0x14030B130L, // WalkerManager.Reset (called from DenyWalk)
    };
    private static final long[] XREF_TARGETS = {
        0x140179C60L, // Send_WalkRequest
        0x140179740L, // Send_Resync
        0x14030AD40L, // FastWalkStack.AddValue
    };

    @Override
    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        HashSet<Long> done = new HashSet<Long>();
        for (long va : VAS) {
            decompOne(decomp, va);
            done.add(va);
        }
        for (long tgt : XREF_TARGETS) {
            Address taddr = toAddr(tgt);
            Function tf = getFunctionAt(taddr);
            if (tf == null) { println("### XREF target 0x" + Long.toHexString(tgt) + " no function"); continue; }
            println("### CALLERS OF " + tf.getName() + " @ 0x" + Long.toHexString(tgt));
            Reference[] refs = getReferencesTo(taddr);
            int n = 0;
            for (Reference r : refs) {
                if (n >= 12) break;
                Function caller = getFunctionContaining(r.getFromAddress());
                if (caller == null) continue;
                println("  caller: " + caller.getName() + " @ " + caller.getEntryPoint()
                        + " from " + r.getFromAddress());
                long cva = caller.getEntryPoint().getOffset();
                if (!done.contains(cva)) {
                    done.add(cva);
                    DecompileResults res = decomp.decompileFunction(caller, 240, monitor);
                    if (res.decompileCompleted()) {
                        println(res.getDecompiledFunction().getC());
                    }
                }
                n++;
            }
        }
    }

    private void decompOne(DecompInterface decomp, long va) throws Exception {
        Address addr = toAddr(va);
        Function fn = getFunctionAt(addr);
        if (fn == null) fn = getFunctionContaining(addr);
        if (fn == null) { println("=== VA 0x" + Long.toHexString(va) + ": NO FUNCTION ==="); return; }
        println("=== VA 0x" + Long.toHexString(va) + " : " + fn.getName()
                + " entry=" + fn.getEntryPoint() + " size=" + fn.getBody().getNumAddresses() + " ===");
        DecompileResults res = decomp.decompileFunction(fn, 240, monitor);
        if (res.decompileCompleted()) println(res.getDecompiledFunction().getC());
        else println("DECOMPILE FAILED: " + res.getErrorMessage());
    }
}
