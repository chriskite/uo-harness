// Find callers of the two resync wrapper thunks.
//@category UOHarness
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import java.util.HashSet;

public class DecompileMovement4 extends GhidraScript {
    private static final long[] XREF_TARGETS = {
        0x140036E60L, // resync wrapper 1
        0x1400FD960L, // resync wrapper 2
        0x140190500L, // OnPlayerTeleport
        0x140328180L, // helper called by OnPlayerTeleport + MobileUpdate
    };

    @Override
    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        HashSet<Long> done = new HashSet<Long>();
        for (long tgt : XREF_TARGETS) {
            Address taddr = toAddr(tgt);
            Function tf = getFunctionAt(taddr);
            if (tf == null) { println("### XREF 0x" + Long.toHexString(tgt) + " no function"); continue; }
            println("### CALLERS OF " + tf.getName() + " @ 0x" + Long.toHexString(tgt));
            Reference[] refs = getReferencesTo(taddr);
            int n = 0;
            for (Reference r : refs) {
                if (n >= 20) break;
                Function caller = getFunctionContaining(r.getFromAddress());
                if (caller == null) continue;
                println("  caller: " + caller.getName() + " @ " + caller.getEntryPoint()
                        + " from " + r.getFromAddress());
                long cva = caller.getEntryPoint().getOffset();
                if (!done.contains(cva) && caller.getBody().getNumAddresses() < 4000) {
                    done.add(cva);
                    DecompileResults res = decomp.decompileFunction(caller, 240, monitor);
                    if (res.decompileCompleted()) println(res.getDecompiledFunction().getC());
                }
                n++;
            }
        }
    }
}
