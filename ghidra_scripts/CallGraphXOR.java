import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.ReferenceManager;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashSet;
import java.util.Set;

public class CallGraphXOR extends GhidraScript {

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);

        long[] seeds = { 0x140755f10L, 0x140755d80L, 0x140755100L, 0x140754fe0L };
        Set<Function> seen = new HashSet<>();
        Deque<Object[]> queue = new ArrayDeque<>();
        for (long s : seeds) {
            Function f = fm.getFunctionAt(toAddr(s));
            if (f != null) queue.add(new Object[]{f, 0});
        }

        while (!queue.isEmpty()) {
            Object[] item = queue.poll();
            Function f = (Function) item[0];
            int depth = (Integer) item[1];
            if (!seen.add(f) || depth > 4 || f.isThunk()) continue;

            DecompileResults res = ifc.decompileFunction(f, 40, monitor);
            String c = res.decompileCompleted() ? res.getDecompiledFunction().getC() : "";
            boolean hasXor = c.contains("^=") || c.contains(" ^ ");
            println("F depth=" + depth + " " + f.getName() + " @ " + f.getEntryPoint() +
                    " size=" + f.getBody().getNumAddresses() + (hasXor ? "  *** XOR ***" : ""));
            if (hasXor) {
                String[] lines = c.split("\n");
                int max = Math.min(lines.length, 200);
                for (int i = 0; i < max; i++) println("  | " + lines[i]);
                if (lines.length > max) println("  | ... [" + (lines.length - max) + " more]");
            }

            if (depth < 4) {
                for (ReferenceIterator rit = rm.getReferencesTo(f.getEntryPoint()); rit.hasNext();) {
                    Reference r = rit.next();
                    if (!r.getReferenceType().isCall()) continue;
                    Function caller = fm.getFunctionContaining(r.getFromAddress());
                    if (caller != null && !seen.contains(caller)) {
                        queue.add(new Object[]{caller, depth + 1});
                    }
                }
            }
        }
        println("DONE");
    }
}
