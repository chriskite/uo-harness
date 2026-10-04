import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.HashSet;
import java.util.Set;

public class FindAddCallers extends GhidraScript {

    private String repoPath(String rel) {
        return new File(getSourceFile().getParentFile().getParentFile().getAbsolutePath(), rel).getPath();
    }

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        Address addAddr = toAddr(0x140184470L);
        Function addFn = fm.getFunctionAt(addAddr);
        println("Add function: " + addFn);

        Set<Function> callers = new HashSet<Function>();
        ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(addAddr);
        while (ri.hasNext()) {
            Reference r = ri.next();
            Address from = r.getFromAddress();
            Function caller = fm.getFunctionContaining(from);
            println("xref from " + from + " in " + (caller == null ? "<none>" : caller.getName() + " @ " + caller.getEntryPoint()));
            if (caller != null) callers.add(caller);
        }

        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter(repoPath("decompiled/add_callers.c")));
        for (Function f : callers) {
            out.println("\n// ============================================================");
            out.println("// CALLER " + f.getName() + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses());
            out.println("// ============================================================");
            DecompileResults res = ifc.decompileFunction(f, 120, monitor);
            if (res.decompileCompleted()) {
                out.println(res.getDecompiledFunction().getC());
            } else {
                out.println("// DECOMPILE FAILED: " + res.getErrorMessage());
            }
        }
        out.close();
        println("DONE callers=" + callers.size());
    }
}
