import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;

import java.util.LinkedHashSet;
import java.util.Set;

public class XrefDelegateType extends GhidraScript {
    @Override
    public void run() throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        Address ee = toAddr(0x1432633f0L);
        Set<String> fns = new LinkedHashSet<String>();
        ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(ee);
        while (ri.hasNext()) {
            Reference r = ri.next();
            Function caller = fm.getFunctionContaining(r.getFromAddress());
            fns.add(caller == null ? "<data " + r.getFromAddress() + ">"
                                   : caller.getName(true) + " @ " + caller.getEntryPoint());
        }
        for (String s : fns) println(s);
        println("DONE sites=" + fns.size());
    }
}
