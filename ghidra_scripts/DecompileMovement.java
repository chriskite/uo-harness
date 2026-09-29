// Decompile specific functions by VA and print results.
//@category UOHarness
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompileMovement extends GhidraScript {
    private static final long[] VAS = {
        0x140327D40L, // PlayerMobile.Walk (rva 3310656)
        0x14030AEC0L, // WalkerManager.ConfirmWalk (rva 3190464)
        0x14030AE40L, // WalkerManager.DenyWalk (rva 3190336)
        0x14030AD40L, // FastWalkStack.AddValue (called from BF sub2)
        0x140311260L, // called from PacketHandlers.ConfirmWalk
        0x14020F640L, // GameActions.TriggerClientResync (rva 2153088 -> verify)
        0x1401A2BA0L, // send-wrapper used by Send_WalkRequest
    };

    @Override
    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        for (long va : VAS) {
            Address addr = toAddr(va);
            Function fn = getFunctionAt(addr);
            if (fn == null) fn = getFunctionContaining(addr);
            if (fn == null) {
                println("=== VA 0x" + Long.toHexString(va) + ": NO FUNCTION ===");
                continue;
            }
            println("=== VA 0x" + Long.toHexString(va) + " : " + fn.getName()
                    + " size=" + fn.getBody().getNumAddresses() + " ===");
            DecompileResults res = decomp.decompileFunction(fn, 180, monitor);
            if (res.decompileCompleted()) {
                println(res.getDecompiledFunction().getC());
            } else {
                println("DECOMPILE FAILED: " + res.getErrorMessage());
            }
        }
    }
}
