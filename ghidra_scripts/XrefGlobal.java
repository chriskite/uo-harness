import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.ReferenceManager;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.HashSet;
import java.util.Set;

public class XrefGlobal extends GhidraScript {

    private String repoPath(String rel) {
        return new File(getSourceFile().getParentFile().getParentFile().getAbsolutePath(), rel).getPath();
    }

    static final long GLOBAL = 0x14118cf60L;

    @Override
    public void run() throws Exception {
        ReferenceManager rm = currentProgram.getReferenceManager();
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);

        Address g = toAddr(GLOBAL);
        Set<Function> funcs = new HashSet<>();
        for (ReferenceIterator rit = rm.getReferencesTo(g); rit.hasNext();) {
            Reference r = rit.next();
            Function f = fm.getFunctionContaining(r.getFromAddress());
            if (f != null) funcs.add(f);
        }
        println("referencing functions: " + funcs.size());

        PrintWriter out = new PrintWriter(new FileWriter(repoPath("decompiled/xref_add_fn.c")));
        for (Function f : funcs) {
            out.println("\n// ==== " + f.getName() + " @ " + f.getEntryPoint() +
                    " size=" + f.getBody().getNumAddresses() + " ====");
            DecompileResults res = ifc.decompileFunction(f, 90, monitor);
            if (res.decompileCompleted()) {
                out.println(res.getDecompiledFunction().getC());
            } else {
                out.println("// DECOMPILE FAILED");
            }
        }
        out.close();
        println("DONE");
    }
}
