import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.LinkedHashSet;
import java.util.Set;

public class CctorSlotsAndResource extends GhidraScript {

    private String repoPath(String rel) {
        return new File(getSourceFile().getParentFile().getParentFile().getAbsolutePath(), rel).getPath();
    }

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter(repoPath("decompiled/cctor_slots.txt")));

        // 1. dump all instruction mnemonics in cctor that reference disp32 0x10/0x208/0x210/0x2a0 stores
        Function cctor = fm.getFunctionAt(toAddr(0x140184550L));
        AddressSetView body = cctor.getBody();
        InstructionIterator ii = currentProgram.getListing().getInstructions(body, true);
        int storeCount = 0;
        while (ii.hasNext()) {
            Instruction ins = ii.next();
            String s = ins.toString();
            if (s.contains("0x208") || s.contains("0x210") || s.contains("0x2a0") ||
                (s.contains("[R") && s.contains("0x10]"))) {
                out.println(ins.getAddress() + ": " + s);
            }
            if (s.startsWith("CALL") && s.contains("1416a80d0")) storeCount++;
        }
        out.println("// write-barrier calls in cctor: " + storeCount);

        // 2. xrefs to FUN_14118d1a0 (resource tier-dict loader)
        Address rd = toAddr(0x14118d1a0L);
        ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(rd);
        Set<Function> callers = new LinkedHashSet<Function>();
        while (ri.hasNext()) {
            Reference r = ri.next();
            Function caller = fm.getFunctionContaining(r.getFromAddress());
            println("xref 14118d1a0 from " + r.getFromAddress() + " in " + (caller == null ? "<data>" : caller.getName() + " @ " + caller.getEntryPoint()));
            if (caller != null) callers.add(caller);
        }
        for (Function f : callers) {
            out.println("\n// ==== caller of tier-dict loader " + f.getName() + " @ " + f.getEntryPoint() + " ====");
            DecompileResults res = ifc.decompileFunction(f, 120, monitor);
            out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED");
        }
        out.close();
        println("DONE");
    }
}
