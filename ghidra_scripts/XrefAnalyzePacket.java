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

public class XrefAnalyzePacket extends GhidraScript {

    static final String OUT = "C:\\Users\\chris\\uo-harness\\decompiled\\analyze_packet_callers.c";

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter(OUT));

        Address ap = toAddr(0x1401844d0L);
        Set<Function> callers = new LinkedHashSet<Function>();
        ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(ap);
        while (ri.hasNext()) {
            Reference r = ri.next();
            Function caller = fm.getFunctionContaining(r.getFromAddress());
            println("xref AnalyzePacket from " + r.getFromAddress() + " in " + (caller == null ? "<data/none>" : caller.getName() + " @ " + caller.getEntryPoint()));
            if (caller != null) callers.add(caller);
        }
        for (Function f : callers) {
            out.println("\n// ==== CALLER " + f.getName() + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses() + " ====");
            DecompileResults res = ifc.decompileFunction(f, 120, monitor);
            out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED " + res.getErrorMessage());
        }
        out.close();
        println("DONE callers=" + callers.size());
    }
}
