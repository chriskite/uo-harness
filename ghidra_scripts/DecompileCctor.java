import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;

public class DecompileCctor extends GhidraScript {

    private String repoPath(String rel) {
        return new File(getSourceFile().getParentFile().getParentFile().getAbsolutePath(), rel).getPath();
    }

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter(repoPath("decompiled/packet_handlers_cctor.c")));

        // find global named PTR_FUN_143f7b310
        Address ptrAddr = null;
        SymbolIterator si = currentProgram.getSymbolTable().getAllSymbols(true);
        while (si.hasNext()) {
            Symbol s = si.next();
            if (s.getName().equals("PTR_FUN_143f7b310")) {
                ptrAddr = s.getAddress();
                break;
            }
        }
        if (ptrAddr == null) { println("PTR_FUN_143f7b310 not found"); return; }
        println("PTR_FUN_143f7b310 @ " + ptrAddr);
        long cctorVa = getLong(ptrAddr);
        println("cctor VA = 0x" + Long.toHexString(cctorVa));
        Address cctorAddr = toAddr(cctorVa);
        Function f = fm.getFunctionAt(cctorAddr);
        if (f == null) f = createFunction(cctorAddr, "PacketHandlers_cctor");
        if (f == null) { println("no function at cctor"); return; }
        out.println("// ==== PacketHandlers cctor " + f.getName() + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses() + " ====");
        DecompileResults res = ifc.decompileFunction(f, 300, monitor);
        out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED " + res.getErrorMessage());
        out.close();
        println("DONE");
    }
}
