import { createRoot } from "react-dom/client";
import { App } from "./App.tsx";
import { connect } from "./api.ts";
import { subscribeChar } from "./character.ts";
import { vizStore } from "./store.ts";
import "./App.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");
let stop = connect(vizStore);
// Another character: drop the old feed's events and state, follow the new one's.
subscribeChar(() => {
  stop();
  vizStore.reset();
  stop = connect(vizStore);
});
createRoot(root).render(<App />);
