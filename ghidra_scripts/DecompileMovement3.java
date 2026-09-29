// Decompile the fastwalk-key getter and xref reset/resync callers.
//@category UOHarness
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import java.util.HashSet;

public class DecompileMovement3 extends GhidraScript {
    private static final long[] VAS = {
        0x14030ADA0L, // key source for Send_WalkRequest (FastWalkStack.GetValue?)
        0x14031A440L, // ClearSteps
        0x140328180L, // called near end of MobileUpdate player branch
        0x14031A470L, // TryStep-ish in MobileUpdateV10
    };
    private static final long[] XREF_TARGETS = {
        0x14030B130L, // WalkerManager.Reset
        0x14020DA80L, // TriggerClientResync
        0x140328440L, // PlayerMobile.Walk
    };

    @Override
    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        HashSet<Long> done = new HashSet<Long>();
        for (long va : VAS) { decompOne(decomp, va); done.add(va); }
        for (long tgt : XREF_TARGETS) {
            Address taddr = toAddr(tgt);
            Function tf = getFunctionAt(taddr);
            if (tf == null) { println("### XREF 0x" + Long.toHexString(tgt) + " no function"); continue; }
            println("### CALLERS OF " + tf.getName() + " @ 0x" + Long.toHexString(tgt));
            Reference[] refs = getReferencesTo(taddr);
            int n = 0;
            for (Reference r : refs) {
                if (n >= 16) break;
                Function caller = getFunctionContaining(r.getFromAddress());
                if (caller == null) continue;
                println("  caller: " + caller.getName() + " @ " + caller.getEntryPoint());
                long cva = caller.getEntryPoint().getOffset();
                if (!done.contains(cva)) {
                    done.add(cva);
                    DecompileResults res = decomp.decompileFunction(caller, 240, monitor);
                    if (res.decompileCompleted()) println(res.getDecompiledFunction().getC());
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
