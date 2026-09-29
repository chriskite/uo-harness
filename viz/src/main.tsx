import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
import { connect } from "./api.ts";
import { vizStore } from "./store.ts";
import "./App.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");
connect(vizStore);
createRoot(root).render(<App />);
