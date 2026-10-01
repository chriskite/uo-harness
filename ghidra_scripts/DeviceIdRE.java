import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.*;

import java.io.*;
import java.util.*;

// args: <commands.txt> <out.txt>
// command lines:
//   str <text>      UTF-16LE literal: find all occurrences, xrefs to obj start (-16..0), decompile referencing fns
//   astr <text>     ASCII literal: same
//   ext <name>      external/import symbol(s) containing name: xrefs (incl. thunks/IAT ptrs), decompile callers
//   fn <hex>        decompile function at/containing VA, list callers and callees
//   callers <hex>   list callers (+decompile) of function at VA
public class DeviceIdRE extends GhidraScript {
    FunctionManager fm;
    ReferenceManager rm;
    DecompInterface ifc;
    PrintWriter out;
    Set<Address> decompiled = new HashSet<>();

    void decomp(Function f, String why) {
        if (f == null || !decompiled.add(f.getEntryPoint())) {
            if (f != null) out.println("// (already decompiled " + f.getEntryPoint() + " for " + why + ")");
            return;
        }
        out.println("\n// ==== " + f.getName(true) + " @ " + f.getEntryPoint() + " size=" + f.getBody().getNumAddresses() + "  [" + why + "] ====");
        DecompileResults res = ifc.decompileFunction(f, 300, monitor);
        out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// FAILED " + res.getErrorMessage());
    }

    List<Function> refsTo(Address a, String label) {
        List<Function> fns = new ArrayList<>();
        for (ReferenceIterator it = rm.getReferencesTo(a); it.hasNext();) {
            Reference r = it.next();
            Function f = fm.getFunctionContaining(r.getFromAddress());
            out.println("   xref " + label + " " + a + " <- " + r.getFromAddress() + " " + r.getReferenceType() + " in " + (f == null ? "<nofunc>" : f.getName(true) + " @ " + f.getEntryPoint()));
            if (f != null && !fns.contains(f)) fns.add(f);
            if (f == null && r.getReferenceType().isData()) {
                // data pointer (e.g. IAT/indirection cell): follow one level
                for (ReferenceIterator it2 = rm.getReferencesTo(r.getFromAddress()); it2.hasNext();) {
                    Reference r2 = it2.next();
                    Function f2 = fm.getFunctionContaining(r2.getFromAddress());
                    out.println("      xref2 " + r.getFromAddress() + " <- " + r2.getFromAddress() + " " + r2.getReferenceType() + " in " + (f2 == null ? "<nofunc>" : f2.getName(true) + " @ " + f2.getEntryPoint()));
                    if (f2 != null && !fns.contains(f2)) fns.add(f2);
                }
            }
        }
        return fns;
    }

    void doString(String text, boolean utf16) throws Exception {
        byte[] b = utf16 ? text.getBytes("UTF-16LE") : text.getBytes("ASCII");
        Memory mem = currentProgram.getMemory();
        Address a = mem.getMinAddress();
        int guard = 0;
        while (a != null && guard++ < 32) {
            a = mem.findBytes(a, b, null, true, monitor);
            if (a == null) break;
            long off = -1;
            try { off = mem.getAddressSourceInfo(a).getFileOffset(); } catch (Exception e) {}
            out.println("== literal '" + text + "' @ " + a + " fileoff=0x" + Long.toHexString(off));
            List<Function> fns = new ArrayList<>();
            for (int d = -16; d <= 0; d += 4) {
                for (Function f : refsTo(a.add(d), "obj" + d)) if (!fns.contains(f)) fns.add(f);
            }
            for (Function f : fns) decomp(f, "refs literal " + text);
            a = a.add(1);
        }
    }

    void doExt(String name) {
        SymbolTable st = currentProgram.getSymbolTable();
        for (SymbolIterator it = st.getSymbolIterator(); it.hasNext();) {
            Symbol s = it.next();
            if (!s.getName().contains(name)) continue;
            out.println("== symbol " + s.getName(true) + " @ " + s.getAddress() + " ext=" + s.isExternal() + " type=" + s.getSymbolType());
            List<Function> fns = new ArrayList<>(refsTo(s.getAddress(), "sym"));
            if (s.isExternal()) {
                Function ef = fm.getFunctionAt(s.getAddress());
                if (ef != null) {
                    for (Address ta : ef.getFunctionThunkAddresses(true) == null ? new Address[0] : ef.getFunctionThunkAddresses(true)) {
                        out.println("   thunk " + ta);
                        fns.addAll(refsTo(ta, "thunk"));
                    }
                }
            }
            for (Function f : fns) {
                decomp(f, "uses " + s.getName());
            }
        }
    }

    void doFn(long va, boolean decompileCallers) {
        Address a = toAddr(va);
        Function f = fm.getFunctionAt(a);
        if (f == null) f = fm.getFunctionContaining(a);
        if (f == null) f = createFunction(a, "FUN_" + Long.toHexString(va));
        if (f == null) { out.println("// NO FN @ " + a); return; }
        out.println("== fn " + f.getName(true) + " @ " + f.getEntryPoint());
        List<Function> callers = refsTo(f.getEntryPoint(), "call");
        for (Function c : f.getCalledFunctions(monitor)) out.println("   callee " + c.getName(true) + " @ " + c.getEntryPoint());
        decomp(f, "fn");
        if (decompileCallers) for (Function c : callers) decomp(c, "caller of " + f.getEntryPoint());
    }
    void doRevTree(long va, int depth) {
        Address a = toAddr(va);
        Function root = fm.getFunctionContaining(a);
        if (root == null) { out.println("// NO FN @ " + a); return; }
        Set<Address> seen = new HashSet<>();
        List<Function> frontier = new ArrayList<>();
        frontier.add(root); seen.add(root.getEntryPoint());
        for (int d = 1; d <= depth && !frontier.isEmpty(); d++) {
            List<Function> next = new ArrayList<>();
            for (Function f : frontier) {
                for (ReferenceIterator it = rm.getReferencesTo(f.getEntryPoint()); it.hasNext();) {
                    Reference r = it.next();
                    Function c = fm.getFunctionContaining(r.getFromAddress());
                    String cs = c == null ? "<nofunc>" : c.getEntryPoint().toString();
                    out.println("   edge d=" + d + " " + f.getEntryPoint() + " <- " + cs + " via " + r.getFromAddress() + " " + r.getReferenceType());
                    if (c != null && seen.add(c.getEntryPoint())) next.add(c);
                }
            }
            frontier = next;
        }
    }


    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        fm = currentProgram.getFunctionManager();
        rm = currentProgram.getReferenceManager();
        ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        out = new PrintWriter(new FileWriter(args[1]));
        BufferedReader br = new BufferedReader(new FileReader(args[0]));
        String line;
        while ((line = br.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            int sp = line.indexOf(' ');
            String cmd = line.substring(0, sp), arg = line.substring(sp + 1);
            out.println("\n######## " + line);
            switch (cmd) {
                case "str": doString(arg, true); break;
                case "astr": doString(arg, false); break;
                case "ext": doExt(arg); break;
                case "fn": doFn(Long.parseLong(arg.replace("0x", ""), 16), false); break;
                case "callers": doFn(Long.parseLong(arg.replace("0x", ""), 16), true); break;
                case "refs": {
                    Address ra = toAddr(Long.parseLong(arg.replace("0x", ""), 16));
                    for (Function f : refsTo(ra, "data")) decomp(f, "refs " + ra);
                    break;
                }
                case "revtree": {
                    String[] p = arg.split(" ");
                    doRevTree(Long.parseLong(p[0].replace("0x", ""), 16), Integer.parseInt(p[1]));
                    break;
                }
            }
            out.flush();
        }
        br.close();
        out.close();
        println("DONE");
    }
}
