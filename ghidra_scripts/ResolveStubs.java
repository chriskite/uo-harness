import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Reference;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.PrintWriter;

public class ResolveStubs extends GhidraScript {

    private String repoPath(String rel) {
        return new File(getSourceFile().getParentFile().getParentFile().getAbsolutePath(), rel).getPath();
    }

    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        PrintWriter out = new PrintWriter(new FileWriter(repoPath("stub_targets.txt")));
        BufferedReader br = new BufferedReader(new FileReader(repoPath("stubs.txt")));
        String line;
        while ((line = br.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) continue;
            long va = Long.parseLong(line, 16);
            Address a = toAddr(va);
            Function f = fm.getFunctionAt(a);
            if (f == null) {
                out.println(line + " NOFUNC");
                continue;
            }
            StringBuilder targets = new StringBuilder();
            InstructionIterator ii = currentProgram.getListing().getInstructions(f.getBody(), true);
            while (ii.hasNext()) {
                Instruction ins = ii.next();
                for (Reference r : ins.getReferencesFrom()) {
                    Address to = r.getToAddress();
                    Function tf = fm.getFunctionAt(to);
                    if (tf != null) {
                        targets.append(" ").append(tf.getName()).append("@").append(to);
                    } else {
                        targets.append(" ref:").append(to);
                    }
                }
            }
            out.println(line + " ->" + targets);
        }
        br.close();
        out.close();
        println("DONE");
    }
}
