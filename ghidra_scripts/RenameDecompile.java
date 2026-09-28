import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.SourceType;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.PrintWriter;

public class RenameDecompile extends GhidraScript {

    static final String TSV = "C:\\Users\\chris\\uo-harness\\mrt_protocol_subset.tsv";
    static final String OUT = "C:\\Users\\chris\\uo-harness\\decompiled\\protocol_handlers.c";

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);

        PrintWriter out = new PrintWriter(new FileWriter(OUT));
        int renamed = 0, decompiled = 0, failed = 0;

        BufferedReader br = new BufferedReader(new FileReader(TSV));
        String line;
        while ((line = br.readLine()) != null) {
            String[] parts = line.split("\t");
            if (parts.length < 3) continue;
            String name = parts[0];
            Address codeVa = toAddr(Long.parseLong(parts[2], 16));

            Function f = fm.getFunctionAt(codeVa);
            if (f == null) {
                f = createFunction(codeVa, name);
            }
            if (f == null) {
                out.println("// ### NO FUNCTION at " + codeVa + " for " + name);
                failed++;
                continue;
            }
            try {
                f.setName(name, SourceType.ANALYSIS);
                renamed++;
            } catch (Exception e) {
                // name collision: append address
                try {
                    f.setName(name + "_" + codeVa.toString(), SourceType.ANALYSIS);
                    renamed++;
                } catch (Exception ignored) {}
            }

            out.println("\n// ============================================================");
            out.println("// " + name + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses());
            out.println("// ============================================================");
            DecompileResults res = ifc.decompileFunction(f, 90, monitor);
            if (res.decompileCompleted()) {
                out.println(res.getDecompiledFunction().getC());
                decompiled++;
            } else {
                out.println("// DECOMPILE FAILED: " + res.getErrorMessage());
                failed++;
            }
        }
        br.close();
        out.close();
        println("renamed=" + renamed + " decompiled=" + decompiled + " failed=" + failed);
        println("DONE");
    }
}
