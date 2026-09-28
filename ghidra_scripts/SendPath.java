import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.ExternalManager;
import ghidra.program.model.symbol.ExternalLocation;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.ReferenceManager;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolTable;
import ghidra.program.model.symbol.SymbolIterator;

import java.util.HashSet;
import java.util.Set;

public class SendPath extends GhidraScript {

    static final String[] APIS = { "send", "WSASend", "recv", "WSARecv" };

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        SymbolTable st = currentProgram.getSymbolTable();

        Set<Function> targets = new HashSet<>();

        // find thunk functions named after our APIs (import jump stubs)
        for (Function f : fm.getFunctions(true)) {
            String name = f.getName();
            for (String api : APIS) {
                if (name.equals(api) && f.isThunk()) {
                    println("thunk: " + f.getName() + " @ " + f.getEntryPoint());
                    for (ReferenceIterator rit = rm.getReferencesTo(f.getEntryPoint()); rit.hasNext();) {
                        Reference r = rit.next();
                        Function caller = fm.getFunctionContaining(r.getFromAddress());
                        println("  ref from " + r.getFromAddress() + " in " +
                                (caller != null ? caller.getName() + " @ " + caller.getEntryPoint() : "no-func") +
                                " " + r.getReferenceType());
                        if (caller != null && r.getReferenceType().isCall()) {
                            targets.add(caller);
                        }
                    }
                }
            }
        }
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        for (Function f : targets) {
            println("\n########## " + f.getName() + " @ " + f.getEntryPoint() +
                    " size=" + f.getBody().getNumAddresses() + " ##########");
            DecompileResults res = ifc.decompileFunction(f, 60, monitor);
            if (res.decompileCompleted()) {
                String c = res.getDecompiledFunction().getC();
                String[] lines = c.split("\n");
                int max = Math.min(lines.length, 250);
                for (int i = 0; i < max; i++) println(lines[i]);
                if (lines.length > max) println("... [" + (lines.length - max) + " more lines]");
            } else {
                println("(decompile failed: " + res.getErrorMessage() + ")");
            }
        }
        println("DONE");
    }
}
