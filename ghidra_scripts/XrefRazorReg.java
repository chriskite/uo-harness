import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.LinkedHashSet;
import java.util.Set;

public class XrefRazorReg extends GhidraScript {

    static final String OUT = "C:\\Users\\chris\\uo-harness\\decompiled\\razor_reg_callers.c";

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter(OUT));

        long[] targets = {0x14006c550L, 0x14006c7d0L, 0x14006c480L, 0x14006c700L}; // Register S2C viewer/filter, C2S viewer/filter
        Set<Function> fns = new LinkedHashSet<Function>();
        for (long t : targets) {
            Address a = toAddr(t);
            ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(a);
            while (ri.hasNext()) {
                Reference r = ri.next();
                Function caller = fm.getFunctionContaining(r.getFromAddress());
                println("xref " + Long.toHexString(t) + " from " + r.getFromAddress() + " in " + (caller == null ? "<data>" : caller.getName() + " @ " + caller.getEntryPoint()));
                if (caller != null) fns.add(caller);
            }
        }
        for (Function f : fns) {
            out.println("\n// ==== " + f.getName() + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses() + " ====");
            DecompileResults res = ifc.decompileFunction(f, 300, monitor);
            out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED " + res.getErrorMessage());
        }
        out.close();
        println("DONE functions=" + fns.size());
    }
}
