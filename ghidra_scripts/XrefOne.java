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

public class XrefOne extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        long va = Long.parseLong(args[0], 16);
        String outPath = args[1];
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter(outPath));
        Address a = toAddr(va);
        Set<Function> fns = new LinkedHashSet<Function>();
        ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(a);
        while (ri.hasNext()) {
            Reference r = ri.next();
            Function caller = fm.getFunctionContaining(r.getFromAddress());
            println("xref from " + r.getFromAddress() + " in " + (caller == null ? "<data>" : caller.getName(true) + " @ " + caller.getEntryPoint()));
            if (caller != null) fns.add(caller);
        }
        for (Function f : fns) {
            out.println("\n// ==== " + f.getName(true) + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses() + " ====");
            DecompileResults res = ifc.decompileFunction(f, 300, monitor);
            out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED " + res.getErrorMessage());
        }
        out.close();
        println("DONE functions=" + fns.size());
    }
}
