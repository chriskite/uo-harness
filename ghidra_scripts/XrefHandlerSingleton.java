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

public class XrefHandlerSingleton extends GhidraScript {

    static final String OUT = "C:\\Users\\chris\\uo-harness\\decompiled\\handler_singleton_xrefs.c";

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter(OUT));

        // 1. decompile get_Handlers to find the singleton global
        Address gh = toAddr(0x140184440L);
        Function ghFn = fm.getFunctionAt(gh);
        out.println("// ==== get_Handlers ====");
        DecompileResults res = ifc.decompileFunction(ghFn, 60, monitor);
        out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED " + res.getErrorMessage());

        // 2. xrefs to get_Handlers (callers)
        Set<Function> callers = new LinkedHashSet<Function>();
        ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(gh);
        while (ri.hasNext()) {
            Reference r = ri.next();
            Function caller = fm.getFunctionContaining(r.getFromAddress());
            println("xref get_Handlers from " + r.getFromAddress() + " in " + (caller == null ? "<data/none>" : caller.getName() + " @ " + caller.getEntryPoint()));
            if (caller != null) callers.add(caller);
        }
        for (Function f : callers) {
            out.println("\n// ==== CALLER " + f.getName() + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses() + " ====");
            res = ifc.decompileFunction(f, 120, monitor);
            out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED " + res.getErrorMessage());
        }
        out.close();
        println("DONE callers=" + callers.size());
    }
}
