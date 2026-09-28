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

public class XrefStaticsBase extends GhidraScript {

    static final String OUT = "C:\\Users\\chris\\uo-harness\\decompiled\\statics_base_xrefs.c";

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter(OUT));

        Address fld = toAddr(0x143f8bf98L);
        Set<Function> fns = new LinkedHashSet<Function>();
        ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(fld);
        while (ri.hasNext()) {
            Reference r = ri.next();
            Function caller = fm.getFunctionContaining(r.getFromAddress());
            println("xref field from " + r.getFromAddress() + " type=" + r.getReferenceType() + " in " + (caller == null ? "<data/none>" : caller.getName() + " @ " + caller.getEntryPoint()));
            if (caller != null) fns.add(caller);
        }
        for (Function f : fns) {
            out.println("\n// ==== " + f.getName() + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses() + " ====");
            DecompileResults res = ifc.decompileFunction(f, 180, monitor);
            out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED " + res.getErrorMessage());
        }
        out.close();
        println("DONE functions=" + fns.size());
    }
}
