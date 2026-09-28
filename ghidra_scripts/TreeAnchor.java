import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.ReferenceManager;

import java.util.HashSet;
import java.util.Set;

public class TreeAnchor extends GhidraScript {

    @Override
    public void run() throws Exception {
        Memory mem = currentProgram.getMemory();
        FunctionManager fm = currentProgram.getFunctionManager();
        ReferenceManager rm = currentProgram.getReferenceManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);

        // Huffman _decTree starts with ints: 1,2, 3,4, 5,0, 6,7 (little-endian)
        byte[] needle = new byte[32];
        int[] vals = {1,2,3,4,5,0,6,7};
        for (int i = 0; i < 8; i++) {
            needle[i*4]   = (byte)(vals[i] & 0xFF);
            needle[i*4+1] = (byte)((vals[i] >> 8) & 0xFF);
            needle[i*4+2] = (byte)((vals[i] >> 16) & 0xFF);
            needle[i*4+3] = (byte)((vals[i] >> 24) & 0xFF);
        }
        Address a = mem.getMinAddress();
        Set<Function> hits = new HashSet<>();
        while (a != null) {
            a = mem.findBytes(a, needle, null, true, monitor);
            if (a == null) break;
            println("tree candidate @ " + a);
            for (ReferenceIterator rit = rm.getReferencesTo(a); rit.hasNext();) {
                Reference r = rit.next();
                Function f = fm.getFunctionContaining(r.getFromAddress());
                println("  xref from " + r.getFromAddress() + " in " +
                        (f != null ? f.getName() + " @ " + f.getEntryPoint() : "no-func") + " " + r.getReferenceType());
                if (f != null) hits.add(f);
            }
            // refs may point into the middle of the tree; also check refs to a+4..a+2048
            a = a.add(1);
        }

        // decompile hit functions and their callers (recv path) and callees
        Set<Function> related = new HashSet<>(hits);
        for (Function f : hits) {
            for (ReferenceIterator rit = rm.getReferencesTo(f.getEntryPoint()); rit.hasNext();) {
                Reference r = rit.next();
                if (!r.getReferenceType().isCall()) continue;
                Function caller = fm.getFunctionContaining(r.getFromAddress());
                if (caller != null) related.add(caller);
            }
        }
        println("=== decompiling " + related.size() + " related functions ===");
        for (Function f : related) {
            println("\n########## " + f.getName() + " @ " + f.getEntryPoint() +
                    " size=" + f.getBody().getNumAddresses() + " ##########");
            DecompileResults res = ifc.decompileFunction(f, 60, monitor);
            if (res.decompileCompleted()) {
                String c = res.getDecompiledFunction().getC();
                String[] lines = c.split("\n");
                int max = Math.min(lines.length, 200);
                for (int i = 0; i < max; i++) println(lines[i]);
                if (lines.length > max) println("... [" + (lines.length - max) + " more lines]");
            } else {
                println("(decompile failed)");
            }
        }
        println("DONE");
    }
}
