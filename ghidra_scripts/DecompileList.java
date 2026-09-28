import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;

import java.io.FileWriter;
import java.io.PrintWriter;
import java.io.BufferedReader;
import java.io.FileReader;

public class DecompileList extends GhidraScript {

    // arg0: input TSV file with "name <tab> vahex" per line; arg1: output file
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String inPath = args[0];
        String outPath = args[1];
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        PrintWriter out = new PrintWriter(new FileWriter(outPath));
        BufferedReader br = new BufferedReader(new FileReader(inPath));
        String line;
        while ((line = br.readLine()) != null) {
            String[] parts = line.split("\t");
            if (parts.length < 2) continue;
            String name = parts[0];
            long va = Long.parseLong(parts[1], 16);
            Address a = toAddr(va);
            Function f = fm.getFunctionAt(a);
            if (f == null) f = fm.getFunctionContaining(a);
            if (f == null) f = createFunction(a, name.replaceAll("[^A-Za-z0-9_]", "_"));
            if (f == null) { out.println("// NO FN " + name + " @ " + a); continue; }
            out.println("\n// ============================================================");
            out.println("// " + name + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses());
            out.println("// ============================================================");
            DecompileResults res = ifc.decompileFunction(f, 180, monitor);
            out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED " + res.getErrorMessage());
        }
        br.close();
        out.close();
        println("DONE");
    }
}
